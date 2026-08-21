from __future__ import annotations

import ast
from collections import Counter
from hashlib import sha256
import importlib
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


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
        "exp5_factor": factor_by_difficulty["hard"][:42],
        "exp5_lean": tuple(
            case_id
            for topic in TOPICS
            for case_id in lean_by_stratum[("hard_frontier", topic)][:4]
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
        42,
        12,
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


def test_runtime_profiles_do_not_read_legacy_selection_files() -> None:
    profiles = _profiles()
    source = inspect.getsource(profiles)
    syntax = ast.parse(source)

    assert "paper_suite_scale_300_50_54.v1" not in source
    assert "exp5_parent_quarter_selection.v4" not in source
    assert "archive" not in source.lower()
    assert not any(
        isinstance(
            node,
            (
                ast.Call,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Import,
                ast.ImportFrom,
            ),
        )
        for node in ast.walk(syntax)
    )

    module_name = profiles.__name__
    del sys.modules[module_name]
    reloaded = importlib.import_module(module_name)
    assert reloaded.REPRESENTATIVE_CASE_IDS == profiles.REPRESENTATIVE_CASE_IDS
