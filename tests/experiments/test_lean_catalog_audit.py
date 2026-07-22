from __future__ import annotations

from copy import deepcopy

import pytest

from tokenshare.experiments.lean_catalog_audit import (
    LeanCatalogPreflightEntry,
    build_lean_catalog_preflight_manifest,
    lean_preflight_entry_key,
    validate_lean_catalog_preflight_manifest,
)
from tokenshare.plugins.lean_proof.checker import render_lean_source
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _key_inputs() -> dict:
    return {
        "generated_source_digest": DIGEST_A,
        "oracle_proof_digest": DIGEST_B,
        "environment_digest": DIGEST_C,
        "checker_implementation_digest": DIGEST_D,
        "checker_mode": "direct_proof",
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }


def _entry(*, entry_id: str = "direct:case_1", status: str = "accepted") -> LeanCatalogPreflightEntry:
    inputs = _key_inputs()
    return LeanCatalogPreflightEntry(
        entry_id=entry_id,
        case_id="case_1",
        node_id=None,
        checker_mode="direct_proof",
        generated_source_digest=inputs["generated_source_digest"],
        oracle_proof_digest=inputs["oracle_proof_digest"],
        environment_digest=inputs["environment_digest"],
        checker_implementation_digest=inputs["checker_implementation_digest"],
        resource_limits=inputs["resource_limits"],
        entry_key=lean_preflight_entry_key(**inputs),
        normalized_theorem_digest=DIGEST_A,
        proof_digest=DIGEST_B if status == "accepted" else None,
        status=status,
    )


def _manifest_dict() -> dict:
    manifest = build_lean_catalog_preflight_manifest(
        entries=(_entry(),),
        source_digests={"benchmarks/paper/lean_catalog.v1.jsonl": DIGEST_A},
        environment_digest=DIGEST_C,
        checker_implementation_digest=DIGEST_D,
        generated_at="2026-07-22T00:00:00Z",
    )
    return manifest.to_dict()


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("generated_source_digest", DIGEST_B),
        ("oracle_proof_digest", DIGEST_C),
        ("environment_digest", DIGEST_D),
        ("checker_implementation_digest", DIGEST_A),
        ("checker_mode", "child_proof"),
        ("resource_limits", {"timeout_seconds": 31, "max_output_bytes": 65536}),
    ],
)
def test_preflight_entry_key_changes_with_every_checker_input(
    field_name: str,
    new_value: object,
) -> None:
    original = _key_inputs()
    changed = {**original, field_name: new_value}

    assert lean_preflight_entry_key(**original) != lean_preflight_entry_key(**changed)


def test_manifest_round_trip_validates_complete_accepted_coverage() -> None:
    body = _manifest_dict()

    validated = validate_lean_catalog_preflight_manifest(
        body,
        expected_entry_ids={"direct:case_1"},
    )

    assert validated.total_entry_count == 1
    assert validated.status == "passed"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "rejected"])
def test_manifest_rejects_invalid_coverage(mutation: str) -> None:
    body = _manifest_dict()
    if mutation == "missing":
        body["entries"] = []
        body["total_entry_count"] = 0
    elif mutation == "duplicate":
        body["entries"].append(deepcopy(body["entries"][0]))
        body["total_entry_count"] = 2
    else:
        body["entries"][0]["status"] = "rejected"
        body["entries"][0]["proof_digest"] = None

    with pytest.raises(ValueError, match="coverage|accepted"):
        validate_lean_catalog_preflight_manifest(
            body,
            expected_entry_ids={"direct:case_1"},
        )


def test_manifest_rejects_self_digest_tampering() -> None:
    body = _manifest_dict()
    body["manifest_digest"] = DIGEST_A

    with pytest.raises(ValueError, match="manifest_digest"):
        validate_lean_catalog_preflight_manifest(
            body,
            expected_entry_ids={"direct:case_1"},
        )


def test_render_lean_source_is_public_and_deterministic() -> None:
    payload = LeanTheoremPayload(
        theorem_id="lean_theorem:test",
        theorem_name="public_render_test",
        imports=["Init"],
        namespace="TokenShareAudit",
        open_namespaces=[],
        options={},
        parameters_source="",
        statement_source="True",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={},
        decomposition_policy={
            "policy_id": "test",
            "allowed_rules": ["leaf_close"],
            "max_depth": 0,
            "max_children": 0,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )

    assert render_lean_source(payload, "by\n  trivial") == (
        "import Init\n"
        "set_option autoImplicit false\n"
        "namespace TokenShareAudit\n"
        "theorem public_render_test : True := by\n"
        "  trivial\n"
        "end TokenShareAudit\n"
    )
