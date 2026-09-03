from __future__ import annotations

import json
from pathlib import Path

from verification.verify_authoritative_corpus import verify_all


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_authoritative_corpus_verifier_checks_complete_identity_sets() -> None:
    summary = verify_all(REPO_ROOT)

    assert summary["assets_checked"]["asset_count"] == 20
    assert summary["inventory"]["full_root_identities"] == {
        "count": 6912,
        "canonical_byte_length": 1427265,
        "items_sha256": (
            "600d29b146d7324ae09dd2ce2c227d64ad90ff8a0ba5eb9db0eab98eae1b352e"
        ),
    }
    assert summary["inventory"]["full_reference_identities"] == {
        "count": 104,
        "canonical_byte_length": 21647,
        "items_sha256": (
            "0478c5eaa96a35571f1c942da84e090a58af3323dc7ed961bbd603e4a812750e"
        ),
    }


def test_authoritative_manifest_records_public_assets_without_sampling() -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )

    assets = manifest["assets"]
    assert len(assets) == 20
    assert all(
        item["public_path"].startswith(("benchmarks/experiments/", "configs/experiments/"))
        for item in assets
    )
    root_identities = manifest["identity_expectations"]["full_root_identities"]
    reference_identities = manifest["identity_expectations"][
        "full_reference_identities"
    ]
    assert root_identities["count"] == len(root_identities["items"]) == 6912
    assert reference_identities["count"] == len(reference_identities["items"]) == 104


def test_semantic_authority_keeps_frozen_logical_keys_with_public_physical_paths() -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    sidecar = json.loads(
        (
            REPO_ROOT
            / "benchmarks/experiments/lean_environment_semantic_authority.v1.json"
        ).read_text(encoding="utf-8")
    )

    mapping = manifest["semantic_authority"]["logical_to_physical_paths"]
    assert "benchmarks/paper/lean_catalog.v1.jsonl" in sidecar["authority"]["raw_files"]
    assert mapping["benchmarks/paper/lean_catalog.v1.jsonl"] == (
        "benchmarks/experiments/lean_catalog.v1.jsonl"
    )
    assert "fixtures/lean_proof_project/TokenShare/Helper.lean" in (
        sidecar["semantic_projection"]["environment"]["files"]
    )
    assert mapping["fixtures/lean_proof_project/TokenShare/Helper.lean"] == (
        "benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Helper.lean"
    )
