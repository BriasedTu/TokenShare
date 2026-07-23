from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.experiments.lean_catalog_audit import (
    LeanCatalogAuditCandidate,
    LeanCatalogAuditError,
    LeanCatalogPreflightEntry,
    audit_lean_catalog,
    build_lean_catalog_preflight_manifest,
    lean_preflight_entry_key,
    refresh_lean_catalog_preflight_manifest,
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


def test_incremental_audit_preserves_manifest_identity_when_all_evidence_is_reused() -> None:
    candidates = tuple(_candidate(index) for index in range(3))
    previous = _manifest_for_candidates(candidates)

    result = audit_lean_catalog(
        candidates=candidates,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: pytest.fail(
            f"reused evidence unexpectedly invoked checker for {candidate.entry_id}"
        ),
    )

    assert result.rechecked_entry_count == 0
    assert result.reused_entry_count == 3
    assert result.manifest.generated_at == previous.generated_at
    assert result.manifest.manifest_digest == previous.manifest_digest


def test_incremental_audit_rechecks_only_changed_entry() -> None:
    original = tuple(_candidate(index) for index in range(600))
    previous = _manifest_for_candidates(original)
    changed = list(original)
    changed[247] = _candidate(247, proof_source="by\n  exact True.intro")
    checked: list[str] = []

    result = audit_lean_catalog(
        candidates=changed,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: checked.append(candidate.entry_id)
        or candidate.accepted_entry(),
    )

    assert result.reused_entry_count == 599
    assert result.rechecked_entry_count == 1
    assert result.canary_entry_count == 0
    assert checked == ["direct:case_0247"]
    assert result.invalidated_by == ("entry_key_changed",)
    assert result.manifest.generated_at == "2026-07-22T00:00:01Z"
    assert result.manifest.manifest_digest != previous.manifest_digest


def test_force_all_rechecks_every_entry() -> None:
    candidates = tuple(_candidate(index) for index in range(600))
    previous = _manifest_for_candidates(candidates)
    checked: list[str] = []

    result = audit_lean_catalog(
        candidates=candidates,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: checked.append(candidate.entry_id)
        or candidate.accepted_entry(),
        force_all=True,
    )

    assert result.reused_entry_count == 0
    assert result.rechecked_entry_count == 600
    assert len(checked) == 600
    assert result.invalidated_by == ("force_all",)


def test_failed_refresh_preserves_last_good_manifest(tmp_path: Path) -> None:
    candidates = (_candidate(1),)
    previous = _manifest_for_candidates(candidates)
    manifest_path = tmp_path / "lean_checker_preflight.v1.json"
    manifest_path.write_text(
        __import__("json").dumps(previous.to_dict(), sort_keys=True),
        encoding="utf-8",
    )
    before = manifest_path.read_bytes()

    def rejecting_checker(
        candidate: LeanCatalogAuditCandidate,
    ) -> LeanCatalogPreflightEntry:
        return replace(
            candidate.accepted_entry(),
            status="rejected",
            proof_digest=None,
        )

    with pytest.raises(LeanCatalogAuditError, match="rejected"):
        refresh_lean_catalog_preflight_manifest(
            manifest_path=manifest_path,
            candidates=candidates,
            previous_manifest=previous,
            source_digests={"lean_catalog": DIGEST_A},
            generated_at="2026-07-22T00:00:01Z",
            checker=rejecting_checker,
            force_all=True,
        )

    assert manifest_path.read_bytes() == before


@pytest.mark.parametrize(
    ("field_name", "new_digest", "reason"),
    [
        ("environment_digest", DIGEST_A, "environment_digest_changed"),
        (
            "checker_implementation_digest",
            DIGEST_B,
            "checker_implementation_changed",
        ),
    ],
)
def test_shared_checker_inputs_invalidate_full_coverage(
    field_name: str,
    new_digest: str,
    reason: str,
) -> None:
    original = tuple(_candidate(index) for index in range(4))
    previous = _manifest_for_candidates(original)
    changed = tuple(replace(candidate, **{field_name: new_digest}) for candidate in original)

    result = audit_lean_catalog(
        candidates=changed,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: candidate.accepted_entry(),
    )

    assert result.rechecked_entry_count == 4
    assert result.reused_entry_count == 0
    assert result.invalidated_by == (reason,)


def test_changed_entry_also_rechecks_explicit_dependency_canary() -> None:
    original = tuple(_candidate(index) for index in range(4))
    previous = _manifest_for_candidates(original)
    changed = list(original)
    changed[2] = _candidate(2, proof_source="by\n  exact True.intro")

    result = audit_lean_catalog(
        candidates=changed,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: candidate.accepted_entry(),
        canary_entry_ids=("direct:case_0000",),
    )

    assert result.rechecked_entry_count == 2
    assert result.canary_entry_count == 1
    assert result.reused_entry_count == 2


def test_unknown_dependency_change_fails_safe_to_full_invalidation() -> None:
    candidates = tuple(_candidate(index) for index in range(4))
    previous = _manifest_for_candidates(candidates)

    result = audit_lean_catalog(
        candidates=candidates,
        previous_manifest=previous,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:01Z",
        checker=lambda candidate: candidate.accepted_entry(),
        global_invalidation_reasons=("unknown_dependency",),
    )

    assert result.rechecked_entry_count == 4
    assert result.reused_entry_count == 0
    assert result.invalidated_by == ("unknown_dependency",)


def _candidate(
    index: int,
    *,
    proof_source: str = "by\n  trivial",
) -> LeanCatalogAuditCandidate:
    case_id = f"case_{index:04d}"
    payload = LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case_id}",
        theorem_name=f"audit_case_{index:04d}",
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
    return LeanCatalogAuditCandidate(
        entry_id=f"direct:{case_id}",
        case_id=case_id,
        node_id=None,
        theorem_payload=payload,
        proof_source=proof_source,
        environment_digest=DIGEST_C,
        checker_implementation_digest=DIGEST_D,
        checker_mode="direct_proof",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )


def _manifest_for_candidates(
    candidates: tuple[LeanCatalogAuditCandidate, ...],
):
    return build_lean_catalog_preflight_manifest(
        entries=(candidate.accepted_entry() for candidate in candidates),
        source_digests={"lean_catalog": DIGEST_A},
        environment_digest=DIGEST_C,
        checker_implementation_digest=DIGEST_D,
        generated_at="2026-07-22T00:00:00Z",
    )
