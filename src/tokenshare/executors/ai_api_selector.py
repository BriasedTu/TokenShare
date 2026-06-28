"""Provider selection for the Phase 7 AI API executor."""

from __future__ import annotations

import random
from dataclasses import dataclass
from hashlib import sha256

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, AIAPIProviderEntry


@dataclass(frozen=True)
class AIProviderSelection:
    selection_policy_id: str
    eligible_entry_ids: list[str]
    selected_entry_id: str
    attempt_entry_ids: list[str]
    random_seed_material_digest: str
    selection_index: int

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": "phase7.ai_provider_selection.v1",
            "selection_policy_id": self.selection_policy_id,
            "eligible_entry_ids": list(self.eligible_entry_ids),
            "selected_entry_id": self.selected_entry_id,
            "attempt_entry_ids": list(self.attempt_entry_ids),
            "random_seed_material_digest": self.random_seed_material_digest,
            "selection_index": self.selection_index,
        }


def build_provider_selection(
    *,
    config: AIAPIExecutorConfig,
    request_id: str,
    environment_seed: int | None,
    require_json_mode: bool,
) -> AIProviderSelection:
    eligible = [
        entry
        for entry in config.entries
        if _entry_is_eligible(entry, require_json_mode=require_json_mode)
    ]
    if not eligible:
        raise ValueError("no eligible ai api entries")
    seed_material = f"{config.config_digest}|{request_id}|{environment_seed}"
    seed_digest = f"sha256:{sha256(seed_material.encode('utf-8')).hexdigest()}"
    rng = random.Random(seed_digest)
    ordered = list(eligible)
    rng.shuffle(ordered)
    max_attempts = int(config.defaults.get("max_provider_attempts", len(ordered)))
    ordered = ordered[: max(1, min(max_attempts, len(ordered)))]
    selected = ordered[0]
    eligible_ids = [entry.entry_id for entry in eligible]
    return AIProviderSelection(
        selection_policy_id=str(config.selection_policy["kind"]),
        eligible_entry_ids=eligible_ids,
        selected_entry_id=selected.entry_id,
        attempt_entry_ids=[entry.entry_id for entry in ordered],
        random_seed_material_digest=seed_digest,
        selection_index=eligible_ids.index(selected.entry_id),
    )


def entries_by_attempt_order(
    *,
    config: AIAPIExecutorConfig,
    selection: AIProviderSelection,
) -> list[AIAPIProviderEntry]:
    by_id = {entry.entry_id: entry for entry in config.entries}
    return [by_id[entry_id] for entry_id in selection.attempt_entry_ids]


def _entry_is_eligible(entry: AIAPIProviderEntry, *, require_json_mode: bool) -> bool:
    if not entry.enabled:
        return False
    if require_json_mode and not entry.supports_json_mode:
        return False
    try:
        entry.resolve_api_key()
    except ValueError:
        return False
    return True
