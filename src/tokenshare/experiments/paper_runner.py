"""Minimal paper experiment condition expansion for plan-only runs."""

from __future__ import annotations

from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_models import PaperExperimentCondition


EXPERIMENT_ALIASES = {
    "exp1": "exp1_real_ai_feasibility",
    "exp2": "exp2_real_ai_scalability",
    "exp3": "exp3_real_ai_fault_recovery",
    "exp4": "exp4_real_ai_protocol_ablation",
    "exp5": "exp5_real_ai_model_policy",
}


def normalize_experiment_ids(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        normalized.append(EXPERIMENT_ALIASES.get(item, item))
    return tuple(dict.fromkeys(normalized))


def expand_plan_conditions(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    experiment_ids: tuple[str, ...],
    worker_levels: tuple[int, ...],
    repeats: int,
    seed_family: tuple[int, ...],
) -> tuple[PaperExperimentCondition, ...]:
    conditions: list[PaperExperimentCondition] = []
    for experiment_id in experiment_ids:
        if experiment_id != "exp1_real_ai_feasibility":
            continue
        worker_count = worker_levels[0] if worker_levels else 10
        for repeat_index in range(repeats):
            seed = seed_family[repeat_index % len(seed_family)] if seed_family else repeat_index
            for domain in ("factorization", "lean_proof"):
                for difficulty in ("easy", "medium", "hard"):
                    conditions.append(
                        PaperExperimentCondition(
                            experiment_id=experiment_id,
                            condition_id=(
                                f"exp1_{domain}_{difficulty}_w{worker_count}_r{repeat_index}"
                            ),
                            domain=domain,
                            difficulty=difficulty,
                            worker_count=worker_count,
                            fault_type="none",
                            fault_rate=0.0,
                            ablation_mode="FULL",
                            model_policy="strong_only",
                            repeat_id=repeat_index,
                            seed=seed,
                            catalog_digest=catalog_manifest.catalog_digest,
                        )
                    )
    return tuple(conditions)
