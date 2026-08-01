from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter
import tokenshare.experiments.paper_catalog as paper_catalog_module

from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperTaskStatus,
)
from tests.support.lean_checker import RecordingLeanChecker


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"


@pytest.fixture(autouse=True)
def _use_tracked_lean_environment_and_recording_checker(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    tracked = json.loads(
        (repo_root / "benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    environment = paper_catalog_module._current_lean_environment_manifest_without_preflight()
    object.__setattr__(environment, "environment_digest", tracked["environment_digest"])
    oracle_source = (
        repo_root / "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()
    original_file_digest = paper_catalog_module._file_digest

    def worktree_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_file_digest(resolved)
        source = resolved.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{sha256(source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: environment,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked["checker_implementation_digest"],
    )
    monkeypatch.setattr(paper_catalog_module, "_file_digest", worktree_file_digest)
    checker = (
        RecordingLeanChecker.reject_all()
        if request.node.name.startswith(
            "test_gate_c_semantically_wrong_json_reaches_authoritative_rejection"
        )
        and getattr(request.node, "callspec", None) is not None
        and request.node.callspec.params.get("domain") == "lean_proof"
        else RecordingLeanChecker()
    )
    monkeypatch.setattr(lean_paper_adapter, "check_lean_proof", checker)
    monkeypatch.setattr(
        lean_paper_adapter,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )


def test_gate_c_real_mode_allows_explicit_offline_capture_without_paper_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, condition = _case_and_condition("factorization", fixed_identity=True)
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    selected_entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(selected_entry.api_key_env, "gate-c-offline-capture-key")
    transport = _CapturingTransport(domain="factorization", mutation="valid")

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "real-mode-capture").as_posix(),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=selected_entry.entry_id,
        max_tokens=1024,
        timeout_seconds=30,
    )

    assert transport.calls
    assert result.task_result.paper_eligible is False
    assert result.eligibility_report.paper_eligible is False
    assert "unsupported_transport:capturing" in (
        result.eligibility_report.ineligibility_reasons
    )
    assert all(attempt.paper_eligible is False for attempt in result.attempt_results)
    assert all(
        attempt.model_execution_record_ref is not None
        for attempt in result.attempt_results
    )
    model_records = _artifact_bodies(
        Path(result.output_root),
        "PaperModelExecutionRecord",
    )
    assert model_records
    assert all(record["paper_eligible"] is False for record in model_records)
    assert result.run_evidence["transport_evidence"] == {
        **result.run_evidence["transport_evidence"],
        "real_transport": False,
        "transport_kind": "capturing",
        "config_source": "offline_capturing_config",
    }


@pytest.mark.parametrize("domain", ("factorization", "lean_proof"))
def test_gate_c_capturing_transport_persists_valid_structured_output_chain(
    tmp_path: Path,
    domain: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, condition = _case_and_condition(domain, fixed_identity=True)
    transport = _CapturingTransport(domain=domain, mutation="valid")
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-valid-capture-key")

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / domain).as_posix(),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=1024,
        timeout_seconds=30,
    )

    assert transport.calls
    assert all(call["content_type"] == "application/json" for call in transport.calls)
    assert all(call["normalized_absolute_endpoint"].startswith("https://") for call in transport.calls)
    assert all(
        json.loads(call["body_bytes"].decode("utf-8"))["response_format"]
        == {"type": "json_object"}
        for call in transport.calls
    )
    assert all(
        json.loads(call["body_bytes"].decode("utf-8"))["enable_thinking"] is False
        for call in transport.calls
    )
    assert all(
        json.loads(call["body_bytes"].decode("utf-8"))["temperature"] == 0
        for call in transport.calls
    )
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert all(attempt.raw_output_ref for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref for attempt in result.attempt_results)
    assert all(attempt.parse_failure_ref is None for attempt in result.attempt_results)
    artifact_types = _artifact_types(Path(result.output_root))
    assert "RawModelOutput" in artifact_types
    assert "ParsedModelOutput" in artifact_types
    assert "CandidateOutput" in artifact_types
    prompt_bodies = _artifact_bodies(Path(result.output_root), "PromptPackage")
    assert prompt_bodies
    assert all(
        body["constraints"]["strict_json_only"] is True
        and body["constraints"]["requires_json_mode"] is True
        for body in prompt_bodies
    )


@pytest.mark.parametrize("domain", ("factorization", "lean_proof"))
@pytest.mark.parametrize(
    "mutation",
    ("markdown_fence", "invalid_json", "missing_field"),
)
def test_gate_c_strict_parser_rejects_non_json_or_incomplete_output_without_repair(
    tmp_path: Path,
    domain: str,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, condition = _case_and_condition(domain, fixed_identity=True)
    transport = _CapturingTransport(domain=domain, mutation=mutation)
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-invalid-capture-key")

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / domain / mutation).as_posix(),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=1024,
        timeout_seconds=30,
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )
    assert all(attempt.raw_output_ref for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is None for attempt in result.attempt_results)
    assert all(attempt.parse_failure_ref for attempt in result.attempt_results)
    artifact_types = _artifact_types(Path(result.output_root))
    assert "RawModelOutput" in artifact_types
    assert "ParseFailureReport" in artifact_types
    assert "CandidateOutput" not in artifact_types


@pytest.mark.parametrize(
    ("domain", "rejected_status"),
    (
        ("factorization", PaperAttemptStatus.VERIFICATION_REJECTED),
        ("lean_proof", PaperAttemptStatus.CHECKER_REJECTED),
    ),
)
def test_gate_c_semantically_wrong_json_reaches_authoritative_rejection(
    tmp_path: Path,
    domain: str,
    rejected_status: PaperAttemptStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, condition = _case_and_condition(domain, fixed_identity=True)
    transport = _CapturingTransport(domain=domain, mutation="semantic_error")
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-semantic-capture-key")

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / domain / "semantic-error").as_posix(),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=1024,
        timeout_seconds=30,
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert any(
        attempt.attempt_status == rejected_status
        for attempt in result.attempt_results
    )
    assert all(attempt.raw_output_ref for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref for attempt in result.attempt_results)
    assert all(attempt.parse_failure_ref is None for attempt in result.attempt_results)
    artifact_types = _artifact_types(Path(result.output_root))
    assert "CandidateOutput" in artifact_types
    assert "ParseFailureReport" not in artifact_types


class _CapturingTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self, *, domain: str, mutation: str) -> None:
        self.domain = domain
        self.mutation = mutation
        self.delegate = (
            ScriptedFactorizationRangeTransport()
            if domain == "factorization"
            else ScriptedLeanPaperProofTransport()
        )
        self.calls: list[dict] = []

    def post_chat_completion(self, **kwargs):
        response = self.delegate.post_chat_completion(**kwargs)
        response_body = json.loads(json.dumps(response.body))
        response_body["model"] = json.loads(
            kwargs["body_bytes"].decode("utf-8")
        )["model"]
        content = response_body["choices"][0]["message"]["content"]
        response_body["choices"][0]["message"]["content"] = self._mutate(content)
        self.calls.append(
            {
                "body_bytes": kwargs["body_bytes"],
                "normalized_absolute_endpoint": kwargs[
                    "normalized_absolute_endpoint"
                ],
                "content_type": kwargs["content_type"],
            }
        )
        return _TransportResponse(response_body)

    def _mutate(self, content: str) -> str:
        if self.mutation == "valid":
            return content
        if self.mutation == "markdown_fence":
            return f"```json\n{content}\n```"
        if self.mutation == "invalid_json":
            return "This is prose, not a JSON object."
        body = json.loads(content)
        if self.mutation == "missing_field":
            body.pop(
                "schema_version" if self.domain == "factorization" else "proof_source"
            )
        elif self.mutation == "semantic_error":
            if self.domain == "factorization":
                body["result_kind"] = "no_factor_in_range"
                body["found_factor"] = None
                body["cofactor"] = None
                body["checked_divisor_count"] = (
                    int(body["range_end"]) - int(body["range_start"]) + 1
                )
            else:
                body["proof_source"] = "by\n  exact hQ"
        else:
            raise AssertionError(f"unknown mutation: {self.mutation}")
        return json.dumps(body, ensure_ascii=False, sort_keys=True)


class _TransportResponse:
    def __init__(self, body: dict) -> None:
        self.status_code = 200
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def _case_and_condition(domain: str, *, fixed_identity: bool = False):
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain=domain, difficulty="easy")[0]
    identity_fields = {}
    if fixed_identity:
        identity = load_exp1_pilot_profile(
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
        ).model_endpoint_identity
        identity_fields = {
            "model_cohort_id": identity.model_cohort_id,
            "model_cohort_digest": identity.model_cohort_digest,
            "cohort_member_id": identity.cohort_member_id,
            "provider_config_id": identity.provider_config_id,
            "model_entry_id": identity.selected_entry_id,
            "provider_family": identity.provider_family,
            "provider_model_id": identity.provider_model_id,
            "reasoning_profile_id": identity.reasoning_profile_id,
            "source_provider_config_digest": identity.source_provider_config_digest,
            "model_endpoint_identity_digest": (
                identity.model_endpoint_identity_digest
            ),
        }
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"gate_c_{domain}_structured_output",
        domain=domain,
        difficulty="easy",
        paper_difficulty="easy" if domain == "factorization" else "simple",
        topic_family=None if domain == "factorization" else "pure_logic",
        topic_family_version=None if domain == "factorization" else "shallow_v1",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
        **identity_fields,
    )
    return case, condition


def _entry_id(domain: str) -> str:
    return (
        "factorization_paper_scripted"
        if domain == "factorization"
        else "lean_paper_scripted"
    )


def _artifact_types(root: Path) -> set[str]:
    return {
        json.loads(path.read_text(encoding="utf-8"))["artifact_type"]
        for path in (root / "artifacts").glob("*.manifest.json")
    }


def _artifact_bodies(root: Path, artifact_type: str) -> list[dict]:
    bodies: list[dict] = []
    for path in (root / "artifacts").glob("*.manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["artifact_type"] != artifact_type:
            continue
        bodies.append(
            json.loads((root / manifest["uri"]).read_text(encoding="utf-8"))
        )
    return bodies
