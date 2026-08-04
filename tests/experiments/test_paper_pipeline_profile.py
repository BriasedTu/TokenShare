from __future__ import annotations

import copy
import dataclasses
from hashlib import sha256
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import tokenshare.experiments.paper_pipeline_profile as paper_pipeline_profile
from tokenshare.experiments.paper_pipeline_profile import (
    DEFAULT_EPD027_PIPELINE_PROFILE_PATH,
    PROMPT_ADMISSION_ALGORITHM,
    PROMPT_ADMISSION_PROFILE_DIGEST,
    PROMPT_ADMISSION_PROFILE_ID,
    PROMPT_ADMISSION_PROFILE_VERSION,
    compute_budget_digest,
    compute_profile_digest,
    load_paper_pipeline_profile,
)
from tokenshare.experiments.paper_suite_scale import load_paper_suite_scale_profile


EXPECTED_EXP2_CASES = (
    ("early", "factor_v2_hard_034"),
    ("middle", "factor_v2_hard_122"),
    ("late", "factor_v2_hard_063"),
    ("no_factor", "factor_v2_hard_161"),
)


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()
EXPECTED_WORKERS = (1, 3, 7, 10, 30, 50)

ADMISSION_LEAF_MUTATIONS = (
    (("profile_id",), "drifted"),
    (("version",), 2),
    (("algorithm",), PROMPT_ADMISSION_ALGORITHM + "+1"),
    (("canonical_body_schema",), "drifted"),
    (("includes", 0), "drifted-system"),
    (("includes", 1), "drifted-messages"),
    (("includes", 2), "drifted-overhead"),
    (("includes", 3), "drifted-unicode"),
    (("max_prompt_tokens",), 32_767),
    (("profile_digest",), "sha256:" + "0" * 64),
)

PAID_AND_METRIC_LEAF_MUTATIONS = (
    (("paid_authorization", "allow_provider_calls_flag"), False),
    (("paid_authorization", "required_receipt_schema"), "drifted"),
    (("paid_authorization", "offline_approval_authorizes_provider_write"), True),
    (("paid_authorization", "provider_scopes", 0), "drifted-l3"),
    (("paid_authorization", "provider_scopes", 1), "drifted-bank"),
    (("paid_authorization", "provider_scopes", 2), "drifted-exp1"),
    (("paid_authorization", "provider_scopes", 3), "drifted-exp5-smoke"),
    (("paid_authorization", "provider_scopes", 4), "drifted-exp5-full"),
    (("paid_authorization", "output_mode_values", 0), "drifted-new"),
    (("paid_authorization", "output_mode_values", 1), "drifted-resume"),
    (("metric_controls", "exp2_429_or_timeout_union_fraction_max"), "0.3"),
    (("metric_controls", "exp2_union_denominator"), "all_attempts"),
    (("metric_controls", "exp2_union_identity"), "request_identity"),
    (("metric_controls", "exp2_online_trace_same_case_intersection_min"), 4),
    (("metric_controls", "severe_speedup_ratio_min"), "0.4"),
    (("metric_controls", "severe_speedup_ratio_max"), "2.1"),
    (("metric_controls", "severe_opposed_trend_high"), "1.2"),
    (("metric_controls", "severe_opposed_trend_low"), "0.8"),
)

CAPABILITY_CONTROL_LEAF_MUTATIONS = (
    (("classification",), "drifted"),
    (("paper_eligible",), True),
    (("factor", "case_id"), "drifted-factor"),
    (("factor", "split_policy"), "drifted-split"),
    (("factor", "stable_planned_ai_unit_id"), "range_1"),
    (("factor", "planned_first_ai_units"), 2),
    (("factor", "initial_control"), "accept_first_attempt"),
    (("factor", "initial_attempt_must_reject"), False),
    (("factor", "max_retries"), 2),
    (("factor", "replacement_calls_exact"), 2),
    (("factor", "attempts_after_replacement_exact"), 1),
    (("lean", "case_id"), "drifted-lean"),
    (("lean", "selected_proof_node_id"), "pure_simple_root_01"),
    (("lean", "stable_planned_ai_unit_id"), "pure_simple_root_01"),
    (("lean", "planned_first_ai_units"), 2),
    (("lean", "initial_control"), "accept_first_attempt"),
    (("lean", "initial_attempt_must_reject"), False),
    (("lean", "max_retries"), 2),
    (("lean", "replacement_calls_exact"), 2),
    (("lean", "attempts_after_replacement_exact"), 1),
    (("root_count",), 3),
    (("provider_calls_exact",), 5),
)

CLOSED_SCHEMA_PATHS = (
    (),
    ("authorities",),
    ("prompt_admission",),
    ("online_checks",),
    ("online_checks", "exp2"),
    ("online_checks", "exp2", "conditions", 0),
    ("online_checks", "exp3"),
    ("online_checks", "exp3", "cases", 0),
    ("online_checks", "capability"),
    ("online_checks", "capability", "factor"),
    ("online_checks", "capability", "lean"),
    ("metric_controls",),
    ("paid_authorization",),
    ("budget",),
    ("offline_approval",),
)

STRICT_INTEGER_PATHS = (
    ("profile_version",),
    ("prompt_admission", "version"),
    ("prompt_admission", "max_prompt_tokens"),
    ("online_checks", "max_concurrent_roots"),
    ("online_checks", "exp2", "workers", 0),
    ("online_checks", "exp2", "repeat_id"),
    ("online_checks", "exp2", "planned_first_ai_units_per_root"),
    ("online_checks", "exp2", "provider_calls_upper"),
    ("online_checks", "exp2", "conditions", 0, "worker_count"),
    ("online_checks", "exp2", "conditions", 0, "active_selection_index"),
    ("online_checks", "exp2", "conditions", 0, "repeat_id"),
    ("online_checks", "exp2", "conditions", 0, "planned_first_ai_units"),
    ("online_checks", "exp2", "conditions", 0, "provider_calls_upper"),
    ("online_checks", "exp2", "conditions", 0, "max_concurrent_roots"),
    ("online_checks", "exp3", "provider_calls_upper"),
    ("online_checks", "exp3", "cases", 0, "fault_rate_percent"),
    ("online_checks", "exp3", "cases", 0, "worker_count"),
    ("online_checks", "exp3", "cases", 0, "repeat_id"),
    ("online_checks", "exp3", "cases", 0, "max_retries"),
    ("online_checks", "exp3", "cases", 0, "planned_first_ai_units"),
    ("online_checks", "exp3", "cases", 0, "provider_calls_upper"),
    ("online_checks", "exp3", "cases", 1, "kill_progress_percent"),
    ("online_checks", "exp3", "cases", 1, "dead_worker_count"),
    ("online_checks", "capability", "factor", "planned_first_ai_units"),
    ("online_checks", "capability", "factor", "max_retries"),
    ("online_checks", "capability", "factor", "replacement_calls_exact"),
    (
        "online_checks",
        "capability",
        "factor",
        "attempts_after_replacement_exact",
    ),
    ("online_checks", "capability", "lean", "planned_first_ai_units"),
    ("online_checks", "capability", "lean", "max_retries"),
    ("online_checks", "capability", "lean", "replacement_calls_exact"),
    (
        "online_checks",
        "capability",
        "lean",
        "attempts_after_replacement_exact",
    ),
    ("online_checks", "capability", "root_count"),
    ("online_checks", "capability", "provider_calls_exact"),
    ("metric_controls", "exp2_online_trace_same_case_intersection_min"),
    ("budget", "capability_calls_exact"),
    ("budget", "exp2_calls_upper"),
    ("budget", "exp3_calls_upper"),
    ("budget", "online_checks_calls_upper"),
    ("budget", "planned_calls_upper"),
    ("budget", "ambiguous_reserve_calls"),
    ("budget", "calls_hard_limit"),
    ("budget", "prompt_tokens_per_call"),
    ("budget", "completion_tokens_per_call"),
    ("budget", "tokens_per_call"),
    ("budget", "tokens_hard_limit"),
    ("budget", "max_in_flight_acquisition_capability"),
    ("budget", "max_in_flight_exp2_online"),
)


def _profile_body() -> dict[str, object]:
    return json.loads(
        DEFAULT_EPD027_PIPELINE_PROFILE_PATH.read_text(encoding="utf-8")
    )


def _set_leaf(body: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    target: Any = body
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def _value_at(body: Any, path: tuple[str | int, ...]) -> Any:
    target = body
    for part in path:
        target = target[part]
    return target


def _reseal_profile(body: dict[str, Any]) -> None:
    body["budget"]["budget_digest"] = compute_budget_digest(body)
    profile_digest = compute_profile_digest(body)
    body["profile_digest"] = profile_digest
    body["offline_approval"]["approved_profile_digest"] = profile_digest


def _write_profile(path: Path, body: dict[str, Any]) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _assert_deeply_immutable(value: Any) -> None:
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            _assert_deeply_immutable(getattr(value, field.name))
        return
    if isinstance(value, tuple):
        for item in value:
            _assert_deeply_immutable(item)
        return
    assert not isinstance(value, (dict, list, set))


def test_profile_freezes_four_plus_492_and_516_hard_calls() -> None:
    profile = load_paper_pipeline_profile()

    assert profile.capability_calls_exact == 4
    assert profile.exp2_calls_upper == 480
    assert profile.exp3_calls_upper == 12
    assert profile.online_checks_calls_upper == 492
    assert profile.planned_calls_upper == 496
    assert profile.ambiguous_reserve_calls == 20
    assert profile.calls_hard_limit == 516
    assert profile.tokens_per_call == 332_768
    assert profile.tokens_hard_limit == 171_708_288
    assert str(profile.cny_per_call_reservation) == "1.898304"
    assert str(profile.cny_reservation_hard_limit) == "979.524864"
    assert str(profile.cny_absolute_hard_stop) == "1000.0"


def test_profile_freezes_logical_source_latency_1x() -> None:
    profile = load_paper_pipeline_profile()

    assert profile.trace_delay_policy == "logical_source_latency_1x"


def test_profile_freezes_exact_24_condition_sequence_and_max_concurrent_roots_one() -> None:
    profile = load_paper_pipeline_profile()

    expected = tuple(
        (worker, case_position, case_id)
        for worker in EXPECTED_WORKERS
        for case_position, case_id in EXPECTED_EXP2_CASES
    )
    actual = tuple(
        (condition.worker_count, condition.case_position, condition.case_id)
        for condition in profile.exp2_conditions
    )
    assert actual == expected
    assert len(profile.exp2_conditions) == 24
    assert profile.max_concurrent_roots == 1
    assert all(condition.repeat_id == 0 for condition in profile.exp2_conditions)
    assert len({condition.condition_digest for condition in profile.exp2_conditions}) == 24


def test_profile_freezes_exact_exp3_cases() -> None:
    profile = load_paper_pipeline_profile()

    assert tuple(case.to_dict() for case in profile.exp3_cases) == (
        {
            "case_id": "factor_v2_easy_109",
            "check_kind": "false_positive",
            "fault_rate_percent": 100,
            "worker_count": 10,
            "repeat_id": 0,
            "max_retries": 2,
            "planned_first_ai_units": 2,
            "provider_calls_upper": 6,
        },
        {
            "case_id": "factor_v2_easy_148",
            "check_kind": "worker_death",
            "kill_progress_percent": 50,
            "dead_worker_count": 1,
            "worker_count": 10,
            "repeat_id": 0,
            "max_retries": 2,
            "planned_first_ai_units": 2,
            "provider_calls_upper": 6,
        },
    )
    assert profile.capability_factor_case_id == "factor_v2_easy_109"
    assert (
        profile.capability_lean_case_id
        == "lean_v2_simple_pure_logic_direct_prop_01"
    )


def test_profile_freezes_capability_one_plus_one_plus_one_plus_one() -> None:
    profile = load_paper_pipeline_profile()
    capability = _profile_body()["online_checks"]["capability"]

    assert capability == {
        "classification": "new_real_capability_smoke",
        "paper_eligible": False,
        "factor": {
            "case_id": "factor_v2_easy_109",
            "split_policy": "deterministic_one_range_capability_split",
            "stable_planned_ai_unit_id": "range_0",
            "planned_first_ai_units": 1,
            "initial_control": "forced_verification_rejection",
            "initial_attempt_must_reject": True,
            "max_retries": 1,
            "replacement_calls_exact": 1,
            "attempts_after_replacement_exact": 0,
        },
        "lean": {
            "case_id": "lean_v2_simple_pure_logic_direct_prop_01",
            "selected_proof_node_id": "pure_simple_leaf_01",
            "stable_planned_ai_unit_id": "pure_simple_leaf_01",
            "planned_first_ai_units": 1,
            "initial_control": "controlled_checker_rejection",
            "initial_attempt_must_reject": True,
            "max_retries": 1,
            "replacement_calls_exact": 1,
            "attempts_after_replacement_exact": 0,
        },
        "root_count": 2,
        "provider_calls_exact": 4,
    }
    assert profile.capability_factor_planned_first_ai_units == 1
    assert profile.capability_factor_max_retries == 1
    assert profile.capability_factor_max_attempts == 2
    assert profile.capability_factor_replacement_calls_exact == 1
    assert profile.capability_lean_selected_proof_node_id == "pure_simple_leaf_01"
    assert profile.capability_lean_stable_planned_ai_unit_id == "pure_simple_leaf_01"
    assert profile.capability_lean_planned_first_ai_units == 1
    assert profile.capability_lean_max_retries == 1
    assert profile.capability_lean_max_attempts == 2
    assert profile.capability_lean_replacement_calls_exact == 1
    assert (
        profile.capability.root_count
        * profile.capability.selected_ai_units_per_root
        * profile.capability.max_attempts_per_selected_unit
        == profile.capability_calls_exact
        == 4
    )
    assert profile.capability.factor.initial_attempt_must_reject is True
    assert profile.capability.lean.initial_attempt_must_reject is True
    assert profile.capability.factor.attempts_after_replacement_exact == 0
    assert profile.capability.lean.attempts_after_replacement_exact == 0


def test_loader_exposes_complete_frozen_typed_authority() -> None:
    profile = load_paper_pipeline_profile()
    repository_root = DEFAULT_EPD027_PIPELINE_PROFILE_PATH.parents[2]

    assert profile.schema_version == "tokenshare.epd027_pipeline_profile.v1"
    assert profile.profile_id == "epd027_response_bank_paper_pipeline.v1"
    assert profile.profile_version == 1
    tracked_body = _profile_body()
    assert profile.profile_digest == tracked_body["profile_digest"]
    assert profile.profile_digest == compute_profile_digest(tracked_body)
    assert profile.authorities.implementation_plan_path == repository_root / (
        "Doc/archive/design-history/"
        "2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md"
    )
    assert profile.authorities.implementation_plan_content_digest == _file_digest(
        profile.authorities.implementation_plan_path
    )
    assert profile.authorities.factorization_catalog_path == (
        repository_root / "benchmarks/paper/factorization_catalog.v2.jsonl"
    )
    assert profile.authorities.factorization_catalog_content_digest == _file_digest(
        profile.authorities.factorization_catalog_path
    )
    assert profile.authorities.paper_suite_scale_profile_path == (
        repository_root / "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    assert profile.authorities.paper_suite_scale_profile_content_digest == _file_digest(
        profile.authorities.paper_suite_scale_profile_path
    )
    assert profile.authorities.paper_suite_scale_profile_digest == (
        load_paper_suite_scale_profile(
            profile.authorities.paper_suite_scale_profile_path
        ).profile_digest
    )
    assert profile.authorities.lean_catalog_path == (
        repository_root / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
    )
    assert profile.authorities.lean_catalog_content_digest == _file_digest(
        profile.authorities.lean_catalog_path
    )
    assert profile.authorities.lean_readiness_path == (
        repository_root / "benchmarks/paper/lean_task14_3x3_readiness.v1.json"
    )
    assert profile.authorities.lean_readiness_content_digest == _file_digest(
        profile.authorities.lean_readiness_path
    )
    assert profile.authorities.provider_config_path == (
        repository_root / "benchmarks/paper/exp1_baseline_provider_config.v3.json"
    )
    assert profile.authorities.provider_config_content_digest == _file_digest(
        profile.authorities.provider_config_path
    )

    assert profile.prompt_admission.canonical_body_schema == "canonical_json_utf8_v1"
    assert profile.prompt_admission.includes == (
        "system",
        "messages",
        "canonical_body_overhead",
        "unicode_utf8_byte_upper",
    )
    assert profile.exp2.workers == EXPECTED_WORKERS
    assert profile.exp2.repeat_id == 0
    assert profile.exp2.planned_first_ai_units_per_root == 20
    assert profile.exp2.provider_calls_upper == 480
    first = profile.exp2.conditions[0]
    assert (
        first.condition_id,
        first.experiment_id,
        first.evidence_class,
        first.worker_count,
        first.case_position,
        first.case_id,
        first.paper_difficulty,
        first.active_selection_index,
        first.repeat_id,
        first.split_profile_id,
        first.planned_first_ai_units,
        first.provider_calls_upper,
        first.max_concurrent_roots,
    ) == (
        "epd027_exp2_online_w1_early_r0",
        "exp2_online_concurrency_check",
        "online_real_provider",
        1,
        "early",
        "factor_v2_hard_034",
        "hard",
        0,
        0,
        "factorization.exp2_contiguous_20way.v1",
        20,
        20,
        1,
    )
    assert profile.capability.classification == "new_real_capability_smoke"
    assert profile.capability.paper_eligible is False
    assert profile.capability.factor.initial_control == (
        "forced_verification_rejection"
    )
    assert profile.capability.lean.initial_control == "controlled_checker_rejection"

    metrics = profile.metric_controls
    assert str(metrics.exp2_429_or_timeout_union_fraction_max) == "0.2"
    assert metrics.exp2_union_denominator == (
        "actual_first_provider_attempts_by_worker"
    )
    assert metrics.exp2_union_identity == "attempt_identity"
    assert metrics.exp2_online_trace_same_case_intersection_min == 3
    assert str(metrics.severe_speedup_ratio_min) == "0.5"
    assert str(metrics.severe_speedup_ratio_max) == "2.0"
    assert str(metrics.severe_opposed_trend_high) == "1.1"
    assert str(metrics.severe_opposed_trend_low) == "0.9"

    paid = profile.paid_authorization
    assert paid.allow_provider_calls_flag is True
    assert paid.required_receipt_schema == "tokenshare.paid_execution_receipt.v1"
    assert paid.offline_approval_authorizes_provider_write is False
    assert paid.provider_scopes == (
        "epd027_l3_capability_and_online_checks",
        "epd027_full_bank_acquisition",
        "exp1_full_online",
        "exp5_capability_smoke",
        "exp5_full_online",
    )
    assert paid.output_mode_values == ("new-run", "resume")

    budget = profile.budget
    assert budget.prompt_tokens_per_call == 32_768
    assert budget.completion_tokens_per_call == 300_000
    assert budget.max_in_flight_acquisition_capability == 10
    assert budget.max_in_flight_exp2_online == 50
    assert budget.unlimited_budget_provider_writes_allowed is False
    _assert_deeply_immutable(profile)


def test_exp3_cases_are_scalar_frozen_records_and_to_dict_is_detached() -> None:
    profile = load_paper_pipeline_profile()
    false_positive, worker_death = profile.exp3.cases

    assert false_positive.fault_rate_percent == 100
    assert false_positive.kill_progress_percent is None
    assert false_positive.dead_worker_count is None
    assert worker_death.fault_rate_percent is None
    assert worker_death.kill_progress_percent == 50
    assert worker_death.dead_worker_count == 1
    assert false_positive.worker_count == worker_death.worker_count == 10
    assert false_positive.repeat_id == worker_death.repeat_id == 0
    assert false_positive.max_retries == worker_death.max_retries == 2
    assert false_positive.planned_first_ai_units == 2
    assert worker_death.planned_first_ai_units == 2
    assert false_positive.provider_calls_upper == worker_death.provider_calls_upper == 6
    assert profile.exp3.provider_calls_upper == 12

    detached = false_positive.to_dict()
    detached["worker_count"] = 999
    assert false_positive.worker_count == 10
    assert false_positive.to_dict()["worker_count"] == 10
    with pytest.raises(dataclasses.FrozenInstanceError):
        false_positive.worker_count = 999  # type: ignore[misc]


def test_prompt_admission_profile_id_version_algorithm_and_digest_are_exact() -> None:
    profile = load_paper_pipeline_profile()

    assert profile.prompt_admission_profile_id == PROMPT_ADMISSION_PROFILE_ID
    assert profile.prompt_admission_profile_version == PROMPT_ADMISSION_PROFILE_VERSION
    assert profile.prompt_admission_algorithm == PROMPT_ADMISSION_ALGORITHM
    assert profile.prompt_admission_profile_digest == PROMPT_ADMISSION_PROFILE_DIGEST
    assert profile.prompt_admission_max_tokens == 32_768
    assert profile.estimate_prompt_tokens("甲".encode("utf-8"), message_count=1) == 27


@pytest.mark.parametrize(("leaf_path", "replacement"), ADMISSION_LEAF_MUTATIONS)
def test_admission_algorithm_drift_changes_profile_and_budget_digests(
    leaf_path: tuple[str | int, ...], replacement: Any
) -> None:
    body = _profile_body()
    drifted = copy.deepcopy(body)
    _set_leaf(drifted["prompt_admission"], leaf_path, replacement)

    assert compute_profile_digest(drifted) != compute_profile_digest(body)
    assert compute_budget_digest(drifted) != compute_budget_digest(body)


@pytest.mark.parametrize(
    ("message_count", "error_type"),
    (
        (True, TypeError),
        (False, TypeError),
        (1.0, TypeError),
        (1.5, TypeError),
        ("1", TypeError),
        (None, TypeError),
        (-1, ValueError),
    ),
)
def test_prompt_admission_rejects_invalid_message_count(
    message_count: Any, error_type: type[Exception]
) -> None:
    profile = load_paper_pipeline_profile()

    with pytest.raises(error_type, match="message_count"):
        profile.estimate_prompt_tokens(b"{}", message_count=message_count)


def test_offline_plan_approval_cannot_authorize_provider_write() -> None:
    profile = load_paper_pipeline_profile()

    assert not hasattr(paper_pipeline_profile, "assert_paid_provider_authority")
    assert hasattr(
        paper_pipeline_profile, "reject_offline_implementation_approval"
    )
    assert profile.offline_approval.approval_kind == "offline_implementation"
    assert set(_profile_body()["offline_approval"]) == {
        "schema_version",
        "approval_kind",
        "approved_plan_digest",
        "approved_profile_digest",
    }
    with pytest.raises(
        ValueError, match="offline plan approval is not a paid execution receipt"
    ):
        paper_pipeline_profile.reject_offline_implementation_approval(
            profile.offline_approval
        )


@pytest.mark.parametrize(
    ("leaf_path", "replacement"), PAID_AND_METRIC_LEAF_MUTATIONS
)
def test_profile_digest_changes_for_any_paid_or_metric_control(
    tmp_path: Path,
    leaf_path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    body = _profile_body()
    original_digest = compute_profile_digest(body)
    drifted = copy.deepcopy(body)
    _set_leaf(drifted, leaf_path, replacement)
    assert compute_profile_digest(drifted) != original_digest

    path = tmp_path / "drifted-profile.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="digest drift"):
        load_paper_pipeline_profile(path)


@pytest.mark.parametrize(
    ("leaf_path", "replacement"), CAPABILITY_CONTROL_LEAF_MUTATIONS
)
def test_capability_control_drift_changes_both_digests_and_rejects_stored_digest(
    tmp_path: Path,
    leaf_path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    body = _profile_body()
    capability = body["online_checks"]["capability"]
    missing = object()
    original = missing
    try:
        original = _value_at(capability, leaf_path)
    except KeyError:
        pass
    assert original is not missing, f"missing capability field {leaf_path}"

    drifted = copy.deepcopy(body)
    _set_leaf(drifted["online_checks"]["capability"], leaf_path, replacement)
    assert compute_profile_digest(drifted) != compute_profile_digest(body)
    assert compute_budget_digest(drifted) != compute_budget_digest(body)

    path = tmp_path / "capability-drift.json"
    _write_profile(path, drifted)
    with pytest.raises(ValueError, match="budget digest drift"):
        load_paper_pipeline_profile(path)


@pytest.mark.parametrize("object_path", CLOSED_SCHEMA_PATHS)
def test_top_and_major_nested_objects_are_closed_schema(
    tmp_path: Path, object_path: tuple[str | int, ...]
) -> None:
    body = _profile_body()
    target = _value_at(body, object_path)
    assert isinstance(target, dict)
    target["unexpected_field"] = "must-reject"
    _reseal_profile(body)
    path = tmp_path / "unknown-field.json"
    _write_profile(path, body)

    with pytest.raises(ValueError, match="schema drift"):
        load_paper_pipeline_profile(path)


@pytest.mark.parametrize("replacement", (True, 1.0))
@pytest.mark.parametrize("leaf_path", STRICT_INTEGER_PATHS)
def test_every_profile_integer_field_rejects_bool_and_float(
    tmp_path: Path,
    leaf_path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    body = _profile_body()
    missing = object()
    original = missing
    try:
        original = _value_at(body, leaf_path)
    except KeyError:
        pass
    assert original is not missing, f"missing configured integer field {leaf_path}"
    assert type(original) is int
    _set_leaf(body, leaf_path, replacement)
    _reseal_profile(body)
    path = tmp_path / "invalid-integer.json"
    _write_profile(path, body)

    with pytest.raises(ValueError, match="must be an integer"):
        load_paper_pipeline_profile(path)


def test_raw_authority_paths_and_profile_have_explicit_git_eol_attribute() -> None:
    body = _profile_body()
    authority_paths = tuple(
        str(value)
        for name, value in body["authorities"].items()
        if name.endswith("_path")
    )
    repository_root = DEFAULT_EPD027_PIPELINE_PROFILE_PATH.parents[2]
    profile_path = DEFAULT_EPD027_PIPELINE_PROFILE_PATH.relative_to(
        repository_root
    ).as_posix()
    paths = authority_paths + (profile_path,)
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *paths],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    resolved = {
        line.split(": ", 2)[0]: line.split(": ", 2)[2]
        for line in result.stdout.splitlines()
    }

    assert set(resolved) == set(paths)
    assert all(resolved[path] in {"lf", "crlf"} for path in paths)
