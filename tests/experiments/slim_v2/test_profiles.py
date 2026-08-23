from __future__ import annotations

from collections import Counter
from hashlib import sha256
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REPO_ROOT = Path(__file__).parents[3]
FACTOR_CATALOG_PATH = REPO_ROOT / "benchmarks/paper/factorization_catalog.v2.jsonl"
LEAN_CATALOG_PATH = REPO_ROOT / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"

DIFFICULTIES = ("easy", "medium", "hard")
LEAN_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
TOPICS = ("pure_logic", "function_set", "induction")

FACTOR_HARD_PINS = ("factor_v2_hard_138", "factor_v2_hard_145")
LEAN_PINS = {
    ("simple", "pure_logic"): "lean_v2_simple_pure_logic_direct_prop_01",
    ("simple", "induction"): "lean_v2_simple_induction_direct_nat_01",
}


def _profiles() -> ModuleType:
    return importlib.import_module("tokenshare.experiments.slim_v2.profiles")


def _load_catalog(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for catalog_ordinal, line in enumerate(handle):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_catalog_ordinal"] = catalog_ordinal
            rows.append(row)
    return rows


def _ranked(
    rows: list[dict[str, Any]],
    *,
    domain: str,
    difficulty: str,
) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[str, int, str]:
        identity = (
            "slim_v2.full.v1|seed=20260820|"
            f"domain={domain}|difficulty={difficulty}|case_id={row['case_id']}"
        )
        return (
            sha256(identity.encode("utf-8")).hexdigest(),
            int(row.get("catalog_ordinal", row["_catalog_ordinal"])),
            str(row["case_id"]),
        )

    return sorted(rows, key=key)


def _pin_then_rank(
    ranked_rows: list[dict[str, Any]],
    pins: tuple[str, ...],
    count: int,
) -> tuple[str, ...]:
    available = {str(row["case_id"]) for row in ranked_rows}
    assert set(pins) <= available
    return pins + tuple(
        str(row["case_id"])
        for row in ranked_rows
        if row["case_id"] not in pins
    )[: count - len(pins)]


def _expected_full_ids() -> dict[str, tuple[str, ...]]:
    factor_rows = _load_catalog(FACTOR_CATALOG_PATH)
    lean_rows = _load_catalog(LEAN_CATALOG_PATH)

    factor_by_difficulty: dict[str, tuple[str, ...]] = {}
    for difficulty in DIFFICULTIES:
        rows = [row for row in factor_rows if row["paper_difficulty"] == difficulty]
        ranked = _ranked(rows, domain="factorization", difficulty=difficulty)
        pins = FACTOR_HARD_PINS if difficulty == "hard" else ()
        factor_by_difficulty[difficulty] = _pin_then_rank(ranked, pins, 100)

    lean_by_stratum: dict[tuple[str, str], tuple[str, ...]] = {}
    for difficulty in LEAN_DIFFICULTIES:
        for topic in TOPICS:
            rows = [
                row
                for row in lean_rows
                if row["paper_difficulty"] == difficulty
                and row["topic_family"] == topic
                and row["preflight_status"] == "passed"
            ]
            ranked = _ranked(rows, domain="lean", difficulty=difficulty)
            pin = LEAN_PINS.get((difficulty, topic))
            lean_by_stratum[(difficulty, topic)] = _pin_then_rank(
                ranked,
                (pin,) if pin else (),
                15,
            )

    exp1_factor = tuple(
        case_id
        for difficulty in DIFFICULTIES
        for case_id in factor_by_difficulty[difficulty]
    )
    exp1_lean = tuple(
        case_id
        for difficulty in LEAN_DIFFICULTIES
        for topic in TOPICS
        for case_id in lean_by_stratum[(difficulty, topic)]
    )
    return {
        "exp1_factor": exp1_factor,
        "exp1_lean": exp1_lean,
        "exp2_factor": factor_by_difficulty["hard"][:50],
        "exp3_factor": (
            factor_by_difficulty["easy"][:17]
            + factor_by_difficulty["medium"][:17]
            + factor_by_difficulty["hard"][:16]
        ),
        "exp3_lean": tuple(
            lean_by_stratum[("simple", topic)][0] for topic in TOPICS
        ),
        "exp4_factor": (
            factor_by_difficulty["easy"][:17]
            + factor_by_difficulty["medium"][:17]
            + factor_by_difficulty["hard"][:16]
        ),
        "exp4_lean": tuple(
            case_id
            for difficulty in LEAN_DIFFICULTIES
            for topic, count in zip(TOPICS, (2, 2, 1), strict=True)
            for case_id in lean_by_stratum[(difficulty, topic)][:count]
        ),
        "exp5_factor": factor_by_difficulty["hard"][:28],
        "exp5_lean": tuple(
            case_id
            for topic in TOPICS
            for case_id in lean_by_stratum[("hard_frontier", topic)][:3]
        ),
    }


def _all_profile_id_tuples(profiles: ModuleType) -> tuple[tuple[str, ...], ...]:
    return (
        profiles.EXP1_FACTORIZATION_CASE_IDS,
        profiles.EXP1_LEAN_CASE_IDS,
        profiles.EXP2_FACTORIZATION_CASE_IDS,
        profiles.EXP3_FACTORIZATION_CASE_IDS,
        profiles.EXP3_LEAN_CASE_IDS,
        profiles.EXP4_FACTORIZATION_CASE_IDS,
        profiles.EXP4_LEAN_CASE_IDS,
        profiles.EXP5_FACTORIZATION_CASE_IDS,
        profiles.EXP5_LEAN_CASE_IDS,
        profiles.REPRESENTATIVE_CASE_IDS,
    )


def test_full_case_ids_are_literal_unique_and_exist_in_current_catalogs() -> None:
    profiles = _profiles()
    factor_ids = {row["case_id"] for row in _load_catalog(FACTOR_CATALOG_PATH)}
    lean_ids = {row["case_id"] for row in _load_catalog(LEAN_CATALOG_PATH)}
    all_catalog_ids = factor_ids | lean_ids

    for case_ids in _all_profile_id_tuples(profiles):
        assert isinstance(case_ids, tuple)
        assert len(case_ids) == len(set(case_ids))
        assert set(case_ids) <= all_catalog_ids

    assert set(profiles.EXP1_FACTORIZATION_CASE_IDS) <= factor_ids
    assert set(profiles.EXP1_LEAN_CASE_IDS) <= lean_ids
    assert set(profiles.EXP3_LEAN_CASE_IDS) <= lean_ids
    assert set(profiles.EXP4_LEAN_CASE_IDS) <= lean_ids
    assert set(profiles.EXP5_LEAN_CASE_IDS) <= lean_ids


def test_full_case_ids_match_all_authority_strata_and_counts() -> None:
    profiles = _profiles()
    expected = _expected_full_ids()

    assert profiles.EXP1_FACTORIZATION_CASE_IDS == expected["exp1_factor"]
    assert profiles.EXP1_LEAN_CASE_IDS == expected["exp1_lean"]
    assert profiles.EXP2_FACTORIZATION_CASE_IDS == expected["exp2_factor"]
    assert profiles.EXP5_FACTORIZATION_CASE_IDS == expected["exp5_factor"]
    assert profiles.EXP5_LEAN_CASE_IDS == expected["exp5_lean"]
    assert tuple(map(len, _all_profile_id_tuples(profiles)[:9])) == (
        300,
        135,
        50,
        50,
        3,
        50,
        15,
        28,
        9,
    )

    factor_by_id = {
        row["case_id"]: row for row in _load_catalog(FACTOR_CATALOG_PATH)
    }
    lean_by_id = {row["case_id"]: row for row in _load_catalog(LEAN_CATALOG_PATH)}
    assert Counter(
        factor_by_id[case_id]["paper_difficulty"]
        for case_id in profiles.EXP1_FACTORIZATION_CASE_IDS
    ) == {"easy": 100, "medium": 100, "hard": 100}
    assert Counter(
        (lean_by_id[case_id]["paper_difficulty"], lean_by_id[case_id]["topic_family"])
        for case_id in profiles.EXP1_LEAN_CASE_IDS
    ) == {
        (difficulty, topic): 15
        for difficulty in LEAN_DIFFICULTIES
        for topic in TOPICS
    }
    assert {
        (
            lean_by_id[case_id]["catalog_pool_role"],
            lean_by_id[case_id]["preflight_status"],
        )
        for case_id in profiles.EXP1_LEAN_CASE_IDS
    } == {("task14_checker_backed_pool", "passed")}


def test_full_literal_ids_produce_exact_one_thousand_nine_hundred_seventy_planned_units() -> None:
    profiles = _profiles()
    factor_by_id = {
        row["case_id"]: row for row in _load_catalog(FACTOR_CATALOG_PATH)
    }
    lean_by_id = {row["case_id"]: row for row in _load_catalog(LEAN_CATALOG_PATH)}

    factor_units = sum(
        int(factor_by_id[case_id]["split_params"]["requested_child_count"])
        for case_id in profiles.EXP1_FACTORIZATION_CASE_IDS
    )
    lean_units = sum(
        int(lean_by_id[case_id]["expected_ai_unit_count"])
        for case_id in profiles.EXP1_LEAN_CASE_IDS
    )
    assert factor_units + lean_units == 1_970


def test_exp3_and_exp4_case_id_tuples_match_frozen_selection_rules_exactly() -> None:
    profiles = _profiles()
    expected = _expected_full_ids()

    assert profiles.EXP3_FACTORIZATION_CASE_IDS == expected["exp3_factor"]
    assert profiles.EXP3_LEAN_CASE_IDS == expected["exp3_lean"]
    assert profiles.EXP4_FACTORIZATION_CASE_IDS == expected["exp4_factor"]
    assert profiles.EXP4_LEAN_CASE_IDS == expected["exp4_lean"]
    assert profiles.EXP3_FACTORIZATION_CASE_IDS == profiles.EXP4_FACTORIZATION_CASE_IDS


def test_representative_case_ids_and_strata_are_exact() -> None:
    profiles = _profiles()
    expected_case_ids = (
        "factor_v2_hard_138",
        "factor_v2_hard_145",
        "lean_v2_simple_induction_direct_nat_01",
        "lean_v2_simple_pure_logic_direct_prop_01",
    )
    expected_challenge_rows = (
        (
            "factor_v2_hard_138",
            0,
            "INVALID_PARSED_CANDIDATE",
            "stable_first_planned_unit",
            "ordinal_0",
        ),
        (
            "factor_v2_hard_145",
            0,
            "PARSER_REQUIRED_CANONICAL_JSON",
            "stable_first_planned_unit",
            "every_attempt",
        ),
        (
            "lean_v2_simple_induction_direct_nat_01",
            0,
            "REQUIRED_CHILD_DELAY",
            "last_required_terminal_slot",
            "ordinal_0",
        ),
        (
            "lean_v2_simple_pure_logic_direct_prop_01",
            0,
            "RECOVERABLE_NO_RETURN",
            "stable_first_planned_unit",
            "ordinal_0",
        ),
    )
    assert profiles.REPRESENTATIVE_CASE_IDS == expected_case_ids
    assert profiles.REPRESENTATIVE_CHALLENGE_ROWS == expected_challenge_rows

    factor_by_id = {
        row["case_id"]: row for row in _load_catalog(FACTOR_CATALOG_PATH)
    }
    lean_by_id = {row["case_id"]: row for row in _load_catalog(LEAN_CATALOG_PATH)}
    assert [
        (
            factor_by_id[case_id]["paper_difficulty"],
            factor_by_id[case_id]["factor_position_quantile"],
            factor_by_id[case_id]["split_params"]["requested_child_count"],
        )
        for case_id in expected_case_ids[:2]
    ] == [("hard", "late", 8), ("hard", "early", 8)]
    assert [
        (
            lean_by_id[case_id]["paper_difficulty"],
            lean_by_id[case_id]["topic_family"],
            lean_by_id[case_id]["expected_ai_unit_count"],
        )
        for case_id in expected_case_ids[2:]
    ] == [("simple", "induction", 1), ("simple", "pure_logic", 2)]
    assert 8 + 8 + 1 + 2 == 19


def test_full_condition_and_root_counts_match_canonical_inventory() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory

    inventory = build_inventory("full")

    assert inventory.paper_root_counts == {
        "exp1": 435,
        "exp2": 600,
        "exp3": 3_726,
        "exp4": 2_145,
        "exp5": 111,
    }
    assert inventory.paper_root_count == 7_017
    assert inventory.reference_root_counts == {"exp3": 106}
    assert inventory.execution_root_count == 7_123
    assert inventory.condition_counts == {
        "exp1": 12,
        "exp2": 48,
        "exp3": 342,
        "exp4": 33,
        "exp5": 12,
    }
    assert all(
        condition.continue_after_terminal_child_failure is False
        for condition in inventory.conditions
    )
    assert all(condition.worker_count is not None for condition in inventory.conditions)
    assert all(condition.max_retries is not None for condition in inventory.conditions)
    assert all(
        condition.source_repeat_id == 0
        for condition in inventory.conditions
        if condition.experiment_id in {"exp2", "exp3", "exp4"}
    )
    assert len({condition.condition_id for condition in inventory.conditions}) == len(
        inventory.conditions
    )

    for condition in inventory.conditions:
        if condition.experiment_id == "exp1":
            dimensions = (
                f"domain={condition.domain}|difficulty={condition.difficulty}"
            )
            if condition.topic_family is not None:
                dimensions += f"|topic={condition.topic_family}"
        elif condition.experiment_id == "exp2":
            dimensions = (
                f"worker={condition.worker_count}|repeat={condition.repeat_id}"
                f"|position={condition.position_stratum}"
            )
        elif condition.experiment_id == "exp3":
            stratum = (
                f"difficulty={condition.difficulty}"
                if condition.domain == "factorization"
                else f"topic={condition.topic_family}"
            )
            if condition.fault_type is not None:
                dimensions = (
                    f"domain={condition.domain}|{stratum}"
                    f"|fault={condition.fault_type}"
                    f"|rate={condition.fault_rate_percent}"
                    f"|repeat={condition.repeat_id}"
                )
            else:
                dimensions = (
                    f"domain={condition.domain}|{stratum}"
                    f"|dead={condition.dead_worker_count}"
                    f"|progress={condition.kill_progress_percent}"
                    f"|repeat={condition.repeat_id}"
                )
        elif condition.experiment_id == "exp4":
            dimensions = f"mode={condition.mode}|repeat={condition.repeat_id}"
        else:
            stratum = (
                "factorization_hard"
                if condition.domain == "factorization"
                else f"lean_hard_{condition.topic_family}"
            )
            dimensions = (
                f"model={condition.model_id}|repeat={condition.repeat_id}"
                f"|stratum={stratum}"
            )
        assert condition.condition_id == f"{condition.experiment_id}|{dimensions}"

    for reference in inventory.references:
        stratum = (
            f"difficulty={reference.difficulty}"
            if reference.domain == "factorization"
            else f"topic={reference.topic_family}"
        )
        assert reference.condition_id == (
            f"exp3-reference|domain={reference.domain}|{stratum}"
            f"|repeat={reference.repeat_id}"
        )


def test_full_attempt_and_provider_call_hard_caps_are_derived_from_inventory() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory, build_plan

    inventory = build_inventory("full")
    plan = build_plan("full")

    for experiment_id, experiment in plan.experiments.items():
        assert experiment.planned_first_attempt_ai_units == sum(
            root.planned_ai_unit_count
            for root in inventory.roots
            if root.experiment_id == experiment_id
        )

    exp3_roots = [root for root in inventory.roots if root.experiment_id == "exp3"]
    conditions = {condition.condition_id: condition for condition in inventory.conditions}
    exp3_rate_units = sum(
        root.planned_ai_unit_count
        for root in exp3_roots
        if conditions[root.condition_id].fault_type is not None
    )
    exp3_death_units = sum(
        root.planned_ai_unit_count
        for root in exp3_roots
        if conditions[root.condition_id].dead_worker_count is not None
    )
    assert (exp3_rate_units, exp3_death_units) == (13_920, 2_808)
    assert plan.experiments["exp3"].planned_first_attempt_ai_units == 16_728
    assert plan.experiments["exp3"].protocol_execution_attempt_upper == (
        3 * exp3_rate_units + 4 * exp3_death_units
    ) == 52_992

    exp4_roots = [root for root in inventory.roots if root.experiment_id == "exp4"]
    exp4_mode_repeat_units = Counter(
        (conditions[root.condition_id].mode, root.repeat_id)
        for root in exp4_roots
        for _unit in range(root.planned_ai_unit_count)
    )
    assert set(exp4_mode_repeat_units.values()) == {293}
    assert plan.experiments["exp4"].planned_first_attempt_ai_units == 9_669
    assert plan.experiments["exp4"].protocol_execution_attempt_upper == (
        7 * 3 * 293 * 2 + 4 * 3 * 293
    ) == 15_822
    assert plan.experiments["exp2"].protocol_execution_attempt_upper == 14_400
    assert plan.exp3_reference_protocol_execution_attempt_upper == 1_404
    assert plan.experiments["exp5"].planned_first_attempt_ai_units == 852
    assert plan.experiments["exp5"].protocol_execution_attempt_upper == 852
    assert plan.experiments["exp5"].provider_call_upper == 852
    fixed_replay_attempts = sum(
        (
            plan.experiments["exp2"].protocol_execution_attempt_upper,
            plan.experiments["exp3"].protocol_execution_attempt_upper,
            plan.experiments["exp4"].protocol_execution_attempt_upper,
            plan.exp3_reference_protocol_execution_attempt_upper,
        )
    )
    assert fixed_replay_attempts == 84_618
    assert fixed_replay_attempts + plan.online_provider_call_upper == 91_380
    assert plan.online_provider_call_upper == 6_762
    assert plan.online_provider_call_upper == sum(
        experiment.provider_call_upper for experiment in plan.experiments.values()
    )


def test_full_hard_upper_tracks_three_model_exp5_online_term() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_plan

    plan = build_plan("full", representative_raw_response_p95_bytes=256 * 1024)

    assert plan.hard_response_bytes == 16 * 1024 * 1024
    assert plan.online_response_artifact_hard_upper_bytes == 283_826_565_120
    assert round(plan.online_response_artifact_hard_upper_gib, 2) == 264.33
    assert plan.hard_upper_bytes > plan.online_response_artifact_hard_upper_bytes
    assert plan.hard_upper_gib == plan.hard_upper_bytes / 1024**3
    assert isinstance(plan.hard_upper_bytes, int)


def test_exp3_reference_inventory_is_separate_from_paper_denominator() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory, build_plan

    inventory = build_inventory("full")
    plan = build_plan("full")

    assert len(inventory.references) == 106
    assert not {root.root_run_id for root in inventory.references} & {
        root.root_run_id for root in inventory.roots
    }
    assert all(root.is_paper_denominator is False for root in inventory.references)
    assert plan.exp3_reference_planned_first_attempt_ai_units == 468
    assert plan.exp3_reference_protocol_execution_attempt_upper == 1_404


def test_exp4_full_challenge_quotas_and_mode_expansion_are_exact() -> None:
    from tokenshare.experiments.slim_v2.profiles import EXP4_MODES, build_inventory

    inventory = build_inventory("full")
    quotas = Counter(plan.challenge_family for plan in inventory.challenges)
    exp4_roots = [root for root in inventory.roots if root.experiment_id == "exp4"]

    assert len(inventory.challenges) == 195
    assert quotas == {
        "INVALID_PARSED_CANDIDATE": 49,
        "PARSER_REQUIRED_CANONICAL_JSON": 49,
        "RECOVERABLE_NO_RETURN": 48,
        "REQUIRED_CHILD_DELAY": 49,
    }
    assert len(EXP4_MODES) == 11
    assert len(exp4_roots) == len(inventory.challenges) * len(EXP4_MODES) == 2_145
    assert {
        (root.case_id, root.repeat_id, root.challenge_plan_id)
        for root in exp4_roots
    } == {
        (plan.case_id, plan.repeat_id, plan.challenge_plan_id)
        for plan in inventory.challenges
    }


def test_full_required_child_delay_targets_follow_domain_and_primality() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory

    inventory = build_inventory("full")
    profiles = _profiles()
    factor = {row["case_id"]: row for row in _load_catalog(FACTOR_CATALOG_PATH)}
    lean = {row["case_id"]: row for row in _load_catalog(LEAN_CATALOG_PATH)}
    factor_delay = [
        plan
        for plan in inventory.challenges
        if plan.challenge_family == "REQUIRED_CHILD_DELAY" and plan.case_id in factor
    ]
    lean_delay = [
        plan
        for plan in inventory.challenges
        if plan.challenge_family == "REQUIRED_CHILD_DELAY" and plan.case_id not in factor
    ]

    assert [plan.challenge_plan_id for plan in inventory.challenges] == [
        f"exp4-challenge-{ordinal:03d}" for ordinal in range(195)
    ]
    expected_factor_order = sorted(
        [
            (case_id, repeat_id)
            for case_id in profiles.EXP4_FACTORIZATION_CASE_IDS
            for repeat_id in (0, 1, 2)
        ],
        key=lambda item: (factor[item[0]]["paper_difficulty"], item[0], item[1]),
    )
    expected_lean_order = sorted(
        [
            (case_id, repeat_id)
            for case_id in profiles.EXP4_LEAN_CASE_IDS
            for repeat_id in (0, 1, 2)
        ],
        key=lambda item: (
            lean[item[0]]["paper_difficulty"],
            lean[item[0]]["topic_family"],
            item[0],
            item[1],
        ),
    )
    assert [(plan.case_id, plan.repeat_id) for plan in inventory.challenges] == (
        expected_factor_order + expected_lean_order
    )
    assert len(factor_delay) == 37
    assert len(lean_delay) == 12
    for plan in factor_delay:
        row = factor[plan.case_id]
        is_prime = (
            len(row["oracle_prime_factors"]) == 1
            and row["oracle_prime_factors"][0]["prime"] == row["target_n"]
        )
        assert plan.target_rule == (
            "last_required_range" if is_prime else "all_true_divisor_ranges"
        )
    assert {plan.target_rule for plan in lean_delay} == {
        "last_required_terminal_slot"
    }


def test_full_exp5_inventory_preserves_repeat_model_order() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory

    inventory = build_inventory("full")
    conditions = {item.condition_id: item for item in inventory.conditions}
    exp5_conditions = [
        condition
        for condition in inventory.conditions
        if condition.experiment_id == "exp5"
    ]
    assert len(exp5_conditions) == 12
    assert {condition.repeat_id for condition in exp5_conditions} == {0}
    assert {condition.max_retries for condition in exp5_conditions} == {0}
    blocks: list[tuple[int, str | None]] = []
    for root in inventory.roots:
        if root.experiment_id != "exp5":
            continue
        block = (root.repeat_id, conditions[root.condition_id].model_id)
        if not blocks or blocks[-1] != block:
            blocks.append(block)

    assert blocks == [
        (0, "zai-org/GLM-5.2"),
        (0, "Qwen/Qwen3-14B"),
        (0, "MiniMaxAI/MiniMax-M2.5"),
    ]

    representative = build_inventory("representative")
    representative_conditions = {
        item.condition_id: item for item in representative.conditions
    }
    assert [
        representative_conditions[root.condition_id].model_id
        for root in representative.roots
        if root.experiment_id == "exp5"
    ] == [
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
    ]


def test_representative_inventory_and_provider_call_hard_cap_are_exact() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_inventory, build_plan

    inventory = build_inventory("representative")
    plan = build_plan("representative")

    assert inventory.paper_root_counts == {
        "exp1": 4,
        "exp2": 12,
        "exp3": 8,
        "exp4": 44,
        "exp5": 3,
    }
    assert inventory.reference_root_counts == {"exp3": 2}
    for experiment_id, experiment in plan.experiments.items():
        assert experiment.planned_first_attempt_ai_units == sum(
            root.planned_ai_unit_count
            for root in inventory.roots
            if root.experiment_id == experiment_id
        )
    assert plan.experiments["exp1"].planned_first_attempt_ai_units == 19
    conditions = {condition.condition_id: condition for condition in inventory.conditions}
    exp3_roots = [root for root in inventory.roots if root.experiment_id == "exp3"]
    exp3_rate_units = sum(
        root.planned_ai_unit_count
        for root in exp3_roots
        if conditions[root.condition_id].fault_type is not None
    )
    exp3_death_units = sum(
        root.planned_ai_unit_count
        for root in exp3_roots
        if conditions[root.condition_id].dead_worker_count is not None
    )
    assert (exp3_rate_units, exp3_death_units) == (42, 16)
    assert plan.experiments["exp3"].planned_first_attempt_ai_units == 58
    assert plan.experiments["exp3"].protocol_execution_attempt_upper == (
        3 * exp3_rate_units + 4 * exp3_death_units
    ) == 190
    assert plan.experiments["exp5"].provider_call_upper == 24
    assert plan.online_provider_call_upper == 81
    assert all(
        root.worker_count == 10
        for root in inventory.roots
        if root.experiment_id != "exp2"
    )


def test_plan_reports_exp2_and_exp3_reference_ceilings_from_frozen_units() -> None:
    from tokenshare.experiments.slim_v2.profiles import build_plan

    full = build_plan("full")
    representative = build_plan("representative")

    assert (
        full.experiments["exp2"].planned_first_attempt_ai_units,
        full.experiments["exp2"].protocol_execution_attempt_upper,
    ) == (4_800, 14_400)
    assert (
        full.exp3_reference_planned_first_attempt_ai_units,
        full.exp3_reference_protocol_execution_attempt_upper,
    ) == (468, 1_404)
    assert (
        representative.experiments["exp2"].planned_first_attempt_ai_units,
        representative.experiments["exp2"].protocol_execution_attempt_upper,
    ) == (96, 288)
    assert (
        representative.exp3_reference_planned_first_attempt_ai_units,
        representative.exp3_reference_protocol_execution_attempt_upper,
    ) == (10, 30)


def test_representative_inventory_jsonl_round_trips_to_task1_projector_input(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.projector import (
        RootProjectionError,
        project_root_result,
    )
    from tokenshare.experiments.slim_v2.schema import RootInventoryV1
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    typed = project_root_inventory_rows(inventory)
    store = RunStore(tmp_path)

    dispositions = store.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=typed.roots,
        exp3_references=typed.exp3_references,
        exp4_challenges=inventory.challenges,
    )
    assert dispositions == {
        "conditions": "written",
        "roots": "written",
        "exp3_references": "written",
        "exp4_challenges": "written",
    }
    assert store.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=typed.roots,
        exp3_references=typed.exp3_references,
        exp4_challenges=inventory.challenges,
    ) == {
        "conditions": "skipped",
        "roots": "skipped",
        "exp3_references": "skipped",
        "exp4_challenges": "skipped",
    }
    assert {
        path.name for path in (tmp_path / "inventory").iterdir()
    } == {
        "conditions.jsonl",
        "roots.jsonl",
        "exp3_references.jsonl",
        "exp4_challenges.jsonl",
    }

    raw_roots = [
        json.loads(line)
        for line in store.inventory_path("roots").read_text(encoding="utf-8").splitlines()
    ]
    assert len(raw_roots) == len(inventory.roots) == 71
    assert all(set(row) == set(RootInventoryV1.field_names()) for row in raw_roots)

    loaded = store.read_root_inventory_rows("roots")
    assert loaded == typed.roots
    assert store.read_root_inventory_rows("exp3_references") == typed.exp3_references
    assert all(isinstance(row, RootInventoryV1) for row in loaded)
    assert all(
        len(row.planned_ai_unit_ids) == root.planned_ai_unit_count
        for row, root in zip(loaded, inventory.roots, strict=True)
    )

    exp1 = next(row for row in loaded if row.experiment_id == "exp1")
    with pytest.raises(RootProjectionError, match="run_id"):
        project_root_result(
            inventory=exp1,
            assembly=type("Assembly", (), {"run_id": "assembly-run"})(),
            protocol_result=type("Result", (), {"run_id": "other-run"})(),
            provider_family="deepseek",
            requested_model="deepseek-v4-pro",
            resolved_model="deepseek-v4-pro",
            reasoning_mode="enabled",
        )


def test_frozen_inventory_stops_at_middle_conflict(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import (
        RunStore,
        StorageConflictError,
    )

    inventory = build_inventory("representative")
    typed = project_root_inventory_rows(inventory)
    store = RunStore(tmp_path)
    conflicting_roots = store.inventory_path("roots")
    conflicting_roots.parent.mkdir(parents=True)
    conflicting_roots.write_text('{"conflict":true}\n', encoding="utf-8")

    with pytest.raises(StorageConflictError, match="conflicting ordinary file"):
        store.write_frozen_inventories(
            conditions=inventory.conditions,
            roots=typed.roots,
            exp3_references=typed.exp3_references,
            exp4_challenges=inventory.challenges,
        )

    conditions_path = store.inventory_path("conditions")
    assert len(conditions_path.read_text(encoding="utf-8").splitlines()) == len(
        inventory.conditions
    )
    assert conflicting_roots.read_text(encoding="utf-8") == '{"conflict":true}\n'
    assert not store.inventory_path("exp3_references").exists()
    assert not store.inventory_path("exp4_challenges").exists()


def test_forward_exp1_inventory_uses_flash_identity() -> None:
    from tokenshare.experiments.slim_v2.profiles import (
        build_profile,
        build_inventory,
        project_root_inventory_rows,
    )

    profile = build_profile("representative")
    assert profile.experiments["exp1"].provider_entry_id == (
        "deepseek_v4_flash_exp1_baseline"
    )
    assert profile.experiments["exp1"].model_id == "deepseek-v4-flash"

    rows = project_root_inventory_rows(build_inventory("representative"))
    for row in (*rows.roots, *rows.exp3_references):
        if row.experiment_id in {"exp1", "exp2", "exp3", "exp4"}:
            assert row.provider_entry_id == "deepseek_v4_flash_exp1_baseline"
            assert row.configured_model == "deepseek-v4-flash"
