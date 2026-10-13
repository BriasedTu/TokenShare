"""Regression checks for real mathematical dependency contexts and proof assembly."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.plugins.lean_proof.fixed_plan import LeanFixedDecompositionPlan
from tokenshare.plugins.lean_proof.merge_policy import _lemma_graph_merge_proof_source
from tokenshare.plugins.lean_proof.prompt_builder import build_lean_proof_candidate_prompt_package


def mathematical_case():
    row = json.loads(Path('benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl').read_text(encoding='utf-8').splitlines()[0])
    row['proof_assembly_shape'] = 'checked_node_bodies.v1'
    row['root_theorem_payload']['parameters_source'] = '(x : Nat)'
    row['root_theorem_payload']['statement_source'] = 'x + 1 > 0'
    for node, statement in zip(row['lemma_graph']['nodes'], ['0 ≤ x', 'x + 1 > 0']):
        node['theorem_payload']['parameters_source'] = '(x : Nat)'
        node['theorem_payload']['statement_source'] = statement
    return row


def test_direct_dependency_statement_is_in_checked_payload_and_prompt():
    plan = LeanFixedDecompositionPlan.from_catalog_case(mathematical_case())
    payload = plan.node_theorem_payload(plan.root_node_id)
    assert payload.parameters_source == '(x : Nat) (node_pure_simple_leaf_01 : 0 ≤ x)'
    prompt = build_lean_proof_candidate_prompt_package(
        request_id='r', task_id='t', unit_id='u', theorem_payload=payload,
        created_at='2026-09-07T00:00:00Z',
    )
    assert '(node_pure_simple_leaf_01 : 0 ≤ x)' in prompt.prompt_text
    assert 'node_proof_sources' not in prompt.prompt_text
    assert 'exact hRoot' not in prompt.prompt_text


def test_mathematical_merge_consumes_checked_body_and_preserves_nested_indentation():
    certificate = SimpleNamespace(
        proof_assembly_shape='checked_node_bodies.v1', root_node_id='root',
        lemma_nodes=[{'node_id': 'lower'}, {'node_id': 'root'}],
        dependency_edges=[{'source_node_id': 'lower', 'target_node_id': 'root'}],
    )
    body = 'by\n  have step : x + 1 > 0 := by\n    omega\n  exact step'
    result = _lemma_graph_merge_proof_source(
        certificate,
        node_proof_sources={'lower': 'by\n  exact Nat.zero_le x', 'root': body},
        node_statements={'lower': '0 ≤ x', 'root': 'x + 1 > 0'},
    )
    assert 'have node_root : x + 1 > 0 := by\n    have step : x + 1 > 0 := by\n      omega\n    exact step' in result
    assert result.endswith('  exact node_root')


def test_mathematical_plan_rejects_mismatched_original_parameter_context():
    row = mathematical_case()
    row['lemma_graph']['nodes'][0]['theorem_payload']['parameters_source'] = '(x : Nat) (invented : False)'
    with pytest.raises(ValueError, match='parameter context'):
        LeanFixedDecompositionPlan.from_catalog_case(row)


def test_mathematical_plan_rejects_root_replacement_and_answer_import():
    row = mathematical_case()
    row['lemma_graph']['nodes'][-1]['theorem_payload']['statement_source'] = 'True'
    with pytest.raises(ValueError, match='root statement'):
        LeanFixedDecompositionPlan.from_catalog_case(row)
    row = mathematical_case()
    row['lemma_graph']['nodes'][0]['theorem_payload']['imports'] = ['AnswerModule']
    with pytest.raises(ValueError, match='import context'):
        LeanFixedDecompositionPlan.from_catalog_case(row)


def test_mathematical_environment_identity_is_portable_and_lock_sensitive():
    from tokenshare.plugins.lean_proof.fixed_plan import checked_plan_environment_digest
    values = dict(
        lean_version='4.24.0', lake_version='5.0', toolchain_file_digest='sha256:tool',
        lakefile_digest='sha256:lake', import_set_digest='sha256:imports',
        helper_sources_digest='sha256:helpers', resource_limits={'lock_sha256': 'first'},
    )
    first = SimpleNamespace(project_root='C:/one', **values)
    second = SimpleNamespace(project_root='D:/two', **values)
    assert checked_plan_environment_digest(first) == checked_plan_environment_digest(second)
    second.resource_limits = {'lock_sha256': 'changed'}
    assert checked_plan_environment_digest(first) != checked_plan_environment_digest(second)
