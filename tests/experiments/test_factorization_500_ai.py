import csv
import json
from pathlib import Path

from tokenshare.experiments.factorization_500_ai import (
    DIRECT_ANSWER_SCHEMA_VERSION,
    ScriptedDirectFactorizationTransport,
    evaluate_direct_factorization_answer,
    generate_semiprime_inputs,
    run_factorization_500_ai_suite,
)
from tokenshare.experiments.run_factorization_500_ai import main as run_benchmark_main


def test_generate_semiprime_inputs_creates_500_large_unique_semiprimes() -> None:
    cases = generate_semiprime_inputs(count=500, seed=101)

    assert len(cases) == 500
    assert len({case.target_n for case in cases}) == 500
    for index, case in enumerate(cases):
        assert case.input_index == index
        target_n = int(case.target_n)
        factors = case.oracle_prime_factors
        assert 1_000_000 < target_n < 1_000_000_000
        assert len(factors) == 2
        assert all(factor["exponent"] == 1 for factor in factors)
        assert all(int(factor["prime"]) > 1000 for factor in factors)
        assert all(int(factor["prime"]) <= 5000 for factor in factors)
        assert int(factors[0]["prime"]) * int(factors[1]["prime"]) == target_n


def test_evaluate_direct_factorization_answer_requires_prime_product_and_oracle_match() -> None:
    case = generate_semiprime_inputs(count=1, seed=3)[0]
    correct_answer = {
        "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
        "target_n": case.target_n,
        "prime_factors": list(case.oracle_prime_factors),
    }
    wrong_product = {
        "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
        "target_n": case.target_n,
        "prime_factors": [
            {"prime": case.oracle_prime_factors[0]["prime"], "exponent": 1},
            {"prime": "1009", "exponent": 1},
        ],
    }
    composite_factor = {
        "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
        "target_n": "1022117",
        "prime_factors": [{"prime": "1022117", "exponent": 1}],
    }

    correct = evaluate_direct_factorization_answer(
        correct_answer,
        target_n=case.target_n,
        oracle_prime_factors=case.oracle_prime_factors,
    )
    product_failure = evaluate_direct_factorization_answer(
        wrong_product,
        target_n=case.target_n,
        oracle_prime_factors=case.oracle_prime_factors,
    )
    primality_failure = evaluate_direct_factorization_answer(
        composite_factor,
        target_n="1022117",
        oracle_prime_factors=({"prime": "1009", "exponent": 1}, {"prime": "1013", "exponent": 1}),
    )

    assert correct["final_correctness"] is True
    assert correct["failure_kind"] is None
    assert product_failure["final_correctness"] is False
    assert product_failure["failure_kind"] == "product_mismatch"
    assert primality_failure["final_correctness"] is False
    assert primality_failure["failure_kind"] == "non_prime_factor"


def test_factorization_500_ai_suite_writes_accuracy_outputs_with_scripted_transport(tmp_path) -> None:
    cases = generate_semiprime_inputs(count=4, seed=11)
    report = run_factorization_500_ai_suite(
        output_root=tmp_path,
        count=4,
        seed=11,
        transport=ScriptedDirectFactorizationTransport(cases),
        entry_ids=["factorization_500_scripted"],
    )

    assert report["schema_version"] == "phase8.factorization_500_ai_report.v1"
    assert report["requested_count"] == 4
    assert report["attempted_count"] == 4
    assert report["correct_count"] == 4
    assert report["accuracy"] == 1.0
    assert report["parser_success_rate"] == 1.0
    assert report["parse_failure_count"] == 0
    assert report["executor_error_count"] == 0
    assert report["progress_report_path"].endswith("progress_report.json")

    expected_paths = [
        "factorization_500_ai_settings.json",
        "input_numbers.jsonl",
        "oracle_answers.jsonl",
        "per_number_results.jsonl",
        "per_number_summary.csv",
        "progress_report.json",
        "batch_report.json",
    ]
    for relative_path in expected_paths:
        assert (tmp_path / relative_path).exists()

    settings_text = (tmp_path / "factorization_500_ai_settings.json").read_text(
        encoding="utf-8"
    )
    assert "tokenshare-factorization-500-fake-key" not in settings_text
    settings = json.loads(settings_text)
    assert settings["count"] == 4
    assert settings["number_policy"]["min_exclusive"] == "1000000"
    assert settings["number_policy"]["max_exclusive"] == "1000000000"
    assert settings["number_policy"]["prime_factor_max_inclusive"] == "5000"
    assert settings["number_policy"]["anchor_prime_factor_count"] == 5
    assert settings["execution_policy"]["worker_count"] == 1
    assert settings["requested_entry_ids"] == ["factorization_500_scripted"]

    rows = list(csv.DictReader((tmp_path / "per_number_summary.csv").open(encoding="utf-8")))
    assert len(rows) == 4
    assert all(row["final_correctness"] == "true" for row in rows)
    assert all(row["target_n"] for row in rows)
    assert all(row["models"] for row in rows)
    assert all(row["usage_total_tokens"] == "80" for row in rows)

    result_lines = [
        json.loads(line)
        for line in (tmp_path / "per_number_results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(result_lines) == 4
    assert all(line["event_log_ref"] for line in result_lines)
    assert all(Path(line["artifact_root_path"]).exists() for line in result_lines)


def test_factorization_500_ai_cli_writes_report(tmp_path) -> None:
    exit_code = run_benchmark_main(
        ["--output-root", str(tmp_path), "--count", "3", "--seed", "19"]
    )

    report_path = tmp_path / "batch_report.json"
    assert exit_code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["attempted_count"] == 3
    assert report["accuracy"] == 1.0
