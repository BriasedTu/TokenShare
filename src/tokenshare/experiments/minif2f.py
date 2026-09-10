"""Experiment 1 miniF2F catalog and pinned local mathematical environment."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from time import monotonic

from tokenshare.plugins.lean_proof.checker import prepared_lean_environment, render_lean_source
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
)
from tokenshare.plugins.lean_proof.fixed_plan import (
    LeanFixedDecompositionPlan, build_fixed_plan_certificate,
    checked_plan_environment_digest,
)
from tokenshare.plugins.lean_proof.merge_policy import _lemma_graph_merge_proof_source
from tokenshare.plugins.lean_proof.models import canonical_json_digest
from tokenshare.storage.artifacts import ArtifactStore

from .case_source import load_cases
from .lean_environment import LeanEnvironmentInvalid


CONFIG_PATH = Path('configs/experiments/exp1_minif2f.v1.json')
CATALOG_PATH = Path('benchmarks/experiments/minif2f_catalog.v1.jsonl')
FIXTURE_PATH = Path('benchmarks/experiments/fixtures/minif2f_project')
PASS_PATH = Path('local/cache/experiments/minif2f_environment_pass.v1.json')
PASS_SCHEMA = 'tokenshare.experiments.minif2f_environment_pass.v1'


def file_digest(path: Path) -> str:
    return 'sha256:' + sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _kernel_axioms_only(stdout: str, stderr: str) -> bool:
    if 'sorryAx' in stdout + stderr:
        return False
    reports = re.findall(r'depends on axioms:\s*\[([^\]]*)\]', stdout)
    allowed = {'propext', 'Classical.choice', 'Quot.sound'}
    if reports:
        return all({name.strip() for name in report.split(',') if name.strip()} <= allowed
                   for report in reports)
    return 'does not depend on any axioms' in stdout


def _command(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120, check=False)
    if result.returncode:
        raise LeanEnvironmentInvalid(f'local environment command failed: {args}: {result.stderr[:1000]}')
    return result.stdout.strip()


def load_environment(repository_root: str | Path) -> LeanEnvironmentManifest:
    """Check the installed lock and actual tools; never initialize a provider."""
    root = Path(repository_root).resolve()
    config = _read(root / CONFIG_PATH)
    physical = root / config['local_environment_root']
    runtime_path = physical / 'runtime_path.json'
    default_local = _read(runtime_path)['runtime_root'] if runtime_path.is_file() else str(physical)
    local = Path(os.environ.get('TOKENSHARE_MINIF2F_ENVIRONMENT', default_local))
    project = local / 'project'
    binary = local / config['toolchain_directory'] / 'bin'
    _validate_project_lock(root, project)
    lean_version = _command([str(binary / 'lean.exe'), '--version'], project)
    lake_version = _command([str(binary / 'lake.exe'), '--version'], project)
    if lean_version != config['lean_version'] or lake_version != config['lake_version']:
        raise LeanEnvironmentInvalid('miniF2F installed Lean/Lake version differs from lock')
    manifest = LeanEnvironmentManifest.from_project(
        project_root=project, lean_executable=binary / 'lean.exe', lake_executable=binary / 'lake.exe',
        lean_version=lean_version, lake_version=lake_version,
        imports=['Mathlib'], created_at=datetime.now(timezone.utc).isoformat(),
        resource_limits={
            'timeout_seconds': config['checker_timeout_seconds'], 'max_output_bytes': 65536,
            'lake_manifest_sha256': file_digest(project / 'lake-manifest.json'),
        },
    )
    # Keep the Windows SUBST spelling for Lean's filesystem API. from_project
    # resolves physical paths for hashing, which can exceed Lean's path limit.
    return replace(manifest, project_root=str(project),
                   lean_executable=str(binary / 'lean.exe'),
                   lake_executable=str(binary / 'lake.exe'), environment_digest=None)


def _validate_project_lock(root: Path, project: Path) -> None:
    for relative in ('lean-toolchain', 'lake-manifest.json', 'lakefile.lean', 'TokenShare/Preamble.lean'):
        if file_digest(project / relative) != file_digest(root / FIXTURE_PATH / relative):
            raise LeanEnvironmentInvalid(f'miniF2F environment file differs from lock: {relative}')
    lock = _read(project / 'lake-manifest.json')
    for package in lock['packages']:
        package_root = project / '.lake/packages' / package['name']
        actual = _command(['git', 'rev-parse', 'HEAD'], package_root)
        if actual != package['rev']:
            raise LeanEnvironmentInvalid(f"miniF2F package commit differs: {package['name']}")
        if _command(['git', 'status', '--porcelain', '--untracked-files=no'], package_root):
            raise LeanEnvironmentInvalid(f"miniF2F package has modified tracked sources: {package['name']}")


def critical_inputs(repository_root: Path, manifest: LeanEnvironmentManifest) -> dict[str, str]:
    root = repository_root.resolve()
    paths = [root / CONFIG_PATH, root / CATALOG_PATH]
    paths.extend(path for path in (root / FIXTURE_PATH).rglob('*') if path.is_file())
    for name in ('fixed_plan.py', 'models.py', 'merge_policy.py', 'runtime_adapter.py',
                 'checker.py', 'prompt_builder.py', 'environment.py'):
        paths.append(root / 'src/tokenshare/plugins/lean_proof' / name)
    paths.append(Path(__file__))
    paths.extend([Path(manifest.lean_executable), Path(manifest.lake_executable)])
    for row in load_cases(root / CATALOG_PATH):
        paths.append(root / row['oracle_proof_package_ref']['source_path'])
        paths.append(root / row['source_provenance']['public_statement_path'])
        review_path = root / row['independent_review']['source_path']
        paths.append(review_path)
        evidence_path = root / _read(review_path)['lean_validation']['result_path']
        paths.extend(evidence_path.parent / name for name in (
            'result.json', 'isolated_nodes_and_original_root.lean', 'stdout.txt', 'stderr.txt'))
    return {str(path.resolve()): file_digest(path) for path in sorted(set(paths))}


def render_isolated_case(plan, proofs, assembled):
    """Anonymous examples discard their environment; only the root is named.

    Lean 4.24's Lean.Elab.MutualDef.elabMutualDef uses withoutModifyingEnv
    for examples. Each example therefore sees only imports, original parameters,
    and its explicitly declared direct dependencies, never preceding node proofs.
    """
    if set(proofs) != set(plan.node_ids):
        raise ValueError('miniF2F proof package does not cover exactly the graph')
    source = ''.join(f'import {name}\n' for name in plan.parent_theorem_payload().imports)
    source += 'set_option linter.unusedVariables false\n\n'
    segments = []
    checks = [(key, plan.node_theorem_payload(key), proofs[key]) for key in plan.topological_order()]
    checks.append(('original_root_recheck', plan.parent_theorem_payload(), assembled))
    for key, payload, proof in checks:
        if re.search(r'\b(sorry|admit|axiom|unsafe|native_decide)\b', proof):
            raise ValueError(f'{plan.case_id}:{key}: forbidden proof construct')
        standalone = render_lean_source(payload, proof)
        block = '\n'.join(standalone.splitlines()[len(payload.imports):]) + '\n'
        if key != 'original_root_recheck':
            block = block.replace(f'theorem {payload.theorem_name}', 'example', 1)
        first_line = source.count('\n') + 1
        source += block + '\n'
        segments.append({
            'node_id': key, 'payload_digest': payload.payload_digest,
            'proof_sha256': 'sha256:' + sha256(proof.encode('utf-8')).hexdigest(),
            'standalone_source_sha256': 'sha256:' + sha256(standalone.encode('utf-8')).hexdigest(),
            'segment_sha256': 'sha256:' + sha256(block.encode('utf-8')).hexdigest(),
            'start_line': first_line, 'end_line': first_line + block.count('\n') - 1,
        })
    parent = plan.parent_theorem_payload()
    name = '.'.join(filter(None, (parent.namespace, parent.theorem_name)))
    source += f'#print axioms {name}\n'
    return source, segments


def check_case_offline(row: dict, manifest: LeanEnvironmentManifest, output: Path) -> dict:
    """Compile isolated node examples and the unchanged root with the locked Lean.

    This is corpus preflight only. Experiment execution continues to use the
    existing runtime adapter, per-unit checker, and ProtocolRunCoordinator.
    """
    plan = LeanFixedDecompositionPlan.from_catalog_case(row)
    if plan.environment_digest != checked_plan_environment_digest(manifest):
        raise LeanEnvironmentInvalid(f'{plan.case_id}: environment digest mismatch')
    output.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).isoformat()
    store = ArtifactStore(output)
    parent = plan.parent_theorem_payload()
    parent_ref = store.save_json(parent.to_dict(), artifact_id=f'parent_{plan.case_id}',
        artifact_type='LeanTheoremPayload', artifact_schema_id='lean_proof.theorem_payload',
        artifact_schema_version='v1', source={'kind': 'offline_minif2f_preflight'}, metadata={}, created_at=now)
    certificate = build_fixed_plan_certificate(plan=plan, parent_theorem_payload_ref=parent_ref,
                                               environment_manifest=manifest)
    proofs = row['oracle_proof_package_ref']['node_proof_sources']
    assembled = _lemma_graph_merge_proof_source(certificate, node_proof_sources=proofs,
        node_statements={key: plan.node_theorem_payload(key).statement_source for key in plan.node_ids})
    source, segments = render_isolated_case(plan, proofs, assembled)
    source_path = output / 'isolated_nodes_and_original_root.lean'
    source_path.write_text(source, encoding='utf-8', newline='\n')
    command = [manifest.lean_executable, '-DwarningAsError=true', str(source_path.resolve())]
    environment = dict(os.environ)
    environment.update(prepared_lean_environment(manifest))
    started = monotonic()
    timeout = int(parent.resource_limits['timeout_seconds']) * (len(plan.node_ids) + 1)
    try:
        completed = subprocess.run(command, cwd=manifest.project_root, env=environment,
            capture_output=True, encoding='utf-8', errors='replace', timeout=timeout, check=False)
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = exc.stdout or b''
        stdout = stdout.decode('utf-8', errors='replace') if isinstance(stdout, bytes) else stdout
        stderr = f'Lean preflight exceeded {timeout} seconds'
    (output / 'stdout.txt').write_text(stdout, encoding='utf-8')
    (output / 'stderr.txt').write_text(stderr, encoding='utf-8')
    passed = exit_code == 0 and _kernel_axioms_only(stdout, stderr)
    record = {
        'case_id': plan.case_id, 'status': 'passed' if passed else 'failed',
        'exit_code': exit_code, 'command': command, 'cwd': manifest.project_root,
        'duration_seconds': round(monotonic() - started, 3), 'timeout_seconds': timeout,
        'source_path': str(source_path), 'source_sha256': file_digest(source_path),
        'stdout_sha256': file_digest(output / 'stdout.txt'), 'stderr_sha256': file_digest(output / 'stderr.txt'),
        'environment_digest': plan.environment_digest, 'plan_digest': plan.plan_digest,
        'lean_executable_sha256': file_digest(Path(manifest.lean_executable)),
        'lake_executable_sha256': file_digest(Path(manifest.lake_executable)),
        'isolation_method': 'Lean 4.24 anonymous example: withoutModifyingEnv',
        'checks': [{**segment, 'status': 'accepted' if passed else 'not_verified'} for segment in segments],
        'real_provider_call_count': 0,
    }
    (output / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return record


def validate_case_evidence(repository_root: Path, row: dict) -> dict:
    """Verify published compiler evidence against the exact current generated source."""
    plan = LeanFixedDecompositionPlan.from_catalog_case(row)
    review = _read(repository_root / row['independent_review']['source_path'])
    ref = review['lean_validation']
    path = repository_root / ref['result_path']
    if file_digest(path) != ref['result_sha256']:
        raise LeanEnvironmentInvalid(f'{plan.case_id}: Lean evidence digest changed')
    result = _read(path)
    if result['status'] != 'passed' or result['exit_code'] != 0:
        raise LeanEnvironmentInvalid(f'{plan.case_id}: Lean evidence is not passed')
    if result['plan_digest'] != plan.plan_digest or result['environment_digest'] != plan.environment_digest:
        raise LeanEnvironmentInvalid(f'{plan.case_id}: Lean evidence identity changed')
    proofs = row['oracle_proof_package_ref']['node_proof_sources']
    assembled = _lemma_graph_merge_proof_source(plan, node_proof_sources=proofs,
        node_statements={key: plan.node_theorem_payload(key).statement_source for key in plan.node_ids})
    source, segments = render_isolated_case(plan, proofs, assembled)
    if result['checks'] != [{**segment, 'status': 'accepted'} for segment in segments]:
        raise LeanEnvironmentInvalid(f'{plan.case_id}: checked node identities changed')
    if (path.parent / 'isolated_nodes_and_original_root.lean').read_text(encoding='utf-8') != source:
        raise LeanEnvironmentInvalid(f'{plan.case_id}: checked Lean source changed')
    for filename, key in (('isolated_nodes_and_original_root.lean', 'source_sha256'),
                          ('stdout.txt', 'stdout_sha256'), ('stderr.txt', 'stderr_sha256')):
        if file_digest(path.parent / filename) != result[key]:
            raise LeanEnvironmentInvalid(f'{plan.case_id}: Lean evidence file changed: {filename}')
    if not _kernel_axioms_only((path.parent / 'stdout.txt').read_text(encoding='utf-8'),
                               (path.parent / 'stderr.txt').read_text(encoding='utf-8')):
        raise LeanEnvironmentInvalid(f'{plan.case_id}: nonstandard proof axioms in compiler evidence')
    # Early admission reports predate tool-byte fields. The independent reviewer
    # may attest those bytes against the same verified installation; preserve the
    # original compiler report and its SHA rather than rewriting old evidence.
    for key in ('lean_executable_sha256', 'lake_executable_sha256'):
        if key not in result:
            result[key] = review['environment_binary_digests'][key]
    return result


def run_environment_test(repository_root: str | Path, *, pass_path: str | Path | None = None,
                         output_dir: str | Path | None = None, recheck: bool = False) -> dict:
    """Check every isolated node and assembled root; publish a pass only for all."""
    root = Path(repository_root).resolve()
    manifest = load_environment(root)
    rows = list(load_cases(root / CATALOG_PATH))
    if not rows:
        raise LeanEnvironmentInvalid('miniF2F catalog is empty')
    now = datetime.now(timezone.utc).isoformat()
    destination = Path(pass_path) if pass_path else root / PASS_PATH
    output = Path(output_dir) if output_dir else root / 'TokenShareData/outputs/experiments' / (
        'minif2f-preflight-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    if output.exists():
        raise ValueError(f'preflight output already exists: {output}')
    output.mkdir(parents=True)
    destination.unlink(missing_ok=True)
    records = []
    environment_digest = checked_plan_environment_digest(manifest)
    for row in rows:
        if row['environment_digest'] != environment_digest:
            raise LeanEnvironmentInvalid(f"{row['case_id']}: environment digest mismatch; update the catalog before preflight")
        record = None
        if not recheck:
            try:
                cached = validate_case_evidence(root, row)
                if (cached['lean_executable_sha256'] == file_digest(Path(manifest.lean_executable))
                        and cached['lake_executable_sha256'] == file_digest(Path(manifest.lake_executable))):
                    record = {**cached, 'reused_verified_evidence': True}
            except (OSError, KeyError, ValueError, LeanEnvironmentInvalid):
                pass
        if record is None:
            case_directory = sha256(row['case_id'].encode('utf-8')).hexdigest()[:16]
            record = check_case_offline(row, manifest, output / case_directory)
        records.append(record)
        print(f"{row['case_id']}: {record['status']}", flush=True)
    failures = [record['case_id'] for record in records if record['status'] != 'passed']
    body = {
        'schema_version': PASS_SCHEMA, 'status': 'failed' if failures else 'passed',
        'created_at': now, 'environment_digest': checked_plan_environment_digest(manifest),
        'runtime_environment': manifest.to_dict(), 'case_ids': [row['case_id'] for row in rows],
        'checked_node_count': sum(row['expected_ai_unit_count'] for row in rows),
        'checked_root_count': len(rows), 'checks': records, 'failures': failures,
        'critical_input_digests': critical_inputs(root, manifest), 'real_provider_call_count': 0,
        'output_dir': str(output),
    }
    body['pass_digest'] = canonical_json_digest(body)
    (output / 'summary.json').write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failures:
        raise LeanEnvironmentInvalid(f'miniF2F checks failed: {failures}; evidence: {output}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return body


def validate_environment_pass(repository_root: str | Path) -> dict:
    root = Path(repository_root).resolve()
    try:
        body = _read(root / PASS_PATH)
        check = dict(body)
        digest = check.pop('pass_digest')
        if body['schema_version'] != PASS_SCHEMA or body['status'] != 'passed' or digest != canonical_json_digest(check):
            raise LeanEnvironmentInvalid('miniF2F environment pass is invalid')
        manifest = LeanEnvironmentManifest.from_dict(body['runtime_environment'])
        if body['critical_input_digests'] != critical_inputs(root, manifest):
            raise LeanEnvironmentInvalid('miniF2F preflight input set or file digest changed')
        _validate_project_lock(root, Path(manifest.project_root))
        if body['environment_digest'] != checked_plan_environment_digest(manifest):
            raise LeanEnvironmentInvalid('miniF2F preflight environment changed')
        if body['case_ids'] != [row['case_id'] for row in load_cases(root / CATALOG_PATH)]:
            raise LeanEnvironmentInvalid('miniF2F preflight case identities changed')
        return body
    except (OSError, KeyError, ValueError) as exc:
        raise LeanEnvironmentInvalid(f'miniF2F environment pass is unavailable or invalid: {exc}') from exc
