import csv
import json
from pathlib import Path

from tokenshare.experiments.lean_ai_benchmark import (
    LEAN_AI_BENCHMARK_REPORT_SCHEMA_VERSION,
    ScriptedLeanProofTransport,
    generate_lean_ai_benchmark_cases,
    run_lean_ai_benchmark_suite,
)
from tokenshare.experiments.run_lean_ai_benchmark import main as run_benchmark_main


def test_generate_lean_ai_benchmark_cases_matches_current_split_helper_slice() -> None:
    cases = generate_lean_ai_benchmark_cases()

    assert len(cases) == 50
    assert len({case.case_id for case in cases}) == 50
    assert [case.input_index for case in cases] == list(range(50))
    assert sum(case.statement_source == "P ∧ Q" for case in cases) == 25
    assert sum(case.statement_source == "P ↔ Q" for case in cases) == 25
    assert all(case.theorem_payload.imports == ["Init"] for case in cases)
    assert all(case.theorem_payload.namespace == "TokenShareGenerated" for case in cases)
    assert all(case.theorem_payload.resource_limits["timeout_seconds"] == 30 for case in cases)
    assert all(
        case.expected_child_keys in (("child:left", "child:right"), ("child:forward", "child:backward"))
        for case in cases
    )


def test_lean_ai_benchmark_suite_writes_checker_merge_outputs_with_scripted_transport(
    tmp_path: Path,
) -> None:
    report = run_lean_ai_benchmark_suite(
        output_root=tmp_path,
        count=2,
        seed=23,
        transport=ScriptedLeanProofTransport(),
    )

    assert report["schema_version"] == LEAN_AI_BENCHMARK_REPORT_SCHEMA_VERSION
    assert report["requested_count"] == 2
    assert report["attempted_count"] == 2
    assert report["passed_count"] == 2
    assert report["merge_success_count"] == 2
    assert report["parser_success_rate"] == 1.0
    assert report["checker_success_rate"] == 1.0
    assert report["parse_failure_count"] == 0
    assert report["real_transport"] is False

    expected_paths = [
        "lean_ai_50_settings.json",
        "task_catalog.json",
        "per_task_results.jsonl",
        "per_task_summary.csv",
        "batch_report.json",
    ]
    for relative_path in expected_paths:
        assert (tmp_path / relative_path).exists()

    settings_text = (tmp_path / "lean_ai_50_settings.json").read_text(encoding="utf-8")
    assert "tokenshare-lean-ai-fake-key" not in settings_text
    settings = json.loads(settings_text)
    assert settings["requested_count"] == 2
    assert settings["prompt_constraints"]["requires_json_mode"] is True
    assert settings["parser_policy"]["parser_id"] == "lean_proof.proof_candidate.parser.v1"
    assert settings["lean_split_helper_slice"] == {
        "supported_statement_sources": ["P ∧ Q", "P ↔ Q"],
        "supported_merge_rules": [
            "lean_merge.conjunction_intro.v1",
            "lean_merge.iff_intro.v1",
        ],
    }

    rows = list(csv.DictReader((tmp_path / "per_task_summary.csv").open(encoding="utf-8")))
    assert len(rows) == 2
    assert all(row["status"] == "passed" for row in rows)
    assert all(row["split_supported"] == "true" for row in rows)
    assert all(row["merge_success"] == "true" for row in rows)
    assert all(row["raw_output_count"] == "2" for row in rows)
    assert all(row["parsed_output_count"] == "2" for row in rows)
    assert all(row["checker_accepted_count"] == "2" for row in rows)

    result_lines = [
        json.loads(line)
        for line in (tmp_path / "per_task_results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(result_lines) == 2
    assert all(Path(line["artifact_root_path"]).exists() for line in result_lines)
    assert all(Path(line["event_log_path"]).exists() for line in result_lines)
    assert all(line["merge_result_ref"] for line in result_lines)
    assert all(line["root_checker_report_ref"] for line in result_lines)
    assert all(line["child_results"] for line in result_lines)


def test_lean_ai_benchmark_cli_writes_report_with_scripted_transport(tmp_path: Path) -> None:
    exit_code = run_benchmark_main(
        ["--output-root", str(tmp_path), "--count", "2", "--seed", "31"]
    )

    report_path = tmp_path / "batch_report.json"
    assert exit_code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["attempted_count"] == 2
    assert report["passed_count"] == 2
    assert report["parser_success_rate"] == 1.0
