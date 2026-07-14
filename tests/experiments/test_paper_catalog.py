import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_catalog import load_paper_catalogs


def test_paper_catalogs_load_30_factorization_and_30_lean_cases() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    body = manifest.to_dict()

    assert body["schema_version"] == "tokenshare.paper_input_catalog_manifest.v1"
    assert body["case_count"] == 60
    assert body["domain_counts"] == {"factorization": 30, "lean_proof": 30}
    assert body["difficulty_counts"] == {
        "factorization": {"easy": 10, "medium": 10, "hard": 10},
        "lean_proof": {"easy": 10, "medium": 10, "hard": 10},
    }
    assert body["oracle_validation_status"] == "passed"
    assert body["lean_preflight_status"] == "passed"
    assert body["lean_preflight_summary"]["checked_case_count"] == 30
    assert body["lean_preflight_summary"]["accepted_case_count"] == 30
    assert body["lean_preflight_summary"]["environment_digest"].startswith("sha256:")
    assert body["catalog_digest"].startswith("sha256:")
    assert manifest.catalog_digest == load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    ).catalog_digest


def test_factorization_catalog_preflight_rejects_wrong_oracle_product(tmp_path: Path) -> None:
    factorization_path = tmp_path / "bad_factorization.jsonl"
    lean_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    bad_case = {
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "case_id": "bad_factor",
        "target_n": "899",
        "oracle_prime_factors": [{"prime": "13", "exponent": 1}, {"prime": "19", "exponent": 1}],
        "candidate_start": "2",
        "candidate_end": "17",
        "candidate_divisor_count": 16,
        "factor_position_quantile": "middle",
        "difficulty": "easy",
        "split_params": {"requested_child_count": 4},
        "source_seed": 1,
    }
    factorization_path.write_text(json.dumps(bad_case) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="oracle"):
        load_paper_catalogs(factorization_path=factorization_path, lean_path=lean_path)


def test_catalog_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    source_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    rows = source_path.read_text(encoding="utf-8").splitlines()
    duplicate_path = tmp_path / "duplicate_lean.jsonl"
    duplicate_path.write_text("\n".join([rows[0], rows[0]]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case_id"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=duplicate_path,
        )


def test_lean_catalog_preflight_rejects_bad_embedded_oracle_proof(tmp_path: Path) -> None:
    source_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    rows = source_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["oracle_proof_ref"] = {
        **first["oracle_proof_ref"],
        "proof_source": "by\n  exact False.elim (by contradiction)",
    }
    bad_path = tmp_path / "bad_lean_catalog.jsonl"
    bad_path.write_text(
        json.dumps(first, ensure_ascii=False, sort_keys=True)
        + "\n"
        + "\n".join(rows[1:])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Lean preflight rejected case lean_easy_01"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=bad_path,
        )


def test_catalog_case_lookup_rejects_unknown_domain_or_difficulty() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    with pytest.raises(ValueError, match="domain"):
        manifest.cases_for(domain="lean-proof", difficulty="easy")
    with pytest.raises(ValueError, match="difficulty"):
        manifest.cases_for(domain="lean_proof", difficulty="all")
