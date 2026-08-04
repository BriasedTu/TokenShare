from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from tokenshare.experiments import paper_exp5_artifacts as artifact_module
from tokenshare.experiments.paper_exp5_artifacts import (
    PAPER_RENDERER_AUDIT_FILE,
    PAPER_RENDERER_MANIFEST_FILE,
    PAPER_TABLE_CSV_FILES,
    PAPER_TABLE_TEX_FILES,
    render_paper_metric_artifacts,
)
from tokenshare.experiments.paper_exp3_metrics import (
    ONLINE_CURRENT_PROVIDER_ROLES,
    Exp3OnlineRecoveryInput,
    Exp3PersistedObservation,
)
from tokenshare.experiments.paper_formal_evidence import (
    LineageSourceIndex,
    LineageSourceRecord,
)
from tokenshare.experiments.paper_metric_contract import (
    capture_metric_computation_traces,
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_metric_observations import (
    PaperMetricObservation,
    materialize_metric_observations,
)
from tokenshare.experiments.paper_metric_registry import load_paper_metric_registry
from tokenshare.experiments.paper_models import LedgerEventIdentitySnapshot, digest_json

from test_paper_metric_renderer_contract import (
    _DIGEST,
    _collection_digest,
    _complete_publication,
    _observation,
    _provider_ref,
    _resign,
)


EXPECTED_CSV = (
    "metrics/paper_table_feasibility.csv",
    "metrics/paper_plot_scalability.csv",
    "metrics/paper_table_scalability_online.csv",
    "metrics/paper_plot_robustness.csv",
    "metrics/paper_table_recovery_online.csv",
    "metrics/paper_table_ablation.csv",
    "metrics/paper_table_model_endpoint_quality.csv",
    "metrics/paper_table_model_endpoint_resources.csv",
)


def test_exact_current_table_inventory_includes_exp3_online_recovery_and_two_exp5_summaries(
    tmp_path: Path,
) -> None:
    contract = load_paper_metric_contract()
    observations = _complete_publication()
    stale_paths = (
        tmp_path / "metrics" / "exp5_paired_comparisons.csv",
        tmp_path / "paper" / "exp5_results_summary.md",
    )
    for path in stale_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale legacy output\n", encoding="utf-8")

    result = render_paper_metric_artifacts(
        output_root=tmp_path,
        contract=contract,
        observations=observations,
        observations_digest=_collection_digest(observations),
    )
    first_hashes = {
        ref["path"]: ref["content_hash"] for ref in result.artifact_refs
    }
    repeated = render_paper_metric_artifacts(
        output_root=tmp_path,
        contract=contract,
        observations=observations,
        observations_digest=_collection_digest(observations),
    )

    assert PAPER_TABLE_CSV_FILES == EXPECTED_CSV
    assert PAPER_TABLE_TEX_FILES == tuple(path[:-4] + ".tex" for path in EXPECTED_CSV)
    assert tuple(ref["path"] for ref in result.table_refs[::2]) == EXPECTED_CSV
    assert {
        ref["path"]: ref["content_hash"] for ref in repeated.artifact_refs
    } == first_hashes
    assert not any(path.exists() for path in stale_paths)
    expected_files = {
        *(str(tmp_path / path).replace("\\", "/") for path in EXPECTED_CSV),
        *(str(tmp_path / path).replace("\\", "/") for path in PAPER_TABLE_TEX_FILES),
        str(tmp_path / PAPER_RENDERER_AUDIT_FILE).replace("\\", "/"),
        str(tmp_path / PAPER_RENDERER_MANIFEST_FILE).replace("\\", "/"),
    }
    assert {path.as_posix() for path in tmp_path.rglob("*") if path.is_file()} == expected_files
    retired = "\n".join(ref["path"] for ref in result.artifact_refs)
    assert "paired" not in retired
    assert "accepted_validity" not in retired
    assert "ranking" not in retired
    assert "recovery_rate" not in retired


def test_exp3_online_recovery_table_comes_from_task14_registry_observations(
    tmp_path: Path,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    task14_input = Exp3OnlineRecoveryInput(
        recovery_source="validation_replacement",
        case_id="task14-online-recovery",
        repeat_id=0,
        observations=(
            Exp3PersistedObservation(
                observation_id="task14-recovery-anchor",
                facts={
                    "member_kind": "exp3_online_recovery_identity",
                    "case_id": "task14-online-recovery",
                    "recovery_source": "validation_replacement",
                    "current_replacement_attempt_id": "attempt-replacement",
                    "provider_object_links": (
                        (
                            "attempt-replacement",
                            "response-replacement",
                            "raw-replacement",
                            "provenance-replacement",
                            "usage-replacement",
                            "model-replacement",
                        ),
                    ),
                },
            ),
            Exp3PersistedObservation(
                observation_id="task14-root",
                facts={
                    "member_kind": "preregistered_root",
                    "final_result_reference_complete": True,
                    "end_to_end_verified_success": True,
                    "current_provider_roles": ONLINE_CURRENT_PROVIDER_ROLES,
                },
            ),
            Exp3PersistedObservation(
                observation_id="task14-provider-attempt",
                facts={
                    "member_kind": "online_recovery_replacement_attempt",
                    "original_provider_dispatch_succeeded": False,
                    "replacement_provider_dispatch_succeeded": True,
                    "actual_prompt_tokens": 11,
                    "actual_completion_tokens": 7,
                    "actual_total_tokens": 18,
                    "actual_cost_estimate_cny": Decimal("1.25"),
                    "current_provider_roles": ONLINE_CURRENT_PROVIDER_ROLES,
                    "case_id": "task14-online-recovery",
                    "recovery_source": "validation_replacement",
                    "provider_attempt_id": "attempt-replacement",
                    "current_replacement_attempt_id": "attempt-replacement",
                    "provider_response_id": "response-replacement",
                    "raw_or_failure_id": "raw-replacement",
                    "provenance_id": "provenance-replacement",
                    "usage_id": "usage-replacement",
                    "model_record_id": "model-replacement",
                },
            ),
        ),
    )
    provider_refs = tuple(
        _provider_ref(role, "attempt-replacement", index)
        for index, role in enumerate(ONLINE_CURRENT_PROVIDER_ROLES)
    )
    attempt_event = LedgerEventIdentitySnapshot(
        event_seq=1,
        event_id="event-attempt-replacement",
        event_type="ATTEMPT_STATE_CHANGED",
        event_hash=digest_json({"event": "attempt-replacement"}),
        prev_event_hash=None,
        task_id="task-1",
        object_type="Attempt",
        object_id="attempt-replacement",
    )
    direct_ref = {
        "preregistered_root_run_id": "task14-root",
        "execution_binding": {
            "execution_id": "attempt-replacement",
            "task_id": "task-1",
            "root_unit_id": "task14-root-unit",
        },
        "attempt_refs": [attempt_event.to_dict()],
    }
    source_index = LineageSourceIndex.create(
        input_identity_digest=_DIGEST,
        records=(
            LineageSourceRecord.create(
                member_id="attempt-replacement",
                evidence_class="online_real_provider",
                direct_result_refs=(direct_ref,),
                current_task_attempt_event_refs=(attempt_event,),
                current_provider_object_refs=provider_refs,
            ),
        ),
    )
    with capture_metric_computation_traces() as computation_traces:
        task17_draft = registry.project_table(
            "exp3_online_recovery",
            (task14_input,),
        )
    task20_publication = materialize_metric_observations(
        contract=contract,
        table_drafts=(task17_draft,),
        computation_traces=tuple(computation_traces),
        source_index=source_index,
        expected_source_input_identity_digest=_DIGEST,
    )
    task20_by_column = {
        item.column_id: item for item in task20_publication.observations
    }
    target = task20_by_column["actual_provider_calls"]
    assert target.numeric_value == 1, target.to_dict()
    assert target.publish_blocked is False, target.to_dict()
    observations = tuple(
        task20_by_column[item.column_id]
        if item.table_id == "exp3_online_recovery"
        else item
        for item in _complete_publication()
    )

    render_paper_metric_artifacts(
        output_root=tmp_path,
        contract=contract,
        observations=observations,
        observations_digest=_collection_digest(observations),
    )

    with (tmp_path / "metrics" / "paper_table_recovery_online.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["case_id"] == "task14-online-recovery"
    assert row["actual_provider_calls"] == "1"
    tex = (tmp_path / "metrics" / "paper_table_recovery_online.tex").read_text(
        encoding="utf-8"
    )
    assert "actual\\_provider\\_calls" in tex
    assert "task14-online-recovery" in tex
    audit_rows = tuple(
        json.loads(line)
        for line in (tmp_path / PAPER_RENDERER_AUDIT_FILE)
        .read_text(encoding="utf-8")
        .splitlines()
    )
    audit = next(row for row in audit_rows if row["observation_id"] == target.observation_id)
    assert audit["observation_digest"] == target.observation_digest
    manifest = json.loads(
        (tmp_path / PAPER_RENDERER_MANIFEST_FILE).read_text(encoding="utf-8")
    )
    recovery = next(
        table for table in manifest["tables"] if table["table_id"] == "exp3_online_recovery"
    )
    assert target.observation_id in recovery["observation_ids"]
    assert target.observation_digest in recovery["observation_digests"]
    assert recovery["csv_ref"]["path"] == "metrics/paper_table_recovery_online.csv"
    assert recovery["tex_ref"]["path"] == "metrics/paper_table_recovery_online.tex"


def test_renderer_never_reads_raw_reasoning(tmp_path: Path, monkeypatch) -> None:
    contract = load_paper_metric_contract()
    safe = _observation("exp5_quality", "completion_rate")
    observation = replace(
        safe,
        direct_result_refs=(
            {"artifact_id": "artifact-safe-identity", "raw_reasoning": "secret"},
        ),
    )
    observations = tuple(
        observation
        if item.table_id == observation.table_id
        and item.column_id == observation.column_id
        else item
        for item in _complete_publication()
    )

    def forbidden_to_dict(_self: PaperMetricObservation):
        raise AssertionError("renderer traversed the raw observation payload")

    monkeypatch.setattr(PaperMetricObservation, "to_dict", forbidden_to_dict)
    with pytest.raises(ValueError, match="raw reasoning/output payload"):
        render_paper_metric_artifacts(
            output_root=tmp_path,
            contract=contract,
            observations=observations,
            observations_digest=_collection_digest(observations),
        )
    assert not (tmp_path / PAPER_RENDERER_AUDIT_FILE).exists()


def test_renderer_rejects_uncontracted_or_unsafe_row_key_recursively(
    tmp_path: Path,
) -> None:
    contract = load_paper_metric_contract()
    observations = _complete_publication()
    unexpected = _resign(
        replace(
            observations[0],
            row_key={**dict(observations[0].row_key), "convenience_label": "x"},
        )
    )
    nested = next(item for item in observations if item.table_id == "exp5_quality")
    unsafe = _resign(
        replace(
            nested,
            row_key={
                **dict(nested.row_key),
                "model_endpoint_identity": {
                    "metadata": {"raw_reasoning": "must-not-render"}
                },
            },
        )
    )
    unsafe_value = _resign(
        replace(
            nested,
            row_key={
                **dict(nested.row_key),
                "model_endpoint_identity": {
                    "metadata": {"kind": "raw_reasoning"}
                },
            },
        )
    )

    for name, replacement in (
        ("schema", unexpected),
        ("unsafe", unsafe),
        ("unsafe-value", unsafe_value),
    ):
        candidate = tuple(
            replacement if item.observation_id == replacement.observation_id else item
            for item in observations
        )
        message = "row key schema" if name == "schema" else "raw reasoning/output payload"
        with pytest.raises(ValueError, match=message):
            render_paper_metric_artifacts(
                output_root=tmp_path / name,
                contract=contract,
                observations=candidate,
                observations_digest=_collection_digest(candidate),
            )


def test_failed_promotion_restores_complete_previous_generation_and_cleans_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    observations = _complete_publication()
    owned = tuple(
        sorted(
            {
                *PAPER_TABLE_CSV_FILES,
                *PAPER_TABLE_TEX_FILES,
                PAPER_RENDERER_AUDIT_FILE,
                PAPER_RENDERER_MANIFEST_FILE,
                *artifact_module._RETIRED_OUTPUTS,
            }
        )
    )
    before: dict[str, bytes | None] = {}
    for index, relative_path in enumerate(owned):
        path = tmp_path / relative_path
        if relative_path == PAPER_RENDERER_AUDIT_FILE:
            before[relative_path] = None
            continue
        payload = f"previous:{index}:{relative_path}\n".encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        before[relative_path] = payload

    original_replace = Path.replace
    promoted: list[str] = []

    def fail_after_first_promoted_file(source: Path, target: Path) -> Path:
        target_path = Path(target)
        source_is_stage = any(
            part.startswith(".paper-metric-stage-") for part in source.parts
        )
        target_is_stage = any(
            part.startswith(".paper-metric-stage-") for part in target_path.parts
        )
        if source_is_stage and not target_is_stage:
            result = original_replace(source, target_path)
            promoted.append(target_path.relative_to(tmp_path).as_posix())
            if len(promoted) == 1:
                raise OSError("injected failure after first promoted file")
            return result
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_after_first_promoted_file)

    with pytest.raises(OSError, match="after first promoted"):
        render_paper_metric_artifacts(
            output_root=tmp_path,
            contract=contract,
            observations=observations,
            observations_digest=_collection_digest(observations),
        )

    assert len(promoted) == 1
    for relative_path, payload in before.items():
        path = tmp_path / relative_path
        if payload is None:
            assert not path.exists(), relative_path
        else:
            assert path.read_bytes() == payload, relative_path
    assert not tuple(tmp_path.glob(".paper-metric-stage-*"))
    assert not tuple(tmp_path.glob(".paper-metric-backup-*"))
