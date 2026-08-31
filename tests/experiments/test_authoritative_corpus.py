from __future__ import annotations

import json
from pathlib import Path

from verification.verify_authoritative_corpus import verify_all


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_authoritative_corpus_verifier_checks_complete_identity_sets() -> None:
    summary = verify_all(REPO_ROOT)

    assert summary["assets_checked"]["asset_count"] == 20
    assert summary["inventory"]["full_root_identities"] == {
        "count": 7017,
        "canonical_byte_length": 1448798,
        "items_sha256": (
            "ec5ca012faffc148be5eff65d43394b2d7861c666468e59841c716c88dea7c07"
        ),
    }
    assert summary["inventory"]["full_reference_identities"] == {
        "count": 106,
        "canonical_byte_length": 22055,
        "items_sha256": (
            "b537bd614ac9c86f2f864afb0e6a7f3c1fe22e38d772784c1fc8c0c914f43cdb"
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
    assert manifest["identity_expectations"]["full_root_identities"]["count"] == 7017
    assert manifest["identity_expectations"]["full_reference_identities"]["count"] == 106


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
