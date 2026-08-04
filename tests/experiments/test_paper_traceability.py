from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_formal_runner as formal_runner
from tokenshare.executors.response_bank import ResponseBankResolver
from tokenshare.experiments.historical_real_factorization_single_leaf import (
    classify_20260731_exp34_negative,
)
from tokenshare.experiments.paper_metric_contract import MetricObservationBundle
from tokenshare.experiments.paper_exp1_metrics import (
    Exp1ActualProviderAttemptFacts,
    Exp1HydratedDirectRow,
)
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_traceability import (
    L1_SYNTHETIC_COMPONENT,
    L4_ARTIFACT_ROOT,
    TraceabilityBlockedError,
    recompute_cell_lineage,
    verify_external_source_locators,
    _CURRENT_PROVIDER_ROLES,
    _walk_instances,
)
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    ExternalBankObjectLocator,
)

from tests.experiments.test_paper_formal_metrics import _real_canonical_rows
from tests.experiments.test_paper_formal_metrics import (
    _executed_formal_lineage_fixture,
)
from tests.experiments.test_paper_metric_observations import (
    ONLINE_ROLES,
    TRACE_ROLES,
    _materialize,
    _online_sources,
    _trace_bundle,
    _trace_sources,
)


def _online_recovery_publication():
    bundle = MetricObservationBundle(
        row_facts={"infra_invalid": False},
        member_ids=("attempt-1",),
        member_facts_by_id={
            "attempt-1": {
                "member_kind": "online_recovery_original_attempt",
                "original_provider_dispatch_succeeded": True,
                "current_provider_roles": ONLINE_ROLES,
            }
        },
        row_identity_digest=digest_json({"row": "task24-exp3-online"}),
    )
    return _materialize(
        "exp3_online_recovery",
        "online_recovery_summary",
        ("actual_provider_calls",),
        bundle,
        _online_sources(),
    )


def _trace_publication():
    return _materialize(
        "exp2_trace_scalability",
        "worker_repeat_observation",
        ("preregistered_root_count",),
        _trace_bundle(),
        _trace_sources(),
    )


def _tree_content_digest(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _resolver_for_trace_objects(root: Path, observations):
    root.mkdir()
    (root / "objects").mkdir()
    locators = observations[0].source_bank_object_locators
    for locator in locators:
        payload = json.dumps(
            {"entry_id": locator.entry_id, "role": locator.object_role},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert "sha256:" + sha256(payload).hexdigest() == locator.object_digest
        (root / "objects" / locator.object_digest.removeprefix("sha256:")).write_bytes(
            payload
        )
    manifest = SimpleNamespace(
        bank_root_id=locators[0].bank_root_id,
        manifest_digest=locators[0].manifest_digest,
        object_role_schema=TRACE_ROLES,
    )
    return ResponseBankResolver(root, SimpleNamespace(manifest=manifest))


def _persist_l4_inputs(
    root: Path,
    canonical_rows,
    **kwargs,
):
    return formal_runner.persist_paper_traceability_replay_input_root(
        replay_input_root=root,
        canonical_direct_rows=canonical_rows,
        **kwargs,
    )


def _genuine_l4_inputs(
    tmp_path: Path,
    *,
    permute: bool = False,
    contaminate_evidence: bool = False,
):
    evidence, direct = _executed_formal_lineage_fixture(tmp_path / "formal-evidence")
    current_files = {}
    for snapshot in direct.current_provider_object_refs:
        matches = tuple(
            (tmp_path / "formal-evidence").rglob(f"*-{snapshot.artifact_id}.bin")
        )
        assert len(matches) == 1
        current_files[snapshot.artifact_id] = matches[0]
    rows = _real_canonical_rows(tmp_path / "direct-inputs", exp1_root=direct)
    rows["exp1_feasibility"] = (
        Exp1HydratedDirectRow(
            direct_result=direct,
            actual_provider_attempts=(
                Exp1ActualProviderAttemptFacts(
                    # 以 persisted Attempt identity 进入 execution binding；
                    # provider artifacts 仍由 direct ref 绑定到 task20-execution。
                    attempt_id="task20-attempt",
                    provider_latency_ms=10,
                    total_tokens=20,
                    cost_estimate_cny=1,
                    current_provider_roles=tuple(sorted(_CURRENT_PROVIDER_ROLES)),
                ),
            ),
        ),
    )
    if permute:
        rows = {
            table_id: tuple(reversed(values))
            for table_id, values in reversed(tuple(rows.items()))
        }
    for snapshot in _walk_instances(rows, ArtifactIdentitySnapshot):
        if (
            snapshot.source_role not in _CURRENT_PROVIDER_ROLES
            or snapshot.artifact_id in current_files
        ):
            continue
        matches = tuple(
            (tmp_path / "direct-inputs").rglob(snapshot.artifact_id)
        )
        assert len(matches) == 1
        current_files[snapshot.artifact_id] = matches[0]
    source_resolvers = {}
    locators = _walk_instances((rows, (evidence,)), ExternalBankObjectLocator)
    for bank_root_id in sorted({item.bank_root_id for item in locators}):
        bank_locators = tuple(
            item for item in locators if item.bank_root_id == bank_root_id
        )
        bank_root = tmp_path / f"source-{bank_root_id}"
        (bank_root / "objects").mkdir(parents=True)
        for locator in bank_locators:
            payload = json.dumps(
                {"label": f"source:{locator.object_role}"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            assert "sha256:" + sha256(payload).hexdigest() == locator.object_digest
            (bank_root / "objects" / locator.object_digest.removeprefix("sha256:")).write_bytes(
                payload
            )
        source_resolvers[bank_root_id] = ResponseBankResolver(
            bank_root,
            SimpleNamespace(
                manifest=SimpleNamespace(
                    bank_root_id=bank_root_id,
                    manifest_digest=bank_locators[0].manifest_digest,
                    object_role_schema=tuple(
                        sorted({item.object_role for item in bank_locators})
                    ),
                )
            ),
        )
    if contaminate_evidence:
        contaminated = tmp_path / "formal-evidence" / "metrics" / "paper_table_bad.csv"
        contaminated.parent.mkdir()
        contaminated.write_text("derived,output\n", encoding="utf-8")
        manifest_path = tmp_path / "formal-evidence" / "evidence_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = contaminated.read_bytes()
        manifest["files"].append(
            {
                "path": contaminated.relative_to(tmp_path / "formal-evidence").as_posix(),
                "content_sha256": "sha256:" + sha256(payload).hexdigest(),
                "size": len(payload),
                "record_count": 1,
                "records_digest": digest_json(["derived-output"]),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    descriptor = _persist_l4_inputs(
        tmp_path / "replay-input-root",
        rows,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(direct.preregistered_root_run_id,),
        current_provider_object_files=current_files,
        current_evidence_root=tmp_path / "formal-evidence",
        source_resolvers=source_resolvers,
    )
    return descriptor, rows


def test_l4_factory_rejects_manifest_listed_derived_evidence_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    monkeypatch.setattr(FormalEvidenceStore, "_validate_evidence_manifest", lambda self: None)

    with pytest.raises(TraceabilityBlockedError, match="derived|formal evidence"):
        _genuine_l4_inputs(tmp_path, contaminate_evidence=True)


def test_replay_never_calls_provider_or_writes_source_bank(tmp_path: Path) -> None:
    contract, publication = _trace_publication()
    source_root = tmp_path / "immutable-bank"
    resolver = _resolver_for_trace_objects(source_root, publication.observations)
    before = _tree_content_digest(source_root)

    resolved = verify_external_source_locators(
        publication.observations,
        {"bank-1": resolver},
    )
    result = recompute_cell_lineage(
        contract=contract,
        observations=publication.observations,
        audit_level=L1_SYNTHETIC_COMPONENT,
    )

    assert resolved == len(TRACE_ROLES)
    assert result.provider_calls == 0
    assert result.source_write_count == 0
    assert _tree_content_digest(source_root) == before


def test_pure_component_recomputation_uses_synthetic_fixture_for_l1() -> None:
    contract, publication = _online_recovery_publication()

    result = recompute_cell_lineage(
        contract=contract,
        observations=publication.observations,
        audit_level=L1_SYNTHETIC_COMPONENT,
    )

    assert result.audit_level == L1_SYNTHETIC_COMPONENT
    assert result.regression_only is True
    assert result.records[0]["recomputed_value"] == 1


def test_artifact_root_audit_is_separate_l4_nodeid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tokenshare.experiments.paper_formal_metrics as formal_metrics

    descriptor, _canonical_rows = _genuine_l4_inputs(tmp_path)
    original = formal_metrics.recompute_paper_formal_metrics
    restored_before_recompute = []

    def inspect_fresh_root(output_root, *args, **kwargs):
        root = Path(output_root)
        restored = tuple(
            sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        )
        restored_before_recompute.append(restored)
        assert restored
        assert not any(
            path.startswith(("metrics/", "paper/", "audit/"))
            or path.endswith((".csv", ".tex"))
            for path in restored
        )
        return original(output_root, *args, **kwargs)

    monkeypatch.setattr(formal_metrics, "recompute_paper_formal_metrics", inspect_fresh_root)

    result = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "artifact-root",
        replay_input_root=descriptor,
    )

    assert result.audit_level == L4_ARTIFACT_ROOT
    assert result.regression_only is False
    assert result.report.formal_paper_table_generated is True
    assert result.provider_calls == 0
    assert restored_before_recompute


def test_two_recomputations_match_observations_tables_and_lineage_digests(
    tmp_path: Path,
) -> None:
    descriptor, canonical_rows = _genuine_l4_inputs(tmp_path)

    first = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "recompute-a",
        replay_input_root=descriptor,
    )
    second = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "recompute-b",
        replay_input_root=descriptor,
    )

    assert first.observations_digest == second.observations_digest
    assert first.tables_digest == second.tables_digest
    assert first.cell_lineage_digest == second.cell_lineage_digest
    assert first.output_root != second.output_root
    assert first.loaded_direct_inputs is not canonical_rows
    assert first.loaded_direct_inputs is not second.loaded_direct_inputs
    assert first.loaded_current_inputs is not second.loaded_current_inputs
    assert first.loaded_source_inputs is not second.loaded_source_inputs


def test_every_numeric_cell_recomputes_or_has_null_reason(tmp_path: Path) -> None:
    descriptor, _canonical_rows = _genuine_l4_inputs(tmp_path)
    result = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "artifact-root",
        replay_input_root=descriptor,
    )

    records = tuple(
        json.loads(line)
        for line in (tmp_path / "artifact-root" / "audit" / "paper_cell_lineage.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert records
    assert all(
        (
            row["numeric_value"] == row["recomputed_value"]
            and row["formula_recomputed"] is True
        )
        if row["numeric_value"] is not None
        else (
            row["recomputed_value"] is None
            and bool(row["null_reason"])
            and row["denominator_inventory_ids"]
            == row["preserved_denominator_inventory_ids"]
        )
        for row in records
    )
    assert result.cell_lineage_digest.startswith("sha256:")


def test_exp3_online_recovery_cells_recompute_from_current_provider_roles() -> None:
    contract, publication = _online_recovery_publication()

    result = recompute_cell_lineage(
        contract=contract,
        observations=publication.observations,
        audit_level=L1_SYNTHETIC_COMPONENT,
    )

    row = result.records[0]
    assert row["table_id"] == "exp3_online_recovery"
    assert row["required_current_provider_roles"] == list(ONLINE_ROLES)
    assert row["covered_current_provider_roles"] == list(ONLINE_ROLES)
    assert row["source_bank_object_locators"] == "N/A"


def test_trace_cells_recompute_from_source_locator_roles() -> None:
    contract, publication = _trace_publication()

    result = recompute_cell_lineage(
        contract=contract,
        observations=publication.observations,
        audit_level=L1_SYNTHETIC_COMPONENT,
    )

    row = result.records[0]
    assert row["required_source_bank_roles"] == list(TRACE_ROLES)
    assert row["covered_source_bank_roles"] == list(TRACE_ROLES)
    assert row["current_provider_object_refs"] == "N/A"
    assert len(row["source_bank_object_locators"]) == len(TRACE_ROLES)


def test_missing_external_object_blocks_without_online_fill(tmp_path: Path) -> None:
    _contract, publication = _trace_publication()
    locators = publication.observations[0].source_bank_object_locators
    source_root = tmp_path / "incomplete-bank"
    source_root.mkdir()
    (source_root / "objects").mkdir()
    resolver = ResponseBankResolver(
        source_root,
        SimpleNamespace(
            manifest=SimpleNamespace(
                bank_root_id=locators[0].bank_root_id,
                manifest_digest=locators[0].manifest_digest,
                object_role_schema=TRACE_ROLES,
            )
        ),
    )

    with pytest.raises(TraceabilityBlockedError, match="missing external object"):
        verify_external_source_locators(
            publication.observations,
            {"bank-1": resolver},
        )
    assert not tuple((source_root / "objects").iterdir())


def test_historical_negative_expected_fail_does_not_mutate_source(
    tmp_path: Path,
) -> None:
    negative_root = tmp_path / "exp34-smoke-20260731-041442"
    negative_root.mkdir()
    evidence = negative_root / "immutable.json"
    evidence.write_bytes(b'{"status":"expected_failure"}')
    before = _tree_content_digest(negative_root)

    classification = classify_20260731_exp34_negative(negative_root)

    assert classification["classification"] == "negative_only_expected_fail"
    assert classification["paper_eligible"] is False
    assert classification["tree_digest_before"] == classification["tree_digest_after"]
    assert _tree_content_digest(negative_root) == before


@pytest.mark.parametrize("closure_kind", ("direct", "current", "source"))
@pytest.mark.parametrize("damage_kind", ("mutated", "missing"))
def test_l4_protected_replay_input_root_rejects_mutated_or_missing_closure(
    tmp_path: Path,
    closure_kind: str,
    damage_kind: str,
) -> None:
    descriptor, _rows = _genuine_l4_inputs(tmp_path)
    body = json.loads(descriptor.descriptor_path.read_text(encoding="utf-8"))
    if closure_kind == "current":
        target = descriptor.root_path / body["current_provider_object_refs"][0]["path"]
    else:
        target = descriptor.root_path / body["input_refs"][closure_kind]["path"]
    if damage_kind == "mutated":
        target.write_bytes(target.read_bytes() + b"tampered")
    else:
        target.unlink()

    with pytest.raises(TraceabilityBlockedError, match="replay input closure"):
        formal_runner.recompute_paper_traceability_replay(
            output_root=tmp_path / "blocked-output",
            replay_input_root=descriptor,
        )
    assert not (tmp_path / "blocked-output" / "metrics").exists()


def test_permuted_immutable_inputs_share_canonical_observation_digest(
    tmp_path: Path,
) -> None:
    first_descriptor, _rows = _genuine_l4_inputs(tmp_path / "fixture-a")
    second_descriptor, _permuted = _genuine_l4_inputs(
        tmp_path / "fixture-b",
        permute=True,
    )

    first = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "output-a",
        replay_input_root=first_descriptor,
    )
    second = formal_runner.recompute_paper_traceability_replay(
        output_root=tmp_path / "output-b",
        replay_input_root=second_descriptor,
    )

    assert first.observations_digest == second.observations_digest
    assert first.tables_digest == second.tables_digest
    assert first.cell_lineage_digest == second.cell_lineage_digest
