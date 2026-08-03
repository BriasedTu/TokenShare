from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest
import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter_module
import tokenshare.experiments.paper_catalog as paper_catalog_module

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments.paper_formal_callbacks import (
    PaperOnlineProviderEvidenceCallback,
    produce_capability_online_evidence,
)
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_online_checks import (
    CURRENT_PROVIDER_ROLES,
    TypedEvidenceRef,
    freeze_paper_online_checks_plan,
    produce_exp2_online_direct_evidence,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.core.models import ArtifactRef
from tests.experiments.test_factorization_paper_adapter import _v2_condition
from tests.experiments.test_lean_paper_adapter import (
    _catalog_with_lemma_graph,
    _condition_for_case,
    _v2_case,
    real_lean_checker,
)
from tokenshare.experiments.paper_factorization_catalog import (
    generate_factorization_paper_cases,
)


EXPECTED_PROFILE_DIGEST = (
    "sha256:e8e8c1f10b638607054c094f6fee409c62aade27ed01ca8645e9afdb79c14fb7"
)
EXPECTED_BUDGET_DIGEST = (
    "sha256:1898ca133111cec3245927fb4defad091277ac5b02681e21aa79d7b133b18c69"
)
EXPECTED_PLAN_DIGEST = (
    "sha256:92c3bc101ba85dc4268e9b889328d1573cb8e007aca668b44432d55c493c874a"
)
PROFILE_PATH = Path("benchmarks/paper/epd027_pipeline_profile.v1.json")


def _single_entry_config() -> dict:
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    return body


def _run_callback_attempt(
    store: ArtifactStore,
    *,
    callback: PaperOnlineProviderEvidenceCallback,
    request_id: str,
    submission_id: str,
) -> object:
    request = replace(
        make_ai_request(store, request_id=request_id),
        attempt_id=submission_id,
        unit_id=f"unit-{submission_id}",
    )
    executor = AIAPIExecutor(
        executor_id="task25-official-callback",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(_single_entry_config()),
        transport=FakeSiliconFlowTransport(
            [
                FakeProviderResponse(
                    status_code=200,
                    body={
                        "id": f"response-{submission_id}",
                        "model": "Qwen/Qwen2.5-7B-Instruct",
                        "choices": [{"message": {"content": "candidate"}}],
                        "usage": {
                            "prompt_tokens": 11,
                            "completion_tokens": 13,
                            "total_tokens": 24,
                        },
                    },
                )
            ]
        ),
        post_raw_output_hook=callback,
    )
    submission = executor.execute(
        request,
        submission_id=submission_id,
        submitted_at="2026-08-03T00:00:00Z",
    )
    assert submission.raw_output_ref is not None
    return callback.require_capture(submission_id)


class _RejectThenAcceptFactorTransport(ScriptedFactorizationRangeTransport):
    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        if len(self.calls) == 1:
            result = dict(self.calls[-1]["range_result"])
            result["child_index"] = int(result["child_index"]) + 1
            self.calls[-1]["range_result"] = result
            response.body["choices"][0]["message"]["content"] = json.dumps(result)
            response.text = json.dumps(response.body)
        return response


class _RejectThenAcceptLeanTransport(ScriptedLeanPaperProofTransport):
    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        if len(self.calls) == 1:
            content = json.loads(response.body["choices"][0]["message"]["content"])
            content["proof_source"] = "by\n  exact True.intro"
            response.body["choices"][0]["message"]["content"] = json.dumps(content)
            response.text = json.dumps(response.body)
        return response


def test_capability_plan_is_exact_factor_and_lean_initial_replacement_four_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    assert plan.plan_digest == EXPECTED_PLAN_DIGEST
    assert plan.capability_calls_exact == 4
    assert [
        (call.domain, call.phase, call.attempt_ordinal)
        for call in plan.capability_calls
    ] == [
        ("factorization", "controlled_initial_rejection", 0),
        ("factorization", "replacement", 1),
        ("lean_proof", "controlled_initial_rejection", 0),
        ("lean_proof", "replacement", 1),
    ]

    store = ArtifactStore(tmp_path)
    captures = tuple(
        _run_callback_attempt(
            store,
            callback=PaperOnlineProviderEvidenceCallback.for_capability_call(call),
            request_id=f"capability-{index}",
            submission_id=f"capability-attempt-{index}",
        )
        for index, call in enumerate(plan.capability_calls)
    )
    evidence = produce_capability_online_evidence(
        artifact_store=store,
        captures=captures,
        lifecycle_evidence_by_domain={},
    )
    assert [item.initial_source_raw_ref for item in evidence.replacements] == [
        captures[0].raw_or_failure_ref,
        captures[2].raw_or_failure_ref,
    ]
    assert all(item.eligibility_input.blocked for item in evidence.replacements)


def test_capability_official_verifier_checker_requeue_seams_are_required_and_domain_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = freeze_paper_online_checks_plan()
    factor_case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["case_id"] == plan.capability_calls[0].case_id
    )
    factor_callback = PaperOnlineProviderEvidenceCallback.for_capability_domain(
        "factorization"
    )
    factor_transport = _RejectThenAcceptFactorTransport()
    run_factorization_paper_case(
        case=factor_case,
        condition=_v2_condition(factor_case),
        output_root=tmp_path / "factor",
        transport=factor_transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id=plan.capability_calls[0].planned_ai_unit_id,
        post_raw_output_hook=factor_callback,
    )
    factor_store = ArtifactStore(tmp_path / "factor" / factor_case["case_id"])

    tracked = json.loads(
        Path("benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = paper_catalog_module._current_lean_environment_manifest_without_preflight()
    object.__setattr__(manifest, "environment_digest", tracked["environment_digest"])
    original_digest = paper_catalog_module._file_digest
    oracle_source = Path(
        "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()

    def tracked_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_digest(resolved)
        source = resolved.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{sha256(source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: manifest,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked["checker_implementation_digest"],
    )
    monkeypatch.setattr(paper_catalog_module, "_file_digest", tracked_file_digest)
    monkeypatch.setattr(
        lean_paper_adapter_module,
        "default_lean_paper_environment_manifest",
        lambda: manifest,
    )
    catalog = _catalog_with_lemma_graph()
    lean_case = _v2_case(catalog, plan.capability_calls[2].case_id)
    lean_callback = PaperOnlineProviderEvidenceCallback.for_capability_domain(
        "lean_proof"
    )
    lean_node = next(
        node
        for node in lean_case["lemma_graph"]["nodes"]
        if node["node_id"] == plan.capability_calls[2].planned_ai_unit_id
    )
    lean_transport = _RejectThenAcceptLeanTransport(
        proof_sources_by_statement={
            lean_node["theorem_payload"]["statement_source"]: lean_case[
                "oracle_proof_package_ref"
            ]["node_proof_sources"][plan.capability_calls[2].planned_ai_unit_id]
        }
    )
    run_lean_paper_case(
        case=lean_case,
        condition=_condition_for_case(catalog.catalog_digest, lean_case),
        output_root=tmp_path / "lean",
        transport=lean_transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
        selected_ai_unit_id=plan.capability_calls[2].planned_ai_unit_id,
        post_raw_output_hook=lean_callback,
        checker=real_lean_checker,
    )
    lean_store = ArtifactStore(tmp_path / "lean" / lean_case["case_id"])

    factor_captures = factor_callback.require_capability_captures()
    lean_captures = lean_callback.require_capability_captures()
    lifecycle = {
        "factorization": factor_callback.require_capability_lifecycle(),
        "lean_proof": lean_callback.require_capability_lifecycle(),
    }
    rejection_bodies = {
        domain: json.loads(
            store.read_bytes(value.rejection_ref.artifact_ref).decode("utf-8")
        )
        for domain, store, value in (
            ("factorization", factor_store, lifecycle["factorization"]),
            ("lean_proof", lean_store, lifecycle["lean_proof"]),
        )
    }
    domain_rejection_refs = {
        domain: ArtifactRef.from_dict(body["domain_rejection_ref"])
        for domain, body in rejection_bodies.items()
    }
    assert domain_rejection_refs["factorization"].artifact_type == "FactorVerifierRejection"
    assert domain_rejection_refs["lean_proof"].artifact_type == "LeanCheckerReport"
    evidence = produce_capability_online_evidence(
        artifact_store=factor_store,
        artifact_stores_by_domain={
            "factorization": factor_store,
            "lean_proof": lean_store,
        },
        captures=factor_captures + lean_captures,
        lifecycle_evidence_by_domain=lifecycle,
    )
    assert all(item.eligibility_input.value is True for item in evidence.replacements)

    crossed = produce_capability_online_evidence(
        artifact_store=factor_store,
        artifact_stores_by_domain={
            "factorization": factor_store,
            "lean_proof": lean_store,
        },
        captures=factor_captures + lean_captures,
        lifecycle_evidence_by_domain={
            "factorization": lifecycle["lean_proof"],
            "lean_proof": lifecycle["factorization"],
        },
    )
    assert all(item.eligibility_input.blocked for item in crossed.replacements)

    missing_checker_body = dict(rejection_bodies["lean_proof"])
    missing_checker_body.pop("domain_rejection_ref")
    missing_checker_ref = lean_store.save_json(
        missing_checker_body,
        artifact_id="lean_controlled_rejection_without_checker_report",
        artifact_type="PaperOnlineEvidenceObject",
        artifact_schema_id="tokenshare.paper_online_controlled_rejection",
        artifact_schema_version="v1",
        source={"kind": "test_tamper"},
        metadata={"evidence_role": "controlled_rejection"},
        created_at=missing_checker_body["occurred_at"],
    )
    missing_checker = replace(
        lifecycle["lean_proof"],
        rejection_ref=TypedEvidenceRef(
            role="controlled_rejection", artifact_ref=missing_checker_ref
        ),
    )
    missing = produce_capability_online_evidence(
        artifact_store=factor_store,
        artifact_stores_by_domain={
            "factorization": factor_store,
            "lean_proof": lean_store,
        },
        captures=factor_captures + lean_captures,
        lifecycle_evidence_by_domain={**lifecycle, "lean_proof": missing_checker},
    )
    assert missing.replacements[1].eligibility_input.blocked is True

    wrong_ordinal = replace(
        lifecycle["factorization"], replacement_attempt_ordinal=2
    )
    mismatched = produce_capability_online_evidence(
        artifact_store=factor_store,
        artifact_stores_by_domain={
            "factorization": factor_store,
            "lean_proof": lean_store,
        },
        captures=factor_captures + lean_captures,
        lifecycle_evidence_by_domain={**lifecycle, "factorization": wrong_ordinal},
    )
    assert mismatched.replacements[0].eligibility_input.blocked is True


def test_exp2_plan_uses_frozen_24_sequence_and_max_concurrent_roots_one_with_480_upper() -> None:
    authority = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    plan = freeze_paper_online_checks_plan()

    assert plan.profile_digest == authority["profile_digest"] == EXPECTED_PROFILE_DIGEST
    assert plan.budget_digest == authority["budget"]["budget_digest"] == EXPECTED_BUDGET_DIGEST
    assert plan.plan_digest == EXPECTED_PLAN_DIGEST
    assert plan.max_concurrent_roots == 1
    assert plan.exp2_calls_upper == 480
    assert len(plan.exp2_condition_refs) == 24
    assert [ref.worker_count for ref in plan.exp2_condition_refs] == [
        worker for worker in (1, 3, 7, 10, 30, 50) for _ in range(4)
    ]
    assert [ref.case_position for ref in plan.exp2_condition_refs] == [
        position
        for _ in range(6)
        for position in ("early", "middle", "late", "no_factor")
    ]


def test_exp2_plan_emits_digest_bound_condition_refs_and_compliant_online_direct_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    store = ArtifactStore(tmp_path)
    rows = tuple(
        {
            "condition_id": ref.condition_id,
            "condition_digest": ref.condition_digest,
            "profile_digest": EXPECTED_PROFILE_DIGEST,
            "budget_digest": EXPECTED_BUDGET_DIGEST,
            "plan_digest": EXPECTED_PLAN_DIGEST,
            "evidence_class": "online_real_provider",
            "case_id": ref.case_id,
            "repeat_id": ref.repeat_id,
        }
        for ref in plan.exp2_condition_refs
    )
    attempts = {
        ref.condition_id: (
            _run_callback_attempt(
                store,
                callback=PaperOnlineProviderEvidenceCallback.for_exp2_condition(ref),
                request_id=f"exp2-{index}",
                submission_id=f"exp2-attempt-{index}",
            ),
        )
        for index, ref in enumerate(plan.exp2_condition_refs)
    }

    evidence = produce_exp2_online_direct_evidence(
        artifact_store=store,
        plan=plan,
        direct_rows=rows,
        current_provider_attempts_by_condition=attempts,
    )

    assert len(evidence) == 24
    assert all(row.condition_ref.plan_digest == EXPECTED_PLAN_DIGEST for row in evidence)
    assert all(row.condition_ref.budget_digest == EXPECTED_BUDGET_DIGEST for row in evidence)
    assert all(row.main_trace_table_eligible is False for row in evidence)
    assert all(row.current_provider_roles == CURRENT_PROVIDER_ROLES for row in evidence)
    assert all(row.eligibility_input.value is True for row in evidence)
    assert all(row.provider_inputs[0].total_tokens.value == 24 for row in evidence)

    with pytest.raises(ValueError, match="authoritative online-check plan"):
        produce_exp2_online_direct_evidence(
            artifact_store=store,
            plan=replace(plan, exp2_calls_upper=479),
            direct_rows=rows,
            current_provider_attempts_by_condition=attempts,
        )
    tampered = list(rows)
    tampered[0] = {**tampered[0], "budget_digest": "sha256:" + "0" * 64}
    with pytest.raises(ValueError, match="identity drift"):
        produce_exp2_online_direct_evidence(
            artifact_store=store,
            plan=plan,
            direct_rows=tampered,
            current_provider_attempts_by_condition=attempts,
        )


def test_exp3_plan_is_two_roots_12_upper() -> None:
    plan = freeze_paper_online_checks_plan()

    assert plan.exp3_calls_upper == 12
    assert len(plan.exp3_root_refs) == 2
    assert [ref.check_kind for ref in plan.exp3_root_refs] == [
        "false_positive",
        "worker_death",
    ]
    assert [ref.provider_calls_upper for ref in plan.exp3_root_refs] == [6, 6]


def test_task25_exports_no_metric_formula_or_post_bank_gate() -> None:
    from tokenshare.experiments import paper_online_checks

    forbidden = {
        "compute_union_fraction",
        "compute_severe_deviation",
        "compute_wasted_actual_tokens",
        "compute_cost_estimate",
        "post_bank_gate",
        "paper_publication_gate",
    }
    assert forbidden.isdisjoint(paper_online_checks.__all__)
    assert not any(name.startswith("project_") for name in paper_online_checks.__all__)
