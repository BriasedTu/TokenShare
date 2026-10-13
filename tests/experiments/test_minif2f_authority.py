"""The supplement authority compares complete identities and every required file."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from verification import verify_authoritative_corpus as verifier


ROOT = Path(__file__).resolve().parents[2]


def test_declaration_separator_whitespace_does_not_hide_semantic_changes():
    parent = SimpleNamespace(theorem_name='example_name',
                             parameters_source='(a b : ℕ) (h : a < b)', statement_source='a ≤ b')
    original = 'theorem example_name (a b : ℕ) (h : a < b): a ≤ b'
    assert verifier._matches_original_declaration(original, parent)
    assert not verifier._matches_original_declaration(original.replace('a < b', 'a ≤ b'), parent)
    assert not verifier._matches_original_declaration(original.replace('(a b :', '(ab :'), parent)


@pytest.mark.parametrize('name', ['node_identities', 'root_identities'])
def test_rehashed_identity_reordering_is_rejected(name):
    manifest = json.loads((ROOT / verifier.MANIFEST_PATH).read_text(encoding='utf-8'))
    block = manifest['supplements']['minif2f'][name]
    block['items'][0], block['items'][1] = block['items'][1], block['items'][0]
    block['sha256'] = verifier._items_sha256(block['items'])
    with pytest.raises(ValueError, match=f'full {name} array mismatch'):
        verifier._verify_minif2f_supplement(ROOT, manifest)


def test_file_sha_mismatch_is_rejected():
    manifest = json.loads((ROOT / verifier.MANIFEST_PATH).read_text(encoding='utf-8'))
    manifest['supplements']['minif2f']['files_sha256'][
        'benchmarks/experiments/minif2f_catalog.v1.jsonl'] = '0' * 64
    with pytest.raises(ValueError, match='file SHA mismatch'):
        verifier._verify_minif2f_supplement(ROOT, manifest)


def test_compiler_evidence_cannot_be_removed_from_file_authority():
    manifest = json.loads((ROOT / verifier.MANIFEST_PATH).read_text(encoding='utf-8'))
    files = manifest['supplements']['minif2f']['files_sha256']
    source = next(name for name in files if name.endswith('/isolated_nodes_and_original_root.lean'))
    del files[source]
    with pytest.raises(ValueError, match='omits required file SHA records'):
        verifier._verify_minif2f_supplement(ROOT, manifest)
