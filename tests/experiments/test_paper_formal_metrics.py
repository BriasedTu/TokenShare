from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.experiments import paper_formal_metrics
from tokenshare.experiments.paper_formal_metrics import (
    publish_paper_formal_metric_drafts,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_metric_registry import (
    PaperMetricRegistry,
    load_paper_metric_registry,
)


def test_formal_metrics_delegates_without_rederiving_formula(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    received = []
    original = PaperMetricRegistry.project_all

    def recording_project_all(self, inputs, *, global_infra_invalid=False):
        received.append((self, inputs, global_infra_invalid))
        return original(
            self,
            inputs,
            global_infra_invalid=global_infra_invalid,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", recording_project_all)

    publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        global_infrastructure_valid=False,
        registry=registry,
        contract=contract,
    )

    assert received == [(registry, canonical_rows, True)]
    with pytest.raises(TypeError, match="registry must be PaperMetricRegistry"):
        publish_paper_formal_metric_drafts(
            tmp_path / "fake",
            canonical_rows,
            registry=object(),
            contract=contract,
        )
    wrong_contracts = (
        replace(contract, contract_id="wrong-contract"),
        replace(contract, contract_digest="sha256:" + "f" * 64),
        replace(contract, pipeline_profile_id="wrong-profile"),
    )
    for wrong_contract in wrong_contracts:
        with pytest.raises(ValueError, match="registry contract identity"):
            publish_paper_formal_metric_drafts(
                tmp_path / "wrong-contract",
                canonical_rows,
                registry=registry,
                contract=wrong_contract,
            )
    source = inspect.getsource(paper_formal_metrics)
    for forbidden in (
        "recompute_metric",
        "_rate(",
        "_quantile(",
    ):
        assert forbidden not in source


def test_retired_aliases_and_old_exp2_exp5_outputs_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    drafts = registry.project_all(canonical_rows)
    original = PaperMetricRegistry.project_all

    def missing_table(self, inputs, *, global_infra_invalid=False):
        return drafts[:-1]

    monkeypatch.setattr(PaperMetricRegistry, "project_all", missing_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "missing",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def extra_table(self, inputs, *, global_infra_invalid=False):
        return (*drafts, drafts[0])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", extra_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "extra",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def wrong_path(self, inputs, *, global_infra_invalid=False):
        return (replace(drafts[0], output_path="metrics/wrong.csv"), *drafts[1:])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", wrong_path)
    with pytest.raises(ValueError, match="output path contract drift"):
        publish_paper_formal_metric_drafts(
            tmp_path / "wrong-path",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", original)

    result = publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        registry=registry,
        contract=contract,
    )

    assert tuple(draft.table_id for draft in result.table_drafts) == tuple(
        table.table_id for table in contract.tables
    )
    published = "\n".join(ref["path"] for ref in result.output_refs)
    for retired in (
        "sensitivity",
        "all_runs",
        "views",
        "model_endpoint_comparison",
        "pairwise",
        "significance",
        "accepted_validity",
    ):
        assert retired not in published
    assert not (tmp_path / "metrics" / "exp2_sensitivity.csv").exists()
    assert not (tmp_path / "metrics" / "paper_table_model_endpoint_comparison.csv").exists()
