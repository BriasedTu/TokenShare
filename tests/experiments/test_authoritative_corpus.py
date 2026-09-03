from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from verification import verify_authoritative_corpus as authoritative_corpus
from verification.verify_authoritative_corpus import verify_all


REPO_ROOT = Path(__file__).resolve().parents[2]


def _refresh_selection_sha(section: dict[str, object]) -> None:
    encoded = json.dumps(
        section["ordered_case_ids"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    section["ordered_case_ids_sha256"] = sha256(encoded).hexdigest()


def test_public_verifier_rejects_representative_exp3_selection_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    selection = manifest["profiles"]["representative"]["selection_case_ids"][
        "exp3"
    ]
    selection["ordered_case_ids"][0] = "factor_v2_hard_138"
    _refresh_selection_sha(selection)
    manifest_path = tmp_path / "manifest.v1.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(authoritative_corpus, "MANIFEST_PATH", str(manifest_path))

    with pytest.raises(
        ValueError,
        match="representative exp3 inventory case IDs mismatch",
    ):
        authoritative_corpus.verify_all(REPO_ROOT)


def test_public_verifier_rejects_representative_exp5_selection_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    selection = manifest["profiles"]["representative"]["selection_case_ids"][
        "exp5"
    ]
    selection["ordered_case_ids"][0] = "factor_v2_hard_138"
    _refresh_selection_sha(selection)
    manifest_path = tmp_path / "manifest.v1.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(authoritative_corpus, "MANIFEST_PATH", str(manifest_path))

    with pytest.raises(
        ValueError,
        match="representative exp5 inventory case IDs mismatch",
    ):
        authoritative_corpus.verify_all(REPO_ROOT)


def test_inventory_build_case_ids_reject_duplicates_in_profile_verification() -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile_manifest = manifest["profiles"]["full"]
    exp3_factorization = profile_manifest["inventory_build_case_ids"][
        "exp3_factorization"
    ]
    exp3_factorization[1] = exp3_factorization[0]
    with pytest.raises(
        ValueError,
        match="full exp3_factorization inventory build duplicates",
    ):
        authoritative_corpus._verify_profile_inventory(REPO_ROOT, manifest)


def test_inventory_build_case_ids_reject_missing_catalog_cases_precisely() -> None:
    manifest = json.loads(
        (REPO_ROOT / "benchmarks/experiments/manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile_manifest = manifest["profiles"]["full"]
    profile_manifest["inventory_build_case_ids"]["exp3_factorization"][0] = (
        "factor_v2_missing_case"
    )

    with pytest.raises(
        ValueError,
        match="full exp3_factorization inventory build missing cases",
    ):
        authoritative_corpus._verify_profile_inventory(REPO_ROOT, manifest)


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
