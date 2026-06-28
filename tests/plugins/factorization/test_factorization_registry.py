import json
from dataclasses import replace

import pytest

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.schemas import (
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    PLUGIN_ID,
    PLUGIN_VERSION,
)
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.storage.artifacts import ArtifactStore


def test_factorization_registry_freeze_includes_single_plugin_descriptor(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    plugin_registry = PluginRegistry()
    plugin_registry.register(build_factorization_plugin_descriptor())

    snapshot = plugin_registry.freeze(
        task_id="task_factorization_21",
        registry_snapshot_id="registry_snapshot_factorization_1",
        executor_registry=ExecutorRegistry(),
        artifact_store=store,
        frozen_at="2026-06-27T00:00:00Z",
    )

    assert snapshot.plugin_entries == [
        {
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "descriptor_ref": snapshot.plugin_entries[0]["descriptor_ref"],
            "descriptor_digest": snapshot.plugin_entries[0]["descriptor_digest"],
            "supported_task_types": [
                "root",
                "factor_integer",
                "factor_search_range",
                "factorization_merge",
            ],
            "split_strategy_ids": [CANDIDATE_RANGE_PARTITION_STRATEGY_ID],
        }
    ]
    assert snapshot.executor_entries == []

    descriptor_ref = ArtifactRef.from_dict(snapshot.plugin_entries[0]["descriptor_ref"])
    assert store.verify(descriptor_ref)
    frozen_descriptor = json.loads(store.read_bytes(descriptor_ref).decode("utf-8"))

    assert frozen_descriptor["plugin_id"] == PLUGIN_ID
    assert frozen_descriptor["plugin_version"] == PLUGIN_VERSION
    assert set(frozen_descriptor["split_strategies"]) == {CANDIDATE_RANGE_PARTITION_STRATEGY_ID}
    frozen_descriptor_text = json.dumps(frozen_descriptor, ensure_ascii=False, sort_keys=True)
    assert "factorization_continuation" not in frozen_descriptor_text
    assert "recursive_factorization" not in frozen_descriptor_text


def test_factorization_descriptor_declares_recursive_policy_without_second_plugin() -> None:
    descriptor = build_factorization_plugin_descriptor().to_dict()
    metadata = descriptor["metadata"]

    assert metadata["plugin_identity"] == {
        "main_tdd_section": "14.1",
        "role": "integer_factorization_plugin",
        "is_main_tdd_integer_factorization_plugin": True,
    }
    assert metadata["recursive_policy"]["same_plugin_for_recursive_factor_integer"] is True
    assert metadata["recursive_policy"]["continuation_plugin_allowed"] is False
    assert metadata["recursive_policy_details"] == {
        "recursive_unit_type": "factor_integer",
        "continuation_plugin_id": None,
        "second_factorization_plugin_allowed": False,
        "first_version_recursive_resolution": "canonical_output_driven_same_plugin_future_slice",
        "composite_cofactor_resolution": "limited_first_slice_nontrivial_factor_found_only",
    }
    assert metadata["first_slice_boundary"] == {
        "early_success": "not_in_first_slice",
        "sibling_pruning": "not_in_first_slice",
        "composite_cofactor_recursive_resolution": (
            "limited_first_slice_nontrivial_factor_found_only"
        ),
    }


def test_factorization_registry_rejects_second_factorization_like_descriptor() -> None:
    plugin_registry = PluginRegistry()
    plugin_registry.register(build_factorization_plugin_descriptor())
    continuation_descriptor = replace(
        build_factorization_plugin_descriptor(),
        plugin_id="factorization_continuation",
        metadata={"purpose": "forbidden second factorization plugin"},
    )

    with pytest.raises(ValueError, match="exclusive task type"):
        plugin_registry.register(continuation_descriptor)
