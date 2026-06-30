import csv
import json

from tokenshare.experiments.ai_profile import (
    _build_ai_execution_request,
    _default_ai_profile_config,
    _factors_from_successful_outputs,
    _range_result_body,
    _run_ai_parse_failure_profile,
    _semiprime_range_inputs,
    _strict_arithmetic_config,
    run_ai_profile_suite,
)
from tokenshare.experiments.run_ai_profile import main as run_ai_profile_main
from tokenshare.experiments.run_all import main as run_all_main
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore
from tests.phase7_fixtures import make_config_dict


def test_ai_profile_suite_records_ai_executor_outputs_and_comparison(tmp_path) -> None:
    report = run_ai_profile_suite(output_root=tmp_path, seed=11)

    assert report["schema_version"] == "phase8.ai_profile_suite_report.v1"
    assert report["total_profiles"] == 3
    assert report["summary_csv_path"]
    assert report["suite_report_path"]

    profiles = {item["case_id"]: item for item in report["profiles"]}
    deterministic = profiles["deterministic_semiprime_range_flow"]
    ai_success = profiles["ai_api_semiprime_range_flow"]
    ai_parse_failure = profiles["ai_api_parse_failure_raw_only"]

    assert deterministic["executor_profile"] == "deterministic_local"
    assert deterministic["final_correctness"] is True
    assert deterministic["cost_estimate_total"] == 0

    assert ai_success["executor_profile"] == "ai_api"
    assert ai_success["final_correctness"] is True
    assert ai_success["raw_output_count"] > 0
    assert ai_success["parsed_output_count"] > 0
    assert ai_success["parse_failure_count"] == 0
    assert ai_success["parser_success_rate"] == 1.0
    assert ai_success["provider_attempt_count"] == ai_success["raw_output_count"]
    assert ai_success["usage"]["total_tokens"] > 0
    assert ai_success["usage"]["prompt_tokens"] > 0
    assert ai_success["usage"]["completion_tokens"] > 0
    assert ai_success["retry_count"] == 0
    assert ai_success["cost_estimate_total"] > 0
    assert ai_success["latency_ms_total"] >= 0
    assert ai_success["providers"]
    assert ai_success["models"]
    assert ai_success["raw_output_refs"]
    assert ai_success["parsed_output_refs"]

    assert ai_parse_failure["experiment_id"] == "exp2_failure_recovery"
    assert ai_parse_failure["executor_profile"] == "ai_api"
    assert ai_parse_failure["final_correctness"] is False
    assert ai_parse_failure["raw_output_count"] == 1
    assert ai_parse_failure["parsed_output_count"] == 0
    assert ai_parse_failure["parse_failure_count"] == 1
    assert ai_parse_failure["parser_success_rate"] == 0.0
    assert ai_parse_failure["parse_failure_refs"]
    assert ai_parse_failure["provider_attempt_count"] == 1
    assert ai_parse_failure["cost_estimate_total"] > 0

    comparison = report["deterministic_vs_ai_api"]["semiprime_range_flow"]
    assert comparison["deterministic_correctness"] is True
    assert comparison["ai_api_correctness"] is True
    assert comparison["correctness_delta"] == 0
    assert comparison["deterministic_parser_success_rate"] == 1.0
    assert comparison["ai_api_parser_success_rate"] == 1.0
    assert comparison["cost_delta"] == ai_success["cost_estimate_total"]
    assert comparison["retry_delta"] == 0

    summary_rows = list(csv.DictReader(open(report["summary_csv_path"], encoding="utf-8")))
    assert {row["case_id"] for row in summary_rows} == set(profiles)
    assert {
        "provider_attempt_count",
        "retry_count",
        "cost_estimate_total",
        "latency_ms_total",
        "parser_success_rate",
        "providers",
        "models",
    }.issubset(summary_rows[0])


def test_ai_profile_cli_writes_suite_report(tmp_path) -> None:
    exit_code = run_ai_profile_main(["--output-root", str(tmp_path), "--seed", "13"])

    report_path = tmp_path / "ai_profile_suite_report.json"
    assert exit_code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "phase8.ai_profile_suite_report.v1"
    assert report["total_profiles"] == 3


def test_ai_profile_real_json_requests_have_non_truncated_token_budget(tmp_path) -> None:
    store = ArtifactStore(tmp_path)

    request = _build_ai_execution_request(
        store=store,
        range_input=_semiprime_range_inputs()[0],
        seed=19,
        case_id="ai_api_semiprime_range_flow",
        index=1,
    )

    assert request.limits["max_tokens"] >= 1024


def test_ai_profile_semiprime_success_requires_found_factor_output() -> None:
    no_factor_only = [_range_result_body(_semiprime_range_inputs()[0])]

    assert _factors_from_successful_outputs(no_factor_only) == []


def test_ai_profile_strict_arithmetic_config_excludes_qwen_when_alternatives_exist() -> None:
    body = make_config_dict()
    body["entries"][0]["model"] = "Qwen/Qwen3.6-27B"
    body["entries"][0]["tags"] = ["siliconflow", "qwen", "json_mode"]
    body["entries"][1]["model"] = "deepseek-ai/DeepSeek-V4-Pro"
    body["entries"][1]["tags"] = ["siliconflow", "deepseek", "json_mode"]
    config = load_ai_api_config(body)

    filtered = _strict_arithmetic_config(config)

    assert [entry.model for entry in filtered.entries] == ["deepseek-ai/DeepSeek-V4-Pro"]


def test_ai_profile_raw_only_failure_injection_stays_scripted_with_real_transport(
    tmp_path,
    monkeypatch,
) -> None:
    class ExplodingRealTransport:
        def post_chat_completion(self, **_kwargs):
            raise AssertionError("raw-only failure injection must not call real transport")

    monkeypatch.setattr(
        "tokenshare.experiments.ai_profile.UrlLibSiliconFlowTransport",
        lambda: ExplodingRealTransport(),
    )

    report = _run_ai_parse_failure_profile(
        output_dir=tmp_path,
        config=_default_ai_profile_config(),
        seed=23,
        transport=None,
        real_transport=True,
    )

    assert report["status"] == "passed"
    assert report["parse_failure_count"] == 1
    assert report["parsed_output_count"] == 0


def test_run_all_cli_can_optionally_write_ai_profile_suite(tmp_path) -> None:
    exit_code = run_all_main(
        [
            "--output-root",
            str(tmp_path),
            "--seed",
            "17",
            "--run-ai-profile",
        ]
    )

    assert exit_code == 0
    phase8_report = json.loads(
        (tmp_path / "phase8_suite_report.json").read_text(encoding="utf-8")
    )
    ai_report = json.loads(
        (tmp_path / "ai_profile" / "ai_profile_suite_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert phase8_report["ai_profile_suite_report_path"].endswith(
        "ai_profile/ai_profile_suite_report.json"
    )
    assert ai_report["total_profiles"] == 3
