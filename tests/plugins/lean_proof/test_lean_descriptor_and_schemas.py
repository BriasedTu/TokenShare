import pytest

from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload, canonical_json_digest
from tokenshare.plugins.lean_proof.schemas import (
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
)


def test_lean_descriptor_declares_real_checker_and_no_stub_success() -> None:
    descriptor = build_lean_proof_plugin_descriptor()
    body = descriptor.to_dict()

    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.plugin_version == PLUGIN_VERSION
    assert descriptor.descriptor_digest == build_lean_proof_plugin_descriptor().descriptor_digest
    assert body["metadata"]["real_checker_required"] is True
    assert body["metadata"]["lean_stub_allowed_as_success"] is False
    assert body["metadata"]["ai_may_decide_decomposition"] is False
    assert body["metadata"]["python_semantic_text_parse"] is False
    assert body["metadata"]["structured_theorem_payload_required"] is True
    assert body["metadata"]["environment_ref_required"] is True
    assert body["metadata"]["checker_logs_required"] is True
    assert "lean_stub" not in body["plugin_id"]


def test_lean_theorem_payload_requires_structured_context_fields() -> None:
    payload = _payload(statement_source="1 = 1")

    assert payload.schema_version == LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION
    assert payload.payload_digest.startswith("sha256:")
    assert payload.to_dict()["imports"] == ["Init"]
    assert payload.to_dict()["namespace"] == "TokenShareFixtures"
    assert payload.to_dict()["parameters_source"] == ""

    with pytest.raises(ValueError, match="imports"):
        _payload(imports=[])
    with pytest.raises(ValueError, match="theorem_name"):
        _payload(theorem_name="")
    with pytest.raises(ValueError, match="statement_source"):
        _payload(statement_source="")
    with pytest.raises(ValueError, match="resource_limits"):
        _payload(resource_limits={"timeout_seconds": "30"})
    with pytest.raises(ValueError, match="decomposition_policy"):
        _payload(decomposition_policy={"allowed_rules": "all"})


def test_lean_payload_digest_is_stable_and_includes_imports_namespace_options() -> None:
    base = _payload(statement_source="1 = 1")
    retry = _payload(statement_source="1 = 1")
    changed_import = _payload(statement_source="1 = 1", imports=["Init", "Std"])
    changed_namespace = _payload(statement_source="1 = 1", namespace=None)
    changed_options = _payload(statement_source="1 = 1", options={"pp.universes": True})

    assert base.payload_digest == retry.payload_digest
    assert base.payload_digest != changed_import.payload_digest
    assert base.payload_digest != changed_namespace.payload_digest
    assert base.payload_digest != changed_options.payload_digest

    body = base.to_dict()
    body["payload_digest"] = "sha256:wrong"
    with pytest.raises(ValueError, match="payload_digest"):
        LeanTheoremPayload.from_dict(body)


def test_lean_descriptor_declares_split_strategy_validator_and_merge_policy() -> None:
    body = build_lean_proof_plugin_descriptor().to_dict()
    strategy = body["split_strategies"][DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID]

    assert body["supported_task_types"] == [
        "root",
        "lean_theorem",
        "lean_proof_subgoal",
        "lean_proof_merge",
    ]
    assert body["validator_policy_id"] == "lean_proof.checker.validator.v1"
    assert body["merge_policy_id"] == "lean_proof.verified_merge.v1"
    assert strategy["allowed_unit_types"] == ["lean_proof_subgoal"]
    assert strategy["validator_policy_id"] == "lean_proof.checker.validator.v1"
    assert strategy["merge_policy_id"] == "lean_proof.verified_merge.v1"
    assert strategy["durable_subgoal_policy"]["only_promote_helper_certificate_children"] is True
    assert strategy["candidate_artifact_policy"] == {
        "required_structured_output": "lean_proof_artifact",
        "required_schema_version": LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
        "raw_text_authoritative": False,
        "executor_may_submit_candidates": True,
        "executor_may_define_task_graph": False,
        "ai_may_decide_decomposition": False,
    }
    assert set(body["execution_contracts"]) == {
        "deterministic_lean_checker",
        "lean_helper_split",
        "mock_ai_proof_candidate",
        "ai_api_proof_candidate",
        "environment_policy",
    }


def _payload(**overrides) -> LeanTheoremPayload:
    values = {
        "theorem_id": "lean_theorem:one_eq_one",
        "theorem_name": "one_eq_one",
        "imports": ["Init"],
        "namespace": "TokenShareFixtures",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "",
        "statement_source": "1 = 1",
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {
            "project": "tokenshare_lean",
            "module": "TokenShare.Fixtures.Direct",
        },
        "decomposition_policy": {
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    values.update(overrides)
    return LeanTheoremPayload(**values)
