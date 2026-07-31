"""Tracked Factorization sampling profiles for paper Experiment 3/4."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import JsonObject, digest_json


FACTORIZATION_SAMPLING_PROFILE_SCHEMA_VERSION = (
    "tokenshare.paper_factorization_sampling_profile.v1"
)
FACTORIZATION_SELECTION_RULE_ID = (
    "stable_hash_by_difficulty_not_catalog_prefix"
)
FACTORIZATION_SELECTION_SCORE_SCHEMA_VERSION = (
    "tokenshare.paper_factorization_stratified_score.v2"
)
FACTORIZATION_SELECTION_POLICY_SCHEMA_VERSION = (
    "tokenshare.paper_factorization_stratified_selection.v2"
)
FACTORIZATION_PROFILE_BINDING_SCHEMA_VERSION = (
    "tokenshare.paper_factorization_sampling_profile_binding.v1"
)
FACTOR_PAPER_DIFFICULTIES = ("easy", "medium", "hard")
FACTOR_SAMPLING_EXPERIMENT_IDS = (
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
_PROFILE_SOURCE_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "approved_catalog_versions",
        "experiment_ids",
        "selection_rule",
        "difficulty_order",
        "case_count_by_difficulty",
    }
)
_PROFILE_DECLARED_FIELDS = _PROFILE_SOURCE_FIELDS | {"profile_digest"}
_SELECTION_RULE_FIELDS = frozenset(
    {"rule_id", "score_schema_version", "seed"}
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "profile_binding",
        "difficulty_order",
        "case_count_by_difficulty",
        "ordered_case_ids_by_difficulty",
        "selection_digest",
    }
)
_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "profile_source_path",
        "profile_source_digest",
        "profile_body",
        "catalog_version",
        "experiment_ids",
        "binding_digest",
    }
)


@dataclass(frozen=True, kw_only=True)
class FactorizationSamplingProfile:
    profile_id: str
    approved_catalog_versions: tuple[str, ...]
    experiment_ids: tuple[str, ...]
    selection_rule_id: str
    score_schema_version: str
    seed: int
    difficulty_order: tuple[str, ...]
    case_count_by_difficulty: Mapping[str, int]
    profile_digest: str
    source_path: str
    schema_version: str = FACTORIZATION_SAMPLING_PROFILE_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source_path: str = "<memory>",
    ) -> "FactorizationSamplingProfile":
        unknown_fields = set(value) - _PROFILE_DECLARED_FIELDS
        if unknown_fields:
            raise ValueError("Factorization sampling profile contains unknown fields")
        body = dict(value)
        declared_digest = body.pop("profile_digest", None)
        profile_digest = digest_json(body)
        if declared_digest is not None and declared_digest != profile_digest:
            raise ValueError("Factorization sampling profile digest drift")
        if body.get("schema_version") != FACTORIZATION_SAMPLING_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported Factorization sampling profile schema")

        profile_id = _non_empty_string(body.get("profile_id"), "profile_id")
        approved_catalog_versions = _non_empty_string_tuple(
            body.get("approved_catalog_versions"),
            "approved_catalog_versions",
        )
        experiment_ids = _non_empty_string_tuple(
            body.get("experiment_ids"),
            "experiment_ids",
        )
        if experiment_ids != FACTOR_SAMPLING_EXPERIMENT_IDS:
            raise ValueError("Factorization sampling profile experiment binding drift")

        selection_rule = _mapping(body.get("selection_rule"), "selection_rule")
        if set(selection_rule) != _SELECTION_RULE_FIELDS:
            raise ValueError(
                "Factorization sampling selection_rule contains unknown fields"
            )
        selection_rule_id = _non_empty_string(
            selection_rule.get("rule_id"),
            "selection_rule.rule_id",
        )
        if selection_rule_id != FACTORIZATION_SELECTION_RULE_ID:
            raise ValueError("unsupported Factorization sampling selection rule")
        score_schema_version = _non_empty_string(
            selection_rule.get("score_schema_version"),
            "selection_rule.score_schema_version",
        )
        if score_schema_version != FACTORIZATION_SELECTION_SCORE_SCHEMA_VERSION:
            raise ValueError("unsupported Factorization sampling score schema")
        seed = selection_rule.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("selection_rule.seed must be a non-negative integer")

        difficulty_order = _non_empty_string_tuple(
            body.get("difficulty_order"),
            "difficulty_order",
        )
        if set(difficulty_order) != set(FACTOR_PAPER_DIFFICULTIES) or len(
            difficulty_order
        ) != len(FACTOR_PAPER_DIFFICULTIES):
            raise ValueError("Factorization sampling difficulty order drift")
        raw_counts = _mapping(
            body.get("case_count_by_difficulty"),
            "case_count_by_difficulty",
        )
        if set(raw_counts) != set(difficulty_order):
            raise ValueError("Factorization sampling difficulty counts drift")
        counts: dict[str, int] = {}
        for difficulty in difficulty_order:
            count = raw_counts[difficulty]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(
                    "Factorization sampling counts must be positive integers"
                )
            counts[difficulty] = count

        return cls(
            profile_id=profile_id,
            approved_catalog_versions=approved_catalog_versions,
            experiment_ids=experiment_ids,
            selection_rule_id=selection_rule_id,
            score_schema_version=score_schema_version,
            seed=seed,
            difficulty_order=difficulty_order,
            case_count_by_difficulty=counts,
            profile_digest=profile_digest,
            source_path=_non_empty_string(source_path, "source_path"),
        )

    @property
    def total_case_count(self) -> int:
        return sum(self.case_count_by_difficulty.values())

    def approves(self, *, catalog_version: str, experiment_id: str) -> bool:
        return (
            catalog_version in self.approved_catalog_versions
            and experiment_id in self.experiment_ids
        )

    def to_dict(self) -> JsonObject:
        body = self.source_body()
        body["profile_digest"] = self.profile_digest
        body["profile_source_path"] = self.source_path
        return body

    def source_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "approved_catalog_versions": list(self.approved_catalog_versions),
            "experiment_ids": list(self.experiment_ids),
            "selection_rule": {
                "rule_id": self.selection_rule_id,
                "score_schema_version": self.score_schema_version,
                "seed": self.seed,
            },
            "difficulty_order": list(self.difficulty_order),
            "case_count_by_difficulty": dict(self.case_count_by_difficulty),
        }

    def select_cases_by_difficulty(
        self,
        candidates_by_difficulty: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> dict[str, tuple[Mapping[str, Any], ...]]:
        selected: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for difficulty in self.difficulty_order:
            candidates = tuple(candidates_by_difficulty.get(difficulty, ()))
            required_count = self.case_count_by_difficulty[difficulty]
            if len(candidates) < required_count:
                raise ValueError(
                    "Factorization sampling profile requires more candidate cases"
                )
            case_ids = tuple(str(case.get("case_id") or "") for case in candidates)
            if any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(
                case_ids
            ):
                raise ValueError(
                    "Factorization sampling candidate IDs must be unique and non-empty"
                )
            ranked = sorted(
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
            selected[difficulty] = tuple(ranked[:required_count])
        return selected


def load_factorization_sampling_profile(
    path: str | Path,
) -> FactorizationSamplingProfile:
    profile_path = Path(path)
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to load Factorization sampling profile") from exc
    if not isinstance(value, dict):
        raise ValueError("Factorization sampling profile must be a JSON object")
    return FactorizationSamplingProfile.from_mapping(
        value,
        source_path=_normalized_source_path(profile_path),
    )


def build_factorization_selection_policy(
    *,
    profile: FactorizationSamplingProfile,
    catalog_version: str,
    candidates_by_difficulty: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[JsonObject, dict[str, tuple[Mapping[str, Any], ...]]]:
    for experiment_id in FACTOR_SAMPLING_EXPERIMENT_IDS:
        if not profile.approves(
            catalog_version=catalog_version,
            experiment_id=experiment_id,
        ):
            raise ValueError(
                "Factorization sampling profile does not approve catalog/experiment"
            )
    selected = profile.select_cases_by_difficulty(candidates_by_difficulty)
    binding: JsonObject = {
        "schema_version": FACTORIZATION_PROFILE_BINDING_SCHEMA_VERSION,
        "profile_source_path": profile.source_path,
        "profile_source_digest": profile.profile_digest,
        "profile_body": profile.source_body(),
        "catalog_version": catalog_version,
        "experiment_ids": list(profile.experiment_ids),
    }
    binding["binding_digest"] = digest_json(binding)
    policy: JsonObject = {
        "schema_version": FACTORIZATION_SELECTION_POLICY_SCHEMA_VERSION,
        "profile_binding": binding,
        "difficulty_order": list(profile.difficulty_order),
        "case_count_by_difficulty": dict(profile.case_count_by_difficulty),
        "ordered_case_ids_by_difficulty": {
            difficulty: [str(case["case_id"]) for case in selected[difficulty]]
            for difficulty in profile.difficulty_order
        },
    }
    policy["selection_digest"] = digest_json(policy)
    return policy, selected


def validate_factorization_selection_policy(
    value: Any,
    *,
    catalog_version: str,
    experiment_id: str,
    expected_case_ids_by_difficulty: Mapping[str, Sequence[str]],
) -> FactorizationSamplingProfile:
    policy = _mapping(value, "factorization_selection_policy")
    if set(policy) != _POLICY_FIELDS:
        raise ValueError("Factorization selection policy fields drift")
    if policy.get("schema_version") != FACTORIZATION_SELECTION_POLICY_SCHEMA_VERSION:
        raise ValueError("Factorization selection policy schema drift")
    policy_body = dict(policy)
    selection_digest = policy_body.pop("selection_digest", None)
    if selection_digest != digest_json(policy_body):
        raise ValueError("Factorization selection policy digest drift")

    binding = _mapping(policy.get("profile_binding"), "profile_binding")
    if set(binding) != _BINDING_FIELDS:
        raise ValueError("Factorization sampling profile binding fields drift")
    if binding.get("schema_version") != FACTORIZATION_PROFILE_BINDING_SCHEMA_VERSION:
        raise ValueError("Factorization sampling profile binding schema drift")
    binding_body = dict(binding)
    binding_digest = binding_body.pop("binding_digest", None)
    if binding_digest != digest_json(binding_body):
        raise ValueError("Factorization sampling profile binding digest drift")
    profile = FactorizationSamplingProfile.from_mapping(
        _mapping(binding.get("profile_body"), "profile_binding.profile_body"),
        source_path=_non_empty_string(
            binding.get("profile_source_path"),
            "profile_binding.profile_source_path",
        ),
    )
    if binding.get("profile_source_digest") != profile.profile_digest:
        raise ValueError("Factorization sampling profile source digest drift")
    if binding.get("catalog_version") != catalog_version:
        raise ValueError("Factorization sampling profile catalog binding drift")
    if tuple(binding.get("experiment_ids") or ()) != profile.experiment_ids:
        raise ValueError("Factorization sampling profile experiment binding drift")
    if not profile.approves(
        catalog_version=catalog_version,
        experiment_id=experiment_id,
    ):
        raise ValueError(
            "Factorization sampling profile does not approve catalog/experiment"
        )
    if tuple(policy.get("difficulty_order") or ()) != profile.difficulty_order:
        raise ValueError("Factorization selection difficulty order drift")
    if dict(_mapping(policy.get("case_count_by_difficulty"), "case counts")) != dict(
        profile.case_count_by_difficulty
    ):
        raise ValueError("Factorization selection difficulty counts drift")

    ordered = _mapping(
        policy.get("ordered_case_ids_by_difficulty"),
        "ordered_case_ids_by_difficulty",
    )
    if set(ordered) != set(profile.difficulty_order):
        raise ValueError("Factorization selection difficulty slices drift")
    for difficulty in profile.difficulty_order:
        policy_ids = _case_id_tuple(ordered[difficulty])
        expected_ids = _case_id_tuple(expected_case_ids_by_difficulty[difficulty])
        if len(policy_ids) != profile.case_count_by_difficulty[difficulty]:
            raise ValueError("Factorization selection case count drift")
        if policy_ids != expected_ids:
            raise ValueError("Factorization selection case IDs drift")
    return profile


def _normalized_source_path(path: Path) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(_REPOSITORY_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _case_id_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Factorization selection case IDs must be a list")
    normalized = tuple(_non_empty_string(item, "case_id") for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Factorization selection case IDs must be unique")
    return normalized


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _non_empty_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    normalized = tuple(_non_empty_string(item, field_name) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized
