"""Preflight evidence handling is a baseline check and never starts Lean."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.experiments import minif2f


def test_preflight_rejects_old_plan_after_environment_change_without_compiling(tmp_path, monkeypatch):
    manifest = SimpleNamespace(lean_executable='same-lean', lake_executable='same-lake',
                               to_dict=lambda: {})
    row = {'case_id':'case', 'environment_digest':'old-environment', 'expected_ai_unit_count':2}
    monkeypatch.setattr(minif2f, 'load_environment', lambda _: manifest)
    monkeypatch.setattr(minif2f, 'load_cases', lambda _: [row])
    monkeypatch.setattr(minif2f, 'checked_plan_environment_digest', lambda _: 'new-environment')
    monkeypatch.setattr(minif2f, 'file_digest', lambda _: 'same-tool-bytes')
    monkeypatch.setattr(minif2f, 'critical_inputs', lambda *_: {})
    monkeypatch.setattr(minif2f, 'validate_case_evidence', lambda *_: {
        'case_id':'case', 'status':'passed', 'environment_digest':'old-environment',
        'lean_executable_sha256':'same-tool-bytes', 'lake_executable_sha256':'same-tool-bytes'})
    monkeypatch.setattr(minif2f, 'check_case_offline',
                        lambda *_: pytest.fail('an incompatible plan must not start compilation'))
    destination = tmp_path / 'pass.json'
    destination.write_text('old pass', encoding='utf-8')
    with pytest.raises(minif2f.LeanEnvironmentInvalid, match='environment digest mismatch'):
        minif2f.run_environment_test(tmp_path, pass_path=destination, output_dir=tmp_path/'output')
    assert not destination.exists()


def test_cached_pass_cannot_omit_a_current_critical_input(tmp_path, monkeypatch):
    body = {'schema_version': minif2f.PASS_SCHEMA, 'status': 'passed',
            'runtime_environment': {}, 'environment_digest': 'sha256:env',
            'critical_input_digests': {}, 'case_ids': ['case']}
    body['pass_digest'] = minif2f.canonical_json_digest(body)
    monkeypatch.setattr(minif2f, '_read', lambda _: body)
    monkeypatch.setattr(minif2f.LeanEnvironmentManifest, 'from_dict',
                        lambda _: SimpleNamespace(project_root=str(tmp_path)))
    monkeypatch.setattr(minif2f, '_validate_project_lock', lambda *_: None)
    monkeypatch.setattr(minif2f, 'checked_plan_environment_digest', lambda _: 'sha256:env')
    monkeypatch.setattr(minif2f, 'load_cases', lambda _: [{'case_id': 'case'}])
    monkeypatch.setattr(minif2f, 'critical_inputs', lambda *_: {'required_file': 'sha256:actual'})
    with pytest.raises(minif2f.LeanEnvironmentInvalid, match='input set'):
        minif2f.validate_environment_pass(tmp_path)


@pytest.mark.parametrize(('exit_code', 'stdout', 'expected'), [
    (0, "'root' does not depend on any axioms\n", 'passed'),
    (0, "'root' depends on axioms: [propext, Classical.choice, Quot.sound]\n", 'passed'),
    (0, "'root' depends on axioms: [sorryAx]\n", 'failed'),
    (0, "'root' depends on axioms: [Lean.ofReduceBool]\n", 'failed'),
    (1, "earlier output\n'root' does not depend on any axioms\n", 'failed'),
])
def test_compiler_evidence_accepts_axiom_free_proofs_and_fails_closed(
    tmp_path, monkeypatch, exit_code, stdout, expected,
):
    row = json.loads(Path('benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl')
                     .read_text(encoding='utf-8').splitlines()[0])
    binary = tmp_path / 'compiler-stub'
    binary.write_bytes(b'never executed')
    manifest = SimpleNamespace(lean_executable=str(binary), lake_executable=str(binary),
                               project_root=str(tmp_path))
    monkeypatch.setattr(minif2f, 'checked_plan_environment_digest', lambda _: row['environment_digest'])
    monkeypatch.setattr(minif2f, 'prepared_lean_environment', lambda _: {})
    monkeypatch.setattr(minif2f, 'build_fixed_plan_certificate', lambda **_: None)
    monkeypatch.setattr(minif2f, '_lemma_graph_merge_proof_source', lambda *_, **__: 'by trivial')
    calls = []

    def fake_compile(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=exit_code, stdout=stdout, stderr='')

    monkeypatch.setattr(minif2f.subprocess, 'run', fake_compile)
    record = minif2f.check_case_offline(row, manifest, tmp_path / 'output')
    assert len(calls) == 1
    assert record['status'] == expected
    assert {item['status'] for item in record['checks']} == (
        {'accepted'} if expected == 'passed' else {'not_verified'})
    source = (tmp_path / 'output/isolated_nodes_and_original_root.lean').read_text(encoding='utf-8')
    assert source.count('\nexample ') == row['expected_ai_unit_count']
    assert source.count('\ntheorem ') == 1
    assert record['real_provider_call_count'] == 0
    if expected == 'passed':
        # Reusing compiler evidence must remain a read-only baseline operation.
        row['independent_review'] = {'source_path': 'review.json'}
        review = {'lean_validation': {'result_path': 'output/result.json',
            'result_sha256': minif2f.file_digest(tmp_path / 'output/result.json')}}
        (tmp_path / 'review.json').write_text(json.dumps(review), encoding='utf-8')
        monkeypatch.setattr(minif2f.subprocess, 'run',
                            lambda *_, **__: pytest.fail('evidence validation started a process'))
        assert minif2f.validate_case_evidence(tmp_path, row)['status'] == 'passed'
        (tmp_path / 'output/stdout.txt').write_text('changed', encoding='utf-8')
        with pytest.raises(minif2f.LeanEnvironmentInvalid, match='evidence file changed'):
            minif2f.validate_case_evidence(tmp_path, row)
