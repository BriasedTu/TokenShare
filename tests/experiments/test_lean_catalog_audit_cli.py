from __future__ import annotations

import json

import pytest

import tokenshare.experiments.lean_catalog_audit as audit_module
from tokenshare.experiments.lean_catalog_audit import (
    LeanCatalogAuditCandidate,
    audit_lean_catalog,
)
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@pytest.mark.parametrize(
    ("argv", "expected_refresh", "expected_force_all"),
    [
        (["--verify"], False, False),
        (["--verify", "--force-all"], False, True),
        (["--refresh", "--force-all"], True, True),
    ],
)
def test_cli_supports_verify_refresh_and_force_all(
    argv: list[str],
    expected_refresh: bool,
    expected_force_all: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[bool, bool]] = []
    result = _result()

    def fake_run(*, refresh: bool, force_all: bool):
        calls.append((refresh, force_all))
        return result

    monkeypatch.setattr(audit_module, "_run_default_catalog_audit", fake_run)

    assert audit_module.main(argv) == 0

    assert calls == [(expected_refresh, expected_force_all)]
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "canary_entry_count": 0,
        "invalidated_by": ["missing_previous_manifest"],
        "manifest_digest": result.manifest.manifest_digest,
        "provider_calls_made": 0,
        "rechecked_entry_count": 1,
        "reused_entry_count": 0,
        "status": "passed",
        "total_entry_count": 1,
    }


def _result():
    candidate = LeanCatalogAuditCandidate(
        entry_id="direct:cli_case",
        case_id="cli_case",
        node_id=None,
        theorem_payload=LeanTheoremPayload(
            theorem_id="lean_theorem:cli_case",
            theorem_name="cli_case",
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
        ),
        proof_source="by\n  trivial",
        environment_digest=DIGEST_A,
        checker_implementation_digest=DIGEST_B,
        checker_mode="direct_proof",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
    return audit_lean_catalog(
        candidates=(candidate,),
        previous_manifest=None,
        source_digests={"lean_catalog": DIGEST_A},
        generated_at="2026-07-22T00:00:00Z",
        checker=lambda item: item.accepted_entry(),
    )
