from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_report import (
    scan_artifact_store_for_secrets,
    write_exp1_pilot_report,
)
from tokenshare.storage.artifacts import ArtifactStore


def test_report_writes_six_pilot_only_outputs(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]

    result = write_exp1_pilot_report(root)

    expected = {
        root / "metrics" / "metrics.json",
        root / "metrics" / "task_metrics.csv",
        root / "metrics" / "summary.csv",
        root / "audit" / "audit_summary.json",
        root / "report.md",
        root / "metrics" / "exp1_pilot_feasibility.csv",
    }
    assert set(result.output_paths) == {path.as_posix() for path in expected}
    assert all(path.is_file() for path in expected)
    metrics = json.loads((root / "metrics" / "metrics.json").read_text(encoding="utf-8"))
    audit = json.loads((root / "audit" / "audit_summary.json").read_text(encoding="utf-8"))
    assert metrics["pilot_only"] is True
    assert audit["paper_eligible"] is True
    assert audit["secret_scan"]["passed"] is True
    with (root / "metrics" / "exp1_pilot_feasibility.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["pilot_only"] for row in rows} == {"true"}
    with (root / "metrics" / "task_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        task_rows = list(csv.DictReader(handle))
    assert task_rows[0]["provider_latency_ms"] == "5"
    assert task_rows[0]["wall_clock_ms"] == "20"
    assert task_rows[1]["provider_latency_ms"] == "7"
    assert task_rows[1]["wall_clock_ms"] == "30"
    report = (root / "report.md").read_text(encoding="utf-8")
    assert "pilot-only" in report
    assert "不得进入论文主表" in report


def test_report_keeps_scripted_rows_out_of_feasibility_export(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    runs_path = root / "run_results.jsonl"
    runs = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()]
    runs[0]["transport_evidence"] = {
        "real_transport": False,
        "transport_kind": "scripted",
    }
    runs[0]["paper_eligible"] = False
    runs_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in runs),
        encoding="utf-8",
    )
    complete_paper_evidence_suite["refresh_manifest"]()

    write_exp1_pilot_report(root)

    with (root / "metrics" / "exp1_pilot_feasibility.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert list(csv.DictReader(handle)) == []
    audit = json.loads((root / "audit" / "audit_summary.json").read_text(encoding="utf-8"))
    assert audit["paper_eligible"] is False
    assert audit["feasibility_rows_exported"] == 0


def test_report_attests_completed_suite_scan_over_adapter_pending_placeholder(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    runs_path = root / "run_results.jsonl"
    tasks_path = root / "per_task_results.jsonl"
    runs = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()]
    tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
    runs[0]["paper_eligible"] = False
    runs[0]["ineligibility_reasons"] = [
        "secret_scan_failed",
        "adapter_task_not_paper_eligible",
        "adapter_eligibility_report_not_paper_eligible",
    ]
    tasks[0]["paper_eligible"] = False
    runs_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in runs),
        encoding="utf-8",
    )
    tasks_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in tasks),
        encoding="utf-8",
    )
    complete_paper_evidence_suite["refresh_manifest"]()

    first = write_exp1_pilot_report(
        root,
        secret_values=("offline-attestation-test-secret",),
    )
    replay = write_exp1_pilot_report(root)

    assert first.paper_eligible is True
    assert replay.paper_eligible is True
    attestation = json.loads(
        (root / "audit" / "suite_secret_scan_attestation.json").read_text(
            encoding="utf-8"
        )
    )
    assert attestation["attested_run_ids"] == [runs[0]["run_id"]]
    assert attestation["secret_scan"]["secret_checked_count"] == 1
    assert "offline-attestation-test-secret" not in json.dumps(
        attestation, ensure_ascii=False
    )
    audit = json.loads(
        (root / "audit" / "audit_summary.json").read_text(encoding="utf-8")
    )
    assert audit["suite_secret_scan_attestation_valid"] is True
    assert audit["secret_scan"]["secret_checked_count"] == 1

    attestation["provider_calls_made_for_attestation"] = 1
    (root / "audit" / "suite_secret_scan_attestation.json").write_text(
        json.dumps(attestation, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="attestation digest"):
        write_exp1_pilot_report(root)


def test_report_rejects_exact_secret_before_writing_outputs(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    secret = "test-secret-value-that-must-not-persist"
    (root / "leaked.log").write_text(f"Authorization: Bearer {secret}", encoding="utf-8")

    with pytest.raises(ValueError, match="secret scan"):
        write_exp1_pilot_report(root, secret_values=(secret,))

    assert not (root / "metrics").exists()
    assert not (root / "audit").exists()


def test_artifact_secret_scan_detects_exact_secret_without_persisting_value(
    tmp_path,
) -> None:
    secret = "test-artifact-secret-value"
    store = ArtifactStore(tmp_path)
    store.save_bytes(
        f"provider response accidentally contained {secret}".encode("utf-8"),
        artifact_id="raw_provider_output",
        artifact_type="AIProviderRawResponse",
        media_type="application/json",
        artifact_schema_id="phase7.ai_api_raw_response",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-07-17T00:00:00Z",
    )

    report = scan_artifact_store_for_secrets(store, secret_values=(secret,))

    assert report["status"] == "failed"
    assert report["leak_count"] == 1
    assert report["secret_checked_count"] == 1
    assert report["scanned_artifact_ids"] == ["raw_provider_output"]
    assert secret not in json.dumps(report, ensure_ascii=False)


def test_report_records_audit_limitation_without_rewriting_raw_evidence(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    task_path = root / "per_task_results.jsonl"
    tasks = [
        json.loads(line)
        for line in task_path.read_text(encoding="utf-8").splitlines()
    ]
    tasks[1]["paper_difficulty"] = "easy"
    task_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in tasks),
        encoding="utf-8",
    )
    complete_paper_evidence_suite["refresh_manifest"]()
    raw_paths = (
        root / "execution_plan.json",
        root / "conditions.jsonl",
        root / "run_results.jsonl",
        root / "per_task_results.jsonl",
        root / "per_attempt_results.jsonl",
        root / "events" / "event_log.jsonl",
        root / "artifacts" / "artifact_index.jsonl",
        root / "evidence_manifest.json",
    )
    before = {path: path.read_bytes() for path in raw_paths}

    result = write_exp1_pilot_report(
        root,
        allow_audit_limitation=True,
    )

    assert result.paper_eligible is False
    assert result.feasibility_rows_exported == 0
    assert before == {path: path.read_bytes() for path in raw_paths}
    metrics = json.loads((root / "metrics" / "metrics.json").read_text(encoding="utf-8"))
    audit = json.loads((root / "audit" / "audit_summary.json").read_text(encoding="utf-8"))
    assert metrics["calculation_status"] == "audit_limited"
    assert metrics["paper_eligible"] is False
    assert metrics["diagnostic_totals"]["provider_latency_ms"] == 12
    assert metrics["diagnostic_totals"]["wall_clock_ms"] == 50
    assert audit["evidence_validation_status"] == "failed"
    assert audit["provider_calls_made_for_report"] == 0
    assert audit["historical_provider_attempt_count"] == 2
    assert audit["current_provider_calls_made"] == 2
    assert audit["replayed_run_count"] == 0
    assert audit["formal_paper_table_generated"] is False
    assert "paper_difficulty" in " ".join(audit["audit_limitations"])
    with (root / "metrics" / "exp1_pilot_feasibility.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert list(csv.DictReader(handle)) == []
    assert "audit limitation" in (root / "report.md").read_text(encoding="utf-8")
