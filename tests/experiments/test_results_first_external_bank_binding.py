from __future__ import annotations

from types import SimpleNamespace

import pytest

from tokenshare.experiments import run_paper_pipeline as pipeline


def _authority(*, budget_digest: str = "sha256:" + "b" * 64) -> object:
    return SimpleNamespace(
        bundle=SimpleNamespace(
            profile_digest="sha256:" + "p" * 64,
            full_budget=SimpleNamespace(budget_digest=budget_digest),
            inventory_digest="sha256:" + "i" * 64,
        )
    )


def test_external_bank_binding_accepts_exact_scope_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_manifest = "sha256:" + "m" * 64
    expected_budget = "sha256:" + "b" * 64
    resolver = SimpleNamespace(
        index=SimpleNamespace(
        manifest=SimpleNamespace(
            manifest_digest=expected_manifest,
            terminal_entry_count=1970,
            profile_digest="sha256:" + "p" * 64,
            budget_digest=expected_budget,
            inventory_digest="sha256:" + "i" * 64,
            )
        )
    )
    monkeypatch.setattr(
        pipeline.ExternalBankResolverBinding,
        "open",
        lambda self: resolver,
    )

    pipeline.validate_results_first_external_bank_binding(
        external_bank_root="bank",
            preparation={
                "source_bank_manifest_digest": expected_manifest,
                "source_bank_terminal_entry_count": 1970,
            },
            authority=_authority(),
            selection="full_exp1_exp3_exp5",
    )


def test_external_bank_binding_rejects_manifest_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = SimpleNamespace(
        index=SimpleNamespace(
            manifest=SimpleNamespace(
                manifest_digest="sha256:" + "x" * 64,
                terminal_entry_count=1970,
                profile_digest="sha256:" + "p" * 64,
                budget_digest="sha256:" + "b" * 64,
                inventory_digest="sha256:" + "i" * 64,
            )
        )
    )
    monkeypatch.setattr(
        pipeline.ExternalBankResolverBinding,
        "open",
        lambda self: resolver,
    )

    with pytest.raises(ValueError, match="external response bank manifest"):
        pipeline.validate_results_first_external_bank_binding(
            external_bank_root="bank",
            preparation={
                "source_bank_manifest_digest": "sha256:" + "m" * 64,
                "source_bank_terminal_entry_count": 1970,
            },
            authority=_authority(),
            selection="full_exp1_exp3_exp5",
        )
