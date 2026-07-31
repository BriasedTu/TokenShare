"""Tracked hierarchical scale profile for the current paper suite."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import hashlib
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import JsonObject, digest_json


PAPER_SUITE_SCALE_PROFILE_SCHEMA_VERSION = (
    "tokenshare.paper_suite_scale_profile.v1"
)
PAPER_SUITE_SCALE_POLICY_SCHEMA_VERSION = (
    "tokenshare.paper_suite_scale_policy.v1"
)
PAPER_DIFFICULTIES = ("easy", "medium", "hard")
FACTOR_EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
EXP5_STRATUM_ORDER = (
    "factorization:hard",
    "lean_proof:hard:pure_logic",
    "lean_proof:hard:function_set",
    "lean_proof:hard:induction",
)
_SCOPES = ("corpus",) + FACTOR_EXPERIMENT_IDS
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_TRACKED_PROFILE_SOURCE_PATH = "benchmarks/paper/paper_suite_scale_profile.v1.json"


@dataclass(frozen=True, kw_only=True)
class PaperSuiteScaleProfile:
    profile_id: str
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    factorization_catalog_path: str
    factorization_catalog_content_digest: str
    score_schema_version: str
    seed: int
    difficulty_order: tuple[str, ...]
    factorization_case_count_by_scope: Mapping[str, Mapping[str, int]]
    exp5_source_selection_path: str
    exp5_source_selection_digest: str
    exp5_case_count_by_stratum: Mapping[str, int]
    profile_digest: str
    source_path: str
    schema_version: str = PAPER_SUITE_SCALE_PROFILE_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source_path: str = "<memory>",
    ) -> "PaperSuiteScaleProfile":
        allowed = {
            "schema_version",
            "profile_id",
            "approved_catalog",
            "selection_rule",
            "difficulty_order",
            "factorization_case_count_by_scope",
            "shared_selection_groups",
            "exp5",
            "profile_digest",
        }
        if set(value) - allowed:
            raise ValueError("paper suite scale profile contains unknown fields")
        body = dict(value)
        declared_digest = body.pop("profile_digest", None)
        profile_digest = digest_json(body)
        if declared_digest is not None and declared_digest != profile_digest:
            raise ValueError("paper suite scale profile digest drift")
        if body.get("schema_version") != PAPER_SUITE_SCALE_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported paper suite scale profile schema")
        profile_id = _string(body.get("profile_id"), "profile_id")

        catalog = _mapping(body.get("approved_catalog"), "approved_catalog")
        if set(catalog) != {
            "catalog_id",
            "catalog_version",
            "catalog_digest",
            "factorization_catalog_path",
            "factorization_catalog_content_digest",
        }:
            raise ValueError("paper suite scale catalog binding fields drift")
        rule = _mapping(body.get("selection_rule"), "selection_rule")
        if set(rule) != {"rule_id", "score_schema_version", "seed"}:
            raise ValueError("paper suite scale selection rule fields drift")
        if rule.get("rule_id") != "stable_hash_by_difficulty_not_catalog_prefix":
            raise ValueError("unsupported paper suite scale selection rule")
        seed = rule.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("paper suite scale seed must be a non-negative integer")

        difficulty_order = _string_tuple(
            body.get("difficulty_order"), "difficulty_order"
        )
        if difficulty_order != PAPER_DIFFICULTIES:
            raise ValueError("paper suite scale difficulty order drift")
        raw_scopes = _mapping(
            body.get("factorization_case_count_by_scope"),
            "factorization_case_count_by_scope",
        )
        if set(raw_scopes) != set(_SCOPES):
            raise ValueError("paper suite scale factorization scopes drift")
        counts_by_scope: dict[str, dict[str, int]] = {}
        for scope in _SCOPES:
            raw_counts = _mapping(raw_scopes[scope], scope)
            allowed_difficulties = (
                ("hard",)
                if scope == "exp2_real_ai_scalability"
                else PAPER_DIFFICULTIES
            )
            if set(raw_counts) != set(allowed_difficulties):
                raise ValueError("paper suite scale difficulty counts drift")
            counts_by_scope[scope] = {
                difficulty: _positive_int(raw_counts[difficulty], difficulty)
                for difficulty in allowed_difficulties
            }
        if counts_by_scope["exp1_real_ai_feasibility"] != counts_by_scope["corpus"]:
            raise ValueError("Experiment 1 must use the complete active corpus")
        if counts_by_scope["exp3_real_ai_fault_recovery"] != counts_by_scope[
            "exp4_real_ai_protocol_ablation"
        ]:
            raise ValueError("Experiment 3/4 factorization selections must be shared")
        corpus_counts = counts_by_scope["corpus"]
        for scope in FACTOR_EXPERIMENT_IDS:
            if any(
                count > corpus_counts[difficulty]
                for difficulty, count in counts_by_scope[scope].items()
            ):
                raise ValueError("paper suite scale selection exceeds active corpus")
        if body.get("shared_selection_groups") != [
            ["exp3_real_ai_fault_recovery", "exp4_real_ai_protocol_ablation"]
        ]:
            raise ValueError("paper suite scale shared selection group drift")

        exp5 = _mapping(body.get("exp5"), "exp5")
        if set(exp5) != {
            "source_selection_path",
            "source_selection_digest",
            "case_count_by_stratum",
        }:
            raise ValueError("paper suite scale Exp5 fields drift")
        exp5_counts = _mapping(
            exp5.get("case_count_by_stratum"), "exp5.case_count_by_stratum"
        )
        if set(exp5_counts) != set(EXP5_STRATUM_ORDER):
            raise ValueError("paper suite scale Exp5 strata drift")
        if rule.get("score_schema_version") != (
            "tokenshare.paper_factorization_stratified_score.v2"
        ):
            raise ValueError("unsupported paper suite scale score schema")

        return cls(
            profile_id=profile_id,
            catalog_id=_string(catalog.get("catalog_id"), "catalog_id"),
            catalog_version=_string(
                catalog.get("catalog_version"), "catalog_version"
            ),
            catalog_digest=_digest(catalog.get("catalog_digest"), "catalog_digest"),
            factorization_catalog_path=_string(
                catalog.get("factorization_catalog_path"),
                "factorization_catalog_path",
            ),
            factorization_catalog_content_digest=_digest(
                catalog.get("factorization_catalog_content_digest"),
                "factorization_catalog_content_digest",
            ),
            score_schema_version=_string(
                rule.get("score_schema_version"), "score_schema_version"
            ),
            seed=seed,
            difficulty_order=difficulty_order,
            factorization_case_count_by_scope=counts_by_scope,
            exp5_source_selection_path=_string(
                exp5.get("source_selection_path"), "source_selection_path"
            ),
            exp5_source_selection_digest=_digest(
                exp5.get("source_selection_digest"), "source_selection_digest"
            ),
            exp5_case_count_by_stratum={
                stratum: _positive_int(exp5_counts[stratum], stratum)
                for stratum in EXP5_STRATUM_ORDER
            },
            profile_digest=profile_digest,
            source_path=_string(source_path, "source_path"),
        )

    def source_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "approved_catalog": {
                "catalog_id": self.catalog_id,
                "catalog_version": self.catalog_version,
                "catalog_digest": self.catalog_digest,
                "factorization_catalog_path": self.factorization_catalog_path,
                "factorization_catalog_content_digest": (
                    self.factorization_catalog_content_digest
                ),
            },
            "selection_rule": {
                "rule_id": "stable_hash_by_difficulty_not_catalog_prefix",
                "score_schema_version": self.score_schema_version,
                "seed": self.seed,
            },
            "difficulty_order": list(self.difficulty_order),
            "factorization_case_count_by_scope": {
                scope: dict(self.factorization_case_count_by_scope[scope])
                for scope in _SCOPES
            },
            "shared_selection_groups": [
                [
                    "exp3_real_ai_fault_recovery",
                    "exp4_real_ai_protocol_ablation",
                ]
            ],
            "exp5": {
                "source_selection_path": self.exp5_source_selection_path,
                "source_selection_digest": self.exp5_source_selection_digest,
                "case_count_by_stratum": dict(self.exp5_case_count_by_stratum),
            },
        }

    def select_factorization_cases(
        self,
        candidates_by_difficulty: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> dict[str, dict[str, tuple[Mapping[str, Any], ...]]]:
        ranked: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for difficulty in self.difficulty_order:
            candidates = tuple(candidates_by_difficulty.get(difficulty, ()))
            ids = tuple(str(case.get("case_id") or "") for case in candidates)
            if any(not case_id for case_id in ids) or len(set(ids)) != len(ids):
                raise ValueError("paper suite scale candidate IDs drift")
            ranked[difficulty] = tuple(
                sorted(
                    candidates,
                    key=lambda case: (
                        digest_json(
                            {
                                "schema_version": self.score_schema_version,
                                "profile_id": self.profile_id,
                                "seed": self.seed,
                                "difficulty": difficulty,
                                "case_id": str(case["case_id"]),
                            }
                        ),
                        str(case["case_id"]),
                    ),
                )
            )
        selected: dict[str, dict[str, tuple[Mapping[str, Any], ...]]] = {}
        for scope in _SCOPES:
            selected[scope] = {}
            for difficulty, count in self.factorization_case_count_by_scope[
                scope
            ].items():
                if len(ranked[difficulty]) < count:
                    raise ValueError("paper suite scale candidate count is insufficient")
                selected[scope][difficulty] = ranked[difficulty][:count]
        return selected


def load_paper_suite_scale_profile(path: str | Path) -> PaperSuiteScaleProfile:
    profile_path = Path(path)
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to load paper suite scale profile") from exc
    if not isinstance(value, Mapping):
        raise ValueError("paper suite scale profile must be an object")
    profile = PaperSuiteScaleProfile.from_mapping(
        value,
        source_path=_source_path(profile_path),
    )
    factor_path = Path(profile.factorization_catalog_path)
    if not factor_path.is_absolute():
        factor_path = _REPOSITORY_ROOT / factor_path
    try:
        actual_digest = "sha256:" + hashlib.sha256(factor_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("unable to read approved Factorization catalog") from exc
    if actual_digest != profile.factorization_catalog_content_digest:
        raise ValueError("approved Factorization catalog content digest drift")
    exp5_path = Path(profile.exp5_source_selection_path)
    if not exp5_path.is_absolute():
        exp5_path = _REPOSITORY_ROOT / exp5_path
    from tokenshare.experiments.paper_exp5_model_comparison import (
        load_exp5_v4_selection,
    )

    try:
        exp5_selection = load_exp5_v4_selection(exp5_path)
    except ValueError as exc:
        raise ValueError("active Experiment 5 selection provenance drift") from exc
    if exp5_selection.get("selection_digest") != (
        profile.exp5_source_selection_digest
    ):
        raise ValueError("active Experiment 5 selection digest drift")
    return profile


def build_paper_suite_scale_policy(
    *,
    profile: PaperSuiteScaleProfile,
    catalog_id: str,
    catalog_version: str,
    catalog_digest: str,
    candidates_by_difficulty: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[JsonObject, dict[str, dict[str, tuple[Mapping[str, Any], ...]]]]:
    if (
        catalog_id != profile.catalog_id
        or catalog_version != profile.catalog_version
        or catalog_digest != profile.catalog_digest
    ):
        raise ValueError("paper suite scale approved catalog binding drift")
    selected = profile.select_factorization_cases(candidates_by_difficulty)
    binding: JsonObject = {
        "schema_version": "tokenshare.paper_suite_scale_profile_binding.v1",
        "profile_source_path": profile.source_path,
        "profile_source_digest": profile.profile_digest,
        "profile_body": profile.source_body(),
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "catalog_digest": catalog_digest,
    }
    binding["binding_digest"] = digest_json(binding)
    ordered = {
        scope: {
            difficulty: [str(case["case_id"]) for case in cases]
            for difficulty, cases in selected[scope].items()
        }
        for scope in _SCOPES
    }
    selection_digests = {
        scope: digest_json(
            {
                "schema_version": "tokenshare.paper_suite_scale_selection.v1",
                "profile_source_digest": profile.profile_digest,
                "scope": scope,
                "ordered_case_ids_by_difficulty": ordered[scope],
            }
        )
        for scope in _SCOPES
    }
    policy: JsonObject = {
        "schema_version": PAPER_SUITE_SCALE_POLICY_SCHEMA_VERSION,
        "profile_binding": binding,
        "ordered_case_ids_by_scope": ordered,
        "selection_digest_by_scope": selection_digests,
    }
    policy["policy_digest"] = digest_json(policy)
    return policy, selected


def validate_paper_suite_scale_policy(
    value: Any,
    *,
    catalog_id: str,
    catalog_version: str,
    catalog_digest: str,
    experiment_id: str,
) -> tuple[PaperSuiteScaleProfile, Mapping[str, tuple[str, ...]]]:
    policy = _mapping(value, "paper_suite_scale_policy")
    if set(policy) != {
        "schema_version",
        "profile_binding",
        "ordered_case_ids_by_scope",
        "selection_digest_by_scope",
        "policy_digest",
    }:
        raise ValueError("paper suite scale policy fields drift")
    if policy.get("schema_version") != PAPER_SUITE_SCALE_POLICY_SCHEMA_VERSION:
        raise ValueError("paper suite scale policy schema drift")
    policy_body = dict(policy)
    policy_digest = policy_body.pop("policy_digest", None)
    if policy_digest != digest_json(policy_body):
        raise ValueError("paper suite scale policy digest drift")
    binding = _mapping(policy.get("profile_binding"), "profile_binding")
    binding_body = dict(binding)
    binding_digest = binding_body.pop("binding_digest", None)
    if binding_digest != digest_json(binding_body):
        raise ValueError("paper suite scale profile binding digest drift")
    profile = PaperSuiteScaleProfile.from_mapping(
        _mapping(binding.get("profile_body"), "profile_body"),
        source_path=_string(binding.get("profile_source_path"), "profile_source_path"),
    )
    if binding.get("profile_source_digest") != profile.profile_digest:
        raise ValueError("paper suite scale profile source digest drift")
    if profile.source_path != _TRACKED_PROFILE_SOURCE_PATH:
        raise ValueError("paper suite scale profile source path drift")
    tracked_profile_path = Path(profile.source_path)
    if not tracked_profile_path.is_absolute():
        tracked_profile_path = _REPOSITORY_ROOT / tracked_profile_path
    tracked_profile = load_paper_suite_scale_profile(tracked_profile_path)
    if (
        tracked_profile.profile_digest != profile.profile_digest
        or tracked_profile.source_body() != profile.source_body()
    ):
        raise ValueError("paper suite scale tracked profile binding drift")
    if (
        binding.get("catalog_id") != catalog_id
        or binding.get("catalog_version") != catalog_version
        or binding.get("catalog_digest") != catalog_digest
        or profile.catalog_id != catalog_id
        or profile.catalog_version != catalog_version
        or profile.catalog_digest != catalog_digest
    ):
        raise ValueError("paper suite scale catalog binding drift")
    if experiment_id not in FACTOR_EXPERIMENT_IDS:
        raise ValueError("paper suite scale experiment binding drift")
    ordered_scopes = _mapping(
        policy.get("ordered_case_ids_by_scope"), "ordered_case_ids_by_scope"
    )
    digests = _mapping(
        policy.get("selection_digest_by_scope"), "selection_digest_by_scope"
    )
    if set(ordered_scopes) != set(_SCOPES) or set(digests) != set(_SCOPES):
        raise ValueError("paper suite scale selection scopes drift")
    normalized: dict[str, dict[str, tuple[str, ...]]] = {}
    for scope in _SCOPES:
        by_difficulty = _mapping(ordered_scopes[scope], scope)
        expected_difficulties = set(
            profile.factorization_case_count_by_scope[scope]
        )
        if set(by_difficulty) != expected_difficulties:
            raise ValueError("paper suite scale selection difficulties drift")
        normalized[scope] = {}
        for difficulty, expected_count in profile.factorization_case_count_by_scope[
            scope
        ].items():
            ids = _string_tuple(by_difficulty.get(difficulty), difficulty)
            if len(ids) != expected_count:
                raise ValueError("paper suite scale selection count drift")
            normalized[scope][difficulty] = ids
        expected_digest = digest_json(
            {
                "schema_version": "tokenshare.paper_suite_scale_selection.v1",
                "profile_source_digest": profile.profile_digest,
                "scope": scope,
                "ordered_case_ids_by_difficulty": {
                    difficulty: list(ids)
                    for difficulty, ids in normalized[scope].items()
                },
            }
        )
        if digests[scope] != expected_digest:
            raise ValueError("paper suite scale selection digest drift")
    if normalized["exp1_real_ai_feasibility"] != normalized["corpus"]:
        raise ValueError("Experiment 1 corpus binding drift")
    if normalized["exp2_real_ai_scalability"]["hard"] != normalized["corpus"][
        "hard"
    ][: len(normalized["exp2_real_ai_scalability"]["hard"])]:
        raise ValueError("Experiment 2 corpus nesting drift")
    if normalized["exp3_real_ai_fault_recovery"] != normalized[
        "exp4_real_ai_protocol_ablation"
    ]:
        raise ValueError("Experiment 3/4 shared selection drift")
    for difficulty, ids in normalized["exp3_real_ai_fault_recovery"].items():
        if ids != normalized["corpus"][difficulty][: len(ids)]:
            raise ValueError("Experiment 3/4 corpus nesting drift")

    canonical_policy, _ = build_paper_suite_scale_policy(
        profile=tracked_profile,
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        catalog_digest=catalog_digest,
        candidates_by_difficulty=_load_factorization_candidates(tracked_profile),
    )
    if policy != canonical_policy:
        raise ValueError("paper suite scale canonical candidate selection drift")
    return profile, normalized[experiment_id]


def _load_factorization_candidates(
    profile: PaperSuiteScaleProfile,
) -> dict[str, tuple[JsonObject, ...]]:
    catalog_path = Path(profile.factorization_catalog_path)
    if not catalog_path.is_absolute():
        catalog_path = _REPOSITORY_ROOT / catalog_path
    try:
        lines = catalog_path.read_text(encoding="utf-8").splitlines()
        cases = tuple(json.loads(line) for line in lines if line.strip())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to load approved Factorization candidates") from exc
    result: dict[str, list[JsonObject]] = {
        difficulty: [] for difficulty in PAPER_DIFFICULTIES
    }
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("approved Factorization candidate must be an object")
        difficulty = case.get("paper_difficulty")
        if difficulty not in result:
            raise ValueError("approved Factorization candidate difficulty drift")
        result[str(difficulty)].append(case)
    return {difficulty: tuple(items) for difficulty, items in result.items()}


def _source_path(path: Path) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(_REPOSITORY_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    result = tuple(_string(item, field_name) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _digest(value: Any, field_name: str) -> str:
    result = _string(value, field_name)
    if (
        len(result) != 71
        or not result.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in result[7:])
    ):
        raise ValueError(f"{field_name} must be a complete digest")
    return result
