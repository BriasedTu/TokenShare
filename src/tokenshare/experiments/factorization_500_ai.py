"""500 个大整数 factorization 真实 AI API benchmark。"""

from __future__ import annotations

import csv
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import isqrt
from pathlib import Path
from time import sleep
from typing import Any

from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, ProtocolConfig
from tokenshare.core.models import TaskState, TaskUnit
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.models import canonical_json_digest
from tokenshare.plugins.factorization.schemas import PLUGIN_ID, PLUGIN_VERSION
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


DIRECT_ANSWER_SCHEMA_VERSION = "factorization.direct_factorization_answer.v1"
DIRECT_PARSE_FAILURE_SCHEMA_VERSION = "factorization.direct_factorization_parse_failure.v1"
DIRECT_PARSER_ID = "factorization.direct_factorization_answer.parser.v1"
DIRECT_OUTPUT_NAME = "direct_factorization_answer"
NOW = "2026-07-02T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"
DEFAULT_MIN_N = 1_000_000
DEFAULT_MAX_N = 1_000_000_000
DEFAULT_MAX_PRIME_FACTOR = 5_000
DEFAULT_ANCHOR_FACTOR_COUNT = 5
DEFAULT_MAX_TOKENS = 256
DEFAULT_TIMEOUT_SECONDS = 60


SUMMARY_COLUMNS = (
    "input_index",
    "target_n",
    "status",
    "final_correctness",
    "failure_kind",
    "factor_expression",
    "parser_success",
    "raw_output_count",
    "parsed_output_count",
    "parse_failure_count",
    "provider_attempt_count",
    "retry_count",
    "usage_total_tokens",
    "cost_estimate",
    "latency_ms",
    "providers",
    "models",
)


@dataclass(frozen=True)
class BenchmarkInput:
    """一次 direct factorization benchmark 的输入和本地 oracle。"""

    input_index: int
    target_n: str
    oracle_prime_factors: tuple[dict[str, Any], ...]

    def to_input_record(self) -> dict[str, Any]:
        return {
            "schema_version": "phase8.factorization_500_ai_input.v1",
            "input_index": self.input_index,
            "target_n": self.target_n,
        }

    def to_oracle_record(self) -> dict[str, Any]:
        return {
            "schema_version": "phase8.factorization_500_ai_oracle.v1",
            "input_index": self.input_index,
            "target_n": self.target_n,
            "prime_factors": [dict(item) for item in self.oracle_prime_factors],
            "oracle_digest": canonical_json_digest(
                {
                    "target_n": self.target_n,
                    "prime_factors": [dict(item) for item in self.oracle_prime_factors],
                }
            ),
        }


@dataclass(frozen=True, kw_only=True)
class DirectFactorizationParseResult:
    """`AIAPIExecutor` 可理解的 direct factorization parser 结果。"""

    succeeded: bool
    result_kind: str
    parser_id: str
    required_output_name: str | None
    parsed_artifact_schema_id: str | None
    parsed_artifact_schema_version: str | None
    parsed_artifact_body: dict[str, Any] | None
    candidate_output_artifact_bodies: dict[str, dict[str, Any]]
    parse_failure_artifact_body: dict[str, Any] | None


class ScriptedDirectFactorizationTransport:
    """测试用 transport：仍走 AIAPIExecutor，但返回 oracle JSON。"""

    def __init__(self, cases: list[BenchmarkInput] | tuple[BenchmarkInput, ...]) -> None:
        self._cases = list(cases)
        self.calls: list[dict[str, Any]] = []

    def post_chat_completion(
        self,
        *,
        entry,
        api_key: str,
        body: dict[str, Any],
        timeout_seconds: int,
    ):
        if not self._cases:
            raise AssertionError("scripted direct transport has no remaining case")
        case = self._cases.pop(0)
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "body": json.loads(json.dumps(body, sort_keys=True)),
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
            }
        )
        return _ProviderResponse(
            status_code=200,
            body={
                "id": f"factorization-500-scripted-{case.input_index}",
                "model": entry.model,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
                                    "target_n": case.target_n,
                                    "prime_factors": list(case.oracle_prime_factors),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 60,
                    "completion_tokens": 20,
                    "total_tokens": 80,
                },
            },
        )


class _ProviderResponse:
    def __init__(self, *, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def generate_semiprime_inputs(
    *,
    count: int,
    seed: int,
    min_exclusive: int = DEFAULT_MIN_N,
    max_exclusive: int = DEFAULT_MAX_N,
    max_prime_factor: int = DEFAULT_MAX_PRIME_FACTOR,
) -> list[BenchmarkInput]:
    """生成唯一、可复现、处在目标范围内的 semiprime 输入。"""

    _require_positive_int("count", count)
    _require_positive_int("seed", seed)
    primes = [prime for prime in _sieve_primes(max_prime_factor) if prime > 1000]
    rng = random.Random(seed)
    anchor_primes = primes[:DEFAULT_ANCHOR_FACTOR_COUNT]
    candidates: list[tuple[int, int, int]] = []
    for left in anchor_primes:
        for right in primes:
            if right == left:
                continue
            value = left * right
            if min_exclusive < value < max_exclusive:
                low, high = sorted((left, right))
                candidates.append((value, low, high))
    candidates = sorted(set(candidates))
    if len(candidates) < count:
        raise ValueError("not enough semiprime candidates for requested count")
    rng.shuffle(candidates)
    selected = sorted(candidates[:count], key=lambda item: item[0])
    return [
        BenchmarkInput(
            input_index=index,
            target_n=str(value),
            oracle_prime_factors=(
                {"prime": str(left), "exponent": 1},
                {"prime": str(right), "exponent": 1},
            ),
        )
        for index, (value, left, right) in enumerate(selected)
    ]


def parse_direct_factorization_ai_output(
    raw_output: dict[str, Any] | str | None,
    *,
    target_n: str,
    raw_output_ref_summary: dict[str, Any],
    created_at: str,
) -> DirectFactorizationParseResult:
    """解析模型直接返回的 prime factorization JSON，不做正确性宽恕。"""

    if raw_output is None:
        return _direct_parse_failure(
            failure_kind="raw_output_missing",
            message="direct factorization requires a structured JSON object",
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )
    try:
        body = _structured_json_object(raw_output)
        if body.get("schema_version") != DIRECT_ANSWER_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {DIRECT_ANSWER_SCHEMA_VERSION}")
        if body.get("target_n") != target_n:
            raise ValueError("target_n mismatch")
        factors = _normalize_prime_factors(body.get("prime_factors"))
    except (TypeError, ValueError) as exc:
        return _direct_parse_failure(
            failure_kind="invalid_direct_factorization_answer",
            message=str(exc),
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    parsed_body = {
        "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
        "target_n": target_n,
        "prime_factors": [dict(item) for item in factors],
        "answer_digest": canonical_json_digest(
            {
                "target_n": target_n,
                "prime_factors": [dict(item) for item in factors],
            }
        ),
        "created_at": created_at,
    }
    return DirectFactorizationParseResult(
        succeeded=True,
        result_kind="parsed",
        parser_id=DIRECT_PARSER_ID,
        required_output_name=DIRECT_OUTPUT_NAME,
        parsed_artifact_schema_id="factorization.direct_factorization_answer",
        parsed_artifact_schema_version="v1",
        parsed_artifact_body=parsed_body,
        candidate_output_artifact_bodies={DIRECT_OUTPUT_NAME: parsed_body},
        parse_failure_artifact_body=None,
    )


def evaluate_direct_factorization_answer(
    answer_body: dict[str, Any],
    *,
    target_n: str,
    oracle_prime_factors: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> dict[str, Any]:
    """本地计算 direct factorization answer 是否准确。"""

    try:
        if answer_body.get("schema_version") != DIRECT_ANSWER_SCHEMA_VERSION:
            raise ValueError("schema_version mismatch")
        if answer_body.get("target_n") != target_n:
            return _evaluation(False, "target_mismatch", target_n, (), oracle_prime_factors)
        factors = _normalize_prime_factors(answer_body.get("prime_factors"))
    except (TypeError, ValueError) as exc:
        return _evaluation(
            False,
            "invalid_answer_schema",
            target_n,
            (),
            oracle_prime_factors,
            message=str(exc),
        )

    product = 1
    for item in factors:
        product *= int(item["prime"]) ** int(item["exponent"])
    if product != int(target_n):
        return _evaluation(False, "product_mismatch", target_n, factors, oracle_prime_factors)
    if any(not _is_prime(int(item["prime"])) for item in factors):
        return _evaluation(False, "non_prime_factor", target_n, factors, oracle_prime_factors)
    oracle = tuple(_normalize_prime_factors(oracle_prime_factors))
    if tuple(factors) != oracle:
        return _evaluation(False, "oracle_mismatch", target_n, factors, oracle_prime_factors)
    return _evaluation(True, None, target_n, factors, oracle_prime_factors)


def run_factorization_500_ai_suite(
    *,
    output_root: str | Path,
    count: int = 500,
    seed: int = 1,
    ai_api_config: AIAPIExecutorConfig | None = None,
    transport: Any | None = None,
    real_transport: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_provider_attempts: int = 1,
    entry_ids: list[str] | tuple[str, ...] | None = None,
    worker_count: int = 1,
) -> dict[str, Any]:
    """运行 direct 500-number AI benchmark 并写出准确率报告。"""

    _require_positive_int("worker_count", worker_count)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "TOKENSHARE_FACTORIZATION_500_FAKE_KEY",
        "tokenshare-factorization-500-fake-key",
    )
    cases = generate_semiprime_inputs(count=count, seed=seed)
    config = _prepare_config(
        ai_api_config or _default_direct_config(),
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        max_provider_attempts=max_provider_attempts,
        entry_ids=entry_ids,
    )
    active_transport = transport
    if active_transport is None:
        active_transport = UrlLibSiliconFlowTransport() if real_transport else ScriptedDirectFactorizationTransport(cases)

    _write_jsonl(root / "input_numbers.jsonl", [case.to_input_record() for case in cases])
    _write_jsonl(root / "oracle_answers.jsonl", [case.to_oracle_record() for case in cases])
    _write_settings(
        root / "factorization_500_ai_settings.json",
        count=count,
        seed=seed,
        real_transport=real_transport,
        config=config,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        max_provider_attempts=max_provider_attempts,
        entry_ids=entry_ids,
        worker_count=worker_count,
        output_root=root,
    )
    _write_current_outputs(root=root, count=count, results=[], config=config)

    if worker_count == 1:
        results = _run_cases_sequentially(
            root=root,
            cases=cases,
            config=config,
            transport=active_transport,
            seed=seed,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            count=count,
        )
    else:
        results = _run_cases_concurrently(
            root=root,
            cases=cases,
            config=config,
            transport=active_transport,
            seed=seed,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            count=count,
            worker_count=worker_count,
        )

    _write_current_outputs(root=root, count=count, results=results, config=config)
    report = _batch_report(root=root, seed=seed, count=count, results=results, config=config)
    _write_json(root / "batch_report.json", report)
    return report


def _run_cases_sequentially(
    *,
    root: Path,
    cases: list[BenchmarkInput],
    config: AIAPIExecutorConfig,
    transport: Any,
    seed: int,
    max_tokens: int,
    timeout_seconds: int,
    count: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        results.append(
            _run_one_direct_case(
                root=root,
                case=case,
                config=config,
                transport=transport,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        )
        _write_current_outputs(root=root, count=count, results=results, config=config)
    return results


def _run_cases_concurrently(
    *,
    root: Path,
    cases: list[BenchmarkInput],
    config: AIAPIExecutorConfig,
    transport: Any,
    seed: int,
    max_tokens: int,
    timeout_seconds: int,
    count: int,
    worker_count: int,
) -> list[dict[str, Any]]:
    results_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _run_one_direct_case,
                root=root,
                case=case,
                config=config,
                transport=transport,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            for case in cases
        ]
        for future in as_completed(futures):
            result = future.result()
            results_by_index[int(result["input_index"])] = result
            completed = [results_by_index[index] for index in sorted(results_by_index)]
            _write_current_outputs(root=root, count=count, results=completed, config=config)
    return [results_by_index[index] for index in range(len(cases)) if index in results_by_index]


def _write_current_outputs(
    *,
    root: Path,
    count: int,
    results: list[dict[str, Any]],
    config: AIAPIExecutorConfig,
) -> None:
    _write_jsonl(root / "per_number_results.jsonl", results)
    _write_summary_csv(root / "per_number_summary.csv", results)
    _write_progress_report(
        root / "progress_report.json",
        count=count,
        results=results,
        config=config,
    )


def _run_one_direct_case(
    *,
    root: Path,
    case: BenchmarkInput,
    config: AIAPIExecutorConfig,
    transport: Any,
    seed: int,
    max_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_dir = root / "runs" / f"run_{case.input_index:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(run_dir)
    ledger = EventLedger(run_dir / "events" / "event_log.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=ProtocolConfig.default(
            config_id="config_factorization_500_ai",
            artifact_store_uri=f"file://{store.artifact_dir.as_posix()}",
            event_log_uri=f"file://{ledger.path.as_posix()}",
            metadata={"suite": "factorization_500_ai"},
        ),
        artifact_store=store,
    )
    request = _build_direct_execution_request(
        store=store,
        case=case,
        seed=seed,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    request_flow = engine.record_execution_request(
        request=request,
        correlation_id=f"corr_factorization_500_request_{case.input_index}",
    )
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw, *, raw_output_ref_summary, created_at: parse_direct_factorization_ai_output(
            raw,
            target_n=case.target_n,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        ),
    )
    submission = executor.execute(
        request,
        submission_id=f"submission_factorization_500_{case.input_index}",
        submitted_at=NOW,
    )
    attempt, lease = _attempt_and_lease(request)
    engine.record_execution_submission(
        submission=submission,
        attempt=attempt,
        lease=lease,
        correlation_id=f"corr_factorization_500_submission_{case.input_index}",
        causation_event_id=request_flow.event.event_id,
    )

    evaluation = _submission_evaluation(
        store=store,
        case=case,
        submission=submission,
    )
    usage = _usage_from_submission(store, submission)
    raw_count = 1 if submission.raw_output_ref is not None else 0
    parsed_count = 1 if submission.parsed_output_ref is not None else 0
    parse_failure_count = 1 if submission.parse_failure_ref is not None else 0
    executor_error_count = 1 if submission.result_kind == "executor_error" else 0
    status = "passed" if evaluation["final_correctness"] else "failed"
    return {
        "schema_version": "phase8.factorization_500_ai_case_report.v1",
        "input_index": case.input_index,
        "target_n": case.target_n,
        "status": status,
        "executor_profile": "ai_api",
        "submission_result_kind": submission.result_kind,
        "final_correctness": evaluation["final_correctness"],
        "failure_kind": evaluation["failure_kind"],
        "factor_expression": evaluation["factor_expression"],
        "normalized_prime_factors": evaluation["normalized_prime_factors"],
        "oracle_prime_factors": [dict(item) for item in case.oracle_prime_factors],
        "product_check_passed": evaluation["product_check_passed"],
        "primality_check_passed": evaluation["primality_check_passed"],
        "oracle_match": evaluation["oracle_match"],
        "parser_success": parsed_count == 1,
        "raw_output_count": raw_count,
        "parsed_output_count": parsed_count,
        "parse_failure_count": parse_failure_count,
        "executor_error_count": executor_error_count,
        "provider_attempt_count": usage["provider_attempt_count"],
        "retry_count": max(0, usage["provider_attempt_count"] - 1),
        "usage": {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
        },
        "usage_prompt_tokens": usage["prompt_tokens"],
        "usage_completion_tokens": usage["completion_tokens"],
        "usage_total_tokens": usage["total_tokens"],
        "cost_estimate": usage["cost_estimate_total"],
        "latency_ms": usage["latency_ms_total"],
        "providers": sorted(usage["providers"]),
        "models": sorted(usage["models"]),
        "request_ref": request_flow.request_ref.to_dict(),
        "raw_output_ref": submission.raw_output_ref.to_dict() if submission.raw_output_ref else None,
        "parsed_output_ref": submission.parsed_output_ref.to_dict() if submission.parsed_output_ref else None,
        "parse_failure_ref": submission.parse_failure_ref.to_dict() if submission.parse_failure_ref else None,
        "provenance_ref": submission.provenance_ref.to_dict() if submission.provenance_ref else None,
        "event_log_ref": ledger.path.as_posix(),
        "artifact_root_path": store.artifact_dir.as_posix(),
    }


def _build_direct_execution_request(
    *,
    store: ArtifactStore,
    case: BenchmarkInput,
    seed: int,
    max_tokens: int,
    timeout_seconds: int,
) -> ExecutionRequest:
    task_id = f"task_factorization_500_{case.input_index}"
    unit_id = f"unit_factorization_500_{case.input_index}"
    request_id = f"request_factorization_500_{case.input_index}"
    input_ref = store.save_json(
        case.to_input_record(),
        artifact_id=f"input_number_{case.input_index}",
        artifact_type="Factorization500Input",
        artifact_schema_id="phase8.factorization_500_ai_input",
        artifact_schema_version="v1",
        source={"kind": "factorization_500_ai_benchmark"},
        metadata={"input_index": case.input_index},
        created_at=NOW,
    )
    prompt_ref = store.save_json(
        _prompt_package(case=case, request_id=request_id, task_id=task_id, unit_id=unit_id, seed=seed),
        artifact_id=f"prompt_factorization_500_{case.input_index}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "factorization_500_ai_benchmark"},
        metadata={"input_index": case.input_index},
        created_at=NOW,
    )
    descriptor = build_factorization_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"attempt_factorization_500_{case.input_index}",
        lease_id=f"lease_factorization_500_{case.input_index}",
        fencing_token=f"fence_factorization_500_{case.input_index}",
        plugin={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": descriptor.descriptor_digest,
            "ai_output_parser_policy_id": DIRECT_PARSER_ID,
        },
        executor={"executor_id": AI_EXECUTOR_ID, "executor_version": AI_EXECUTOR_VERSION},
        registry_snapshot_id="registry_snapshot_factorization_500_ai",
        allocation_decision={
            "decision_id": f"allocation_factorization_500_{case.input_index}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=_task_unit(task_id=task_id, unit_id=unit_id, case=case).to_dict(),
        input_artifact_refs={"input_number": input_ref},
        output_contract=_direct_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.0},
        environment_ref=_environment_ref(seed=seed),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": timeout_seconds, "max_tokens": max_tokens},
        created_at=NOW,
    )


def _prompt_package(
    *,
    case: BenchmarkInput,
    request_id: str,
    task_id: str,
    unit_id: str,
    seed: int,
) -> dict[str, Any]:
    return {
        "schema_version": "phase3.prompt_package.v1",
        "prompt_id": f"prompt_factorization_500:{case.input_index}",
        "request_id": request_id,
        "task_id": task_id,
        "unit_id": unit_id,
        "prompt_text": (
            "Factor the given semiprime integer by choosing prime factors only from "
            "the provided candidate_prime_factors list. Compute silently. Return exactly "
            "one compact JSON object and nothing else. Do not include markdown, analysis, "
            "steps, proof, code fences, or explanatory text."
        ),
        "input_summary": {
            "target_n": case.target_n,
            "target_n_min_exclusive": str(DEFAULT_MIN_N),
            "target_n_max_exclusive": str(DEFAULT_MAX_N),
            "semiprime_policy": (
                "two prime factors, each between 1001 and 5000 inclusive; "
                "at least one factor is in candidate_prime_factors"
            ),
            "candidate_prime_factors": _anchor_prime_factor_strings(),
            "candidate_prime_factor_count": len(_anchor_prime_factor_strings()),
            "input_index": case.input_index,
        },
        "output_schema": {
            "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
            "required_shape": {
                "schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
                "target_n": case.target_n,
                "prime_factors": [
                    {"prime": "<decimal prime string>", "exponent": "<positive integer>"}
                ],
            },
            "rules": [
                "prime_factors must be sorted by numeric prime ascending",
                "prime_factors product must equal target_n",
                "each prime field must be a decimal string",
                "each exponent must be a positive JSON integer",
            ],
        },
        "constraints": {
            "requires_json_mode": False,
            "format": "json",
            "raw_only_allowed": False,
            "seed": seed,
        },
        "created_at": NOW,
    }


def _submission_evaluation(*, store: ArtifactStore, case: BenchmarkInput, submission) -> dict[str, Any]:
    if submission.candidate_output_refs.get(DIRECT_OUTPUT_NAME) is None:
        return _evaluation(
            False,
            submission.result_kind if submission.result_kind != "succeeded" else "parse_failed",
            case.target_n,
            (),
            case.oracle_prime_factors,
        )
    body = json.loads(
        store.read_bytes(submission.candidate_output_refs[DIRECT_OUTPUT_NAME]).decode("utf-8")
    )
    return evaluate_direct_factorization_answer(
        body,
        target_n=case.target_n,
        oracle_prime_factors=case.oracle_prime_factors,
    )


def _usage_from_submission(store: ArtifactStore, submission) -> dict[str, Any]:
    usage = submission.usage_summary or {}
    provider_attempt_count = _int_metric(usage.get("provider_attempt_count"))
    latency_ms_total = _latency_from_provenance(store, submission)
    providers: set[str] = set()
    models: set[str] = set()
    provider = usage.get("provider_family")
    model = usage.get("model")
    if isinstance(provider, str) and provider:
        providers.add(provider)
    if isinstance(model, str) and model:
        models.add(model)
    return {
        "provider_attempt_count": provider_attempt_count,
        "cost_estimate_total": _float_metric(usage.get("cost_estimate")),
        "latency_ms_total": latency_ms_total,
        "prompt_tokens": _int_metric(usage.get("prompt_tokens")),
        "completion_tokens": _int_metric(usage.get("completion_tokens")),
        "total_tokens": _int_metric(usage.get("total_tokens")),
        "providers": providers,
        "models": models,
    }


def _latency_from_provenance(store: ArtifactStore, submission) -> int:
    if submission.provenance_ref is None:
        return 0
    try:
        body = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0
    attempts = body.get("attempts", [])
    if not isinstance(attempts, list):
        return 0
    return sum(
        _int_metric(attempt.get("latency_ms"))
        for attempt in attempts
        if isinstance(attempt, dict)
    )


def _batch_report(
    *,
    root: Path,
    seed: int,
    count: int,
    results: list[dict[str, Any]],
    config: AIAPIExecutorConfig,
) -> dict[str, Any]:
    attempted = len(results)
    correct = sum(1 for item in results if item["final_correctness"])
    raw_count = sum(item["raw_output_count"] for item in results)
    parsed_count = sum(item["parsed_output_count"] for item in results)
    parse_failure_count = sum(item["parse_failure_count"] for item in results)
    executor_error_count = sum(item["executor_error_count"] for item in results)
    provider_attempt_count = sum(item["provider_attempt_count"] for item in results)
    total_tokens = sum(item["usage"]["total_tokens"] for item in results)
    prompt_tokens = sum(item["usage"]["prompt_tokens"] for item in results)
    completion_tokens = sum(item["usage"]["completion_tokens"] for item in results)
    cost = sum(float(item["cost_estimate"]) for item in results)
    latency = sum(int(item["latency_ms"]) for item in results)
    failures: dict[str, int] = {}
    for item in results:
        if item["final_correctness"]:
            continue
        key = str(item["failure_kind"] or "unknown")
        failures[key] = failures.get(key, 0) + 1
    return {
        "schema_version": "phase8.factorization_500_ai_report.v1",
        "seed": seed,
        "requested_count": count,
        "attempted_count": attempted,
        "correct_count": correct,
        "failed_count": attempted - correct,
        "accuracy": (correct / attempted) if attempted else 0.0,
        "parser_success_rate": (parsed_count / attempted) if attempted else 0.0,
        "raw_output_count": raw_count,
        "parsed_output_count": parsed_count,
        "parse_failure_count": parse_failure_count,
        "executor_error_count": executor_error_count,
        "provider_attempt_count": provider_attempt_count,
        "retry_count": sum(item["retry_count"] for item in results),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "cost_estimate_total": cost,
        "latency_ms_total": latency,
        "providers": sorted({provider for item in results for provider in item["providers"]}),
        "models": sorted({model for item in results for model in item["models"]}),
        "failure_breakdown": failures,
        "number_policy": {
            "kind": "deterministic_semiprime",
            "min_exclusive": str(DEFAULT_MIN_N),
            "max_exclusive": str(DEFAULT_MAX_N),
            "prime_factor_min_exclusive": "1000",
            "prime_factor_max_inclusive": str(DEFAULT_MAX_PRIME_FACTOR),
            "anchor_prime_factor_count": DEFAULT_ANCHOR_FACTOR_COUNT,
            "anchor_prime_factors": _anchor_prime_factor_strings(),
        },
        "ai_api_config_digest": config.config_digest,
        "settings_path": (root / "factorization_500_ai_settings.json").as_posix(),
        "input_numbers_path": (root / "input_numbers.jsonl").as_posix(),
        "oracle_answers_path": (root / "oracle_answers.jsonl").as_posix(),
        "per_number_results_path": (root / "per_number_results.jsonl").as_posix(),
        "per_number_summary_path": (root / "per_number_summary.csv").as_posix(),
        "batch_report_path": (root / "batch_report.json").as_posix(),
        "progress_report_path": (root / "progress_report.json").as_posix(),
    }


def _write_progress_report(
    path: Path,
    *,
    count: int,
    results: list[dict[str, Any]],
    config: AIAPIExecutorConfig,
) -> None:
    attempted = len(results)
    correct = sum(1 for item in results if item["final_correctness"])
    parsed_count = sum(item["parsed_output_count"] for item in results)
    executor_error_count = sum(item["executor_error_count"] for item in results)
    parse_failure_count = sum(item["parse_failure_count"] for item in results)
    _write_json(
        path,
        {
            "schema_version": "phase8.factorization_500_ai_progress.v1",
            "requested_count": count,
            "completed_count": attempted,
            "remaining_count": max(0, count - attempted),
            "correct_count": correct,
            "failed_count": attempted - correct,
            "accuracy_so_far": (correct / attempted) if attempted else 0.0,
            "parser_success_rate_so_far": (parsed_count / attempted) if attempted else 0.0,
            "parse_failure_count": parse_failure_count,
            "executor_error_count": executor_error_count,
            "provider_attempt_count": sum(item["provider_attempt_count"] for item in results),
            "latency_ms_total": sum(int(item["latency_ms"]) for item in results),
            "usage_total_tokens": sum(item["usage"]["total_tokens"] for item in results),
            "ai_api_config_digest": config.config_digest,
        },
    )


def _evaluation(
    final_correctness: bool,
    failure_kind: str | None,
    target_n: str,
    factors: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    oracle_prime_factors: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    message: str | None = None,
) -> dict[str, Any]:
    normalized = [dict(item) for item in factors]
    oracle = [dict(item) for item in oracle_prime_factors]
    product = 1
    for item in normalized:
        product *= int(item["prime"]) ** int(item["exponent"])
    return {
        "final_correctness": final_correctness,
        "failure_kind": failure_kind,
        "message": message,
        "target_n": target_n,
        "normalized_prime_factors": normalized,
        "oracle_prime_factors": oracle,
        "factor_expression": _factor_expression(normalized),
        "product_check_passed": bool(normalized) and product == int(target_n),
        "primality_check_passed": bool(normalized)
        and all(_is_prime(int(item["prime"])) for item in normalized),
        "oracle_match": normalized == oracle,
    }


def _normalize_prime_factors(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("prime_factors must be a non-empty list")
    counts: dict[int, int] = {}
    for item in value:
        if isinstance(item, str):
            prime_raw = item
            exponent = 1
        elif isinstance(item, int) and not isinstance(item, bool):
            prime_raw = str(item)
            exponent = 1
        elif isinstance(item, dict):
            prime_raw = item.get("prime")
            exponent = item.get("exponent", 1)
        else:
            raise ValueError("each prime factor must be an object, string, or integer")
        if not isinstance(prime_raw, str) or not prime_raw.isdecimal():
            raise ValueError("prime must be a decimal string")
        if isinstance(exponent, bool) or not isinstance(exponent, int) or exponent < 1:
            raise ValueError("exponent must be a positive integer")
        prime = int(prime_raw)
        if prime < 2:
            raise ValueError("prime must be >= 2")
        counts[prime] = counts.get(prime, 0) + exponent
    return tuple(
        {"prime": str(prime), "exponent": counts[prime]}
        for prime in sorted(counts)
    )


def _structured_json_object(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("direct factorization parser requires a JSON object") from exc
        if not isinstance(decoded, dict):
            raise ValueError("direct factorization parser requires a JSON object")
        return decoded
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError("direct factorization parser requires a JSON object")


def _direct_parse_failure(
    *,
    failure_kind: str,
    message: str,
    raw_output: dict[str, Any] | str | None,
    raw_output_ref_summary: dict[str, Any],
    created_at: str,
) -> DirectFactorizationParseResult:
    return DirectFactorizationParseResult(
        succeeded=False,
        result_kind="parse_failed",
        parser_id=DIRECT_PARSER_ID,
        required_output_name=None,
        parsed_artifact_schema_id=None,
        parsed_artifact_schema_version=None,
        parsed_artifact_body=None,
        candidate_output_artifact_bodies={},
        parse_failure_artifact_body={
            "schema_version": DIRECT_PARSE_FAILURE_SCHEMA_VERSION,
            "parser_id": DIRECT_PARSER_ID,
            "failure_kind": failure_kind,
            "message": message,
            "raw_excerpt": _raw_excerpt(raw_output),
            "raw_output_ref_summary": dict(raw_output_ref_summary),
            "candidate_outputs": {},
            "created_at": created_at,
        },
    )


def _attempt_and_lease(request: ExecutionRequest) -> tuple[Attempt, Lease]:
    attempt = Attempt(
        attempt_id=request.attempt_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        lease_id=request.lease_id,
        client_id=AI_EXECUTOR_ID,
        state=AttemptState.RUNNING,
        attempt_kind="exclusive",
        created_at=NOW,
        started_at=NOW,
        finished_at=None,
        input_artifact_refs=request.input_artifact_refs,
        candidate_output_refs=None,
        metadata={"factorization_500_ai": True},
    )
    lease = Lease(
        lease_id=request.lease_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        client_id=AI_EXECUTOR_ID,
        attempt_id=request.attempt_id,
        state=LeaseState.ACTIVE,
        issued_at=NOW,
        expires_at="2026-07-02T00:30:00Z",
        fencing_token=request.fencing_token,
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="exclusive",
        terminated_at=None,
        terminated_reason=None,
        metadata={"factorization_500_ai": True},
    )
    return attempt, lease


def _task_unit(*, task_id: str, unit_id: str, case: BenchmarkInput) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=None,
        depth=1,
        unit_type="factor_integer",
        state=TaskState.PROCESSING,
        input_refs={},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "factorization": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={"target_n": case.target_n, "benchmark": "factorization_500_ai"},
        metadata={"factorization_500_ai": True, "input_index": case.input_index},
        created_at=NOW,
        updated_at=NOW,
    )


def _direct_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id="factorization.direct_factorization_answer.contract.v1",
        required_outputs=[DIRECT_OUTPUT_NAME],
        output_schema_refs={
            DIRECT_OUTPUT_NAME: {
                "artifact_schema_id": "factorization.direct_factorization_answer",
                "artifact_schema_version": "v1",
            }
        },
        raw_output_policy={"allowed": True, "media_type": "application/json"},
        parsed_output_schema_ref={
            "artifact_schema_id": "factorization.direct_factorization_answer",
            "artifact_schema_version": "v1",
        },
    )


def _environment_ref(*, seed: int) -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_factorization_500_ai",
        environment_digest="sha256:env_factorization_500_ai",
        runtime="python",
        tool_versions={"ai_api_executor": AI_EXECUTOR_VERSION},
        resource_limits={"timeout_seconds": 60},
        fixture_profile_digest="sha256:factorization_500_ai",
        seed=seed,
        clock_policy="fixed",
        created_at=NOW,
    )


def _prepare_config(
    config: AIAPIExecutorConfig,
    *,
    max_tokens: int,
    timeout_seconds: int,
    max_provider_attempts: int,
    entry_ids: list[str] | tuple[str, ...] | None = None,
) -> AIAPIExecutorConfig:
    requested_entry_ids = tuple(entry_ids or ())
    if requested_entry_ids:
        requested = set(requested_entry_ids)
        kept_entries = [entry for entry in config.entries if entry.entry_id in requested]
        if not kept_entries:
            raise ValueError(f"no configured ai api entries matched: {sorted(requested)}")
        filter_reason = "explicit benchmark entry filter"
    else:
        strict_entries = [
            entry
            for entry in config.entries
            if not _known_strict_arithmetic_mismatch_model(entry)
        ]
        preferred_entries = [
            entry for entry in strict_entries if _preferred_direct_factorization_model(entry)
        ]
        kept_entries = preferred_entries or strict_entries or list(config.entries)
        filter_reason = (
            "direct factorization benchmark requires strict JSON arithmetic; "
            "MiniMax is preferred when available because prior real AI profile "
            "runs completed reliably on arithmetic JSON tasks"
        )
    defaults = {
        **dict(config.defaults),
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "temperature": 0.0,
        "max_provider_attempts": max_provider_attempts,
    }
    return AIAPIExecutorConfig(
        schema_version=config.schema_version,
        executor_id=config.executor_id,
        provider_family=config.provider_family,
        selection_policy=dict(config.selection_policy),
        defaults=defaults,
        entries=kept_entries,
        local_concurrency=dict(config.local_concurrency),
        metadata={
            **dict(config.metadata),
            "factorization_500_ai": True,
            "strict_arithmetic_model_filter": {
                "excluded_entry_ids": [
                    entry.entry_id for entry in config.entries if entry not in kept_entries
                ],
                "preferred_entry_ids": [entry.entry_id for entry in kept_entries],
                "requested_entry_ids": list(requested_entry_ids),
                "reason": filter_reason,
            },
        },
    )


def _default_direct_config() -> AIAPIExecutorConfig:
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": AI_EXECUTOR_ID,
            "provider_family": "siliconflow",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "factorization_500_scripted",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": "TOKENSHARE_FACTORIZATION_500_FAKE_KEY",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["factorization_500_ai", "fixture"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "factorization-500-ai-scripted"},
        }
    )


def _known_strict_arithmetic_mismatch_model(entry) -> bool:
    model = entry.model.lower()
    tags = {tag.lower() for tag in entry.tags}
    return "qwen3" in model or "qwen" in tags


def _preferred_direct_factorization_model(entry) -> bool:
    model = entry.model.lower()
    tags = {tag.lower() for tag in entry.tags}
    return "minimax" in model or "minimax" in tags


def _write_settings(
    path: Path,
    *,
    count: int,
    seed: int,
    real_transport: bool,
    config: AIAPIExecutorConfig,
    max_tokens: int,
    timeout_seconds: int,
    max_provider_attempts: int,
    entry_ids: list[str] | tuple[str, ...] | None,
    worker_count: int,
    output_root: Path,
) -> None:
    body = {
        "schema_version": "phase8.factorization_500_ai_settings.v1",
        "count": count,
        "seed": seed,
        "real_transport": real_transport,
        "output_root": output_root.as_posix(),
        "number_policy": {
            "kind": "deterministic_semiprime",
            "min_exclusive": str(DEFAULT_MIN_N),
            "max_exclusive": str(DEFAULT_MAX_N),
            "prime_factor_min_exclusive": "1000",
            "prime_factor_max_inclusive": str(DEFAULT_MAX_PRIME_FACTOR),
            "anchor_prime_factor_count": DEFAULT_ANCHOR_FACTOR_COUNT,
            "anchor_prime_factors": _anchor_prime_factor_strings(),
        },
        "request_limits": {
            "timeout_seconds": timeout_seconds,
            "max_tokens": max_tokens,
            "max_provider_attempts": max_provider_attempts,
        },
        "execution_policy": {
            "worker_count": worker_count,
            "case_isolation": "one artifact/event directory per input_index",
        },
        "requested_entry_ids": list(entry_ids or ()),
        "prompt_constraints": {"requires_json_mode": False, "format": "json"},
        "parser_policy": {
            "parser_id": DIRECT_PARSER_ID,
            "parse_required": True,
            "raw_only_allowed": False,
        },
        "accuracy_definition": {
            "numerator": "tasks with product, primality, and oracle multiset checks all passing",
            "denominator": "attempted_count",
        },
        "ai_api_config": config.to_safe_dict(),
        "ai_api_config_digest": config.config_digest,
    }
    _write_json(path, body)


def _write_summary_csv(path: Path, results: list[dict[str, Any]]) -> None:
    def write() -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
            writer.writeheader()
            for result in results:
                writer.writerow(
                    {column: _csv_value(result.get(column)) for column in SUMMARY_COLUMNS}
                )

    _retry_file_write(path=path, writer=write)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def write() -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    _retry_file_write(path=path, writer=write)


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)

    def write() -> None:
        path.write_text(text, encoding="utf-8")

    _retry_file_write(path=path, writer=write)


def _retry_file_write(*, path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            writer()
            return
        except OSError as exc:
            last_error = exc
            sleep(0.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _sieve_primes(limit: int) -> list[int]:
    sieve = [True] * (limit + 1)
    sieve[0:2] = [False, False]
    for value in range(2, isqrt(limit) + 1):
        if not sieve[value]:
            continue
        start = value * value
        sieve[start : limit + 1 : value] = [False] * (((limit - start) // value) + 1)
    return [value for value, flag in enumerate(sieve) if flag]


def _candidate_prime_factor_strings() -> list[str]:
    return [
        str(prime)
        for prime in _sieve_primes(DEFAULT_MAX_PRIME_FACTOR)
        if prime > 1000
    ]


def _anchor_prime_factor_strings() -> list[str]:
    return _candidate_prime_factor_strings()[:DEFAULT_ANCHOR_FACTOR_COUNT]


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value in {2, 3}:
        return True
    if value % 2 == 0:
        return False
    divisor = 3
    bound = isqrt(value)
    while divisor <= bound:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _factor_expression(factors: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    if not factors:
        return ""
    parts = []
    for item in factors:
        prime = str(item["prime"])
        exponent = int(item["exponent"])
        parts.append(prime if exponent == 1 else f"{prime}^{exponent}")
    return " * ".join(parts)


def _raw_excerpt(raw_output: dict[str, Any] | str | None, *, max_chars: int = 200) -> str | None:
    if raw_output is None:
        return None
    text = raw_output if isinstance(raw_output, str) else json.dumps(raw_output, ensure_ascii=False)
    return text if len(text) <= max_chars else f"{text[:max_chars]}..."


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _int_metric(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_metric(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
