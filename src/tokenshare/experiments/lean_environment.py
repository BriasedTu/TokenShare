"""TokenShare Experiments 的独立 Lean 环境实测与轻量 pass 文件校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Callable, Mapping

from tokenshare.executors.contracts import EnvironmentRef
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.models import (
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.storage.artifacts import ArtifactStore


LEAN_ENVIRONMENT_PASS_SCHEMA_VERSION = "tokenshare.slim_v2.lean_environment_pass.v1"
DEFAULT_LEAN_ENVIRONMENT_PASS_PATH = Path(
    "local/cache/experiments/lean_environment_pass.v1.json"
)
_CATALOG_PATH = Path("benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl")
_PROJECT_PATH = Path("benchmarks/experiments/fixtures/lean_proof_project")
_LEGACY_PROJECT_PREFIX = "fixtures/lean_proof_project/"
_ORACLE_MODULE = "TokenShare.LemmaGraphOracle"
_LEAN_VERSION = (
    "Lean (version 4.8.0, x86_64-w64-windows-gnu, "
    "commit df668f00e6c0, Release)"
)
_LAKE_VERSION = "Lake version 5.0.0-df668f0 (Lean version 4.8.0)"
_CREATED_AT = "2026-08-22T00:00:00Z"


class LeanEnvironmentInvalid(RuntimeError):
    """Lean pass 文件或独立实测不满足启动条件。"""


@dataclass(frozen=True, slots=True)
class LeanEnvironmentNode:
    case_id: str
    node_id: str
    theorem_payload: Mapping[str, object]
    proof_source: str


@dataclass(frozen=True, slots=True)
class LeanEnvironmentInventory:
    nodes: tuple[LeanEnvironmentNode, ...]
    checker_backed_case_count: int
    structured_blocked_case_count: int
    structured_blocked_node_count: int
    environment_digest: str
    oracle_source_paths: tuple[str, ...]


def collect_checker_backed_nodes(
    repository_root: str | Path,
) -> LeanEnvironmentInventory:
    """读取冻结 catalog；只跳过显式预注册 structured-blocked case。"""

    root = Path(repository_root).resolve()
    catalog_path = root / _CATALOG_PATH
    rows = _read_jsonl(catalog_path)
    nodes: list[LeanEnvironmentNode] = []
    checker_cases: set[str] = set()
    blocked_cases: set[str] = set()
    blocked_node_count = 0
    environment_digests: set[str] = set()
    oracle_paths: set[str] = set()
    for row in rows:
        case_id = _nonempty(row.get("case_id"), "case_id")
        environment_digests.add(
            _digest(row.get("environment_digest"), f"{case_id} environment_digest")
        )
        graph = _mapping(row.get("lemma_graph"), f"{case_id} lemma_graph")
        raw_nodes = graph.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise LeanEnvironmentInvalid(f"{case_id} lemma_graph.nodes is invalid")
        status = row.get("preflight_status")
        if status == "structured_blocked":
            if row.get("oracle_proof_package_ref") is not None:
                raise LeanEnvironmentInvalid(
                    f"{case_id} structured_blocked case carries an oracle package"
                )
            blocked_cases.add(case_id)
            blocked_node_count += len(raw_nodes)
            continue
        if status != "passed":
            raise LeanEnvironmentInvalid(
                f"{case_id} has unsupported preflight_status: {status}"
            )
        oracle = _mapping(
            row.get("oracle_proof_package_ref"),
            f"{case_id} oracle_proof_package_ref",
        )
        if oracle.get("kind") != "fixed_oracle_package":
            raise LeanEnvironmentInvalid(f"{case_id} oracle package kind is invalid")
        source_path = _nonempty(oracle.get("source_path"), f"{case_id} source_path")
        physical_source_path = _physical_lean_source_path(source_path)
        source = root / physical_source_path
        if not source.is_file():
            raise LeanEnvironmentInvalid(
                f"{case_id} oracle source is missing: {physical_source_path}"
            )
        if _normalized_text_digest(source) != oracle.get("content_hash"):
            raise LeanEnvironmentInvalid(f"{case_id} oracle source digest mismatch")
        proofs = _mapping(
            oracle.get("node_proof_sources"), f"{case_id} node_proof_sources"
        )
        node_ids = {
            _nonempty(_mapping(item, "lemma node").get("node_id"), "node_id")
            for item in raw_nodes
        }
        if set(proofs) != node_ids:
            raise LeanEnvironmentInvalid(
                f"{case_id} oracle proofs do not cover every catalog node"
            )
        checker_cases.add(case_id)
        oracle_paths.add(physical_source_path)
        for item in raw_nodes:
            node = _mapping(item, "lemma node")
            node_id = _nonempty(node.get("node_id"), "node_id")
            payload = _mapping(node.get("theorem_payload"), f"{case_id}:{node_id} payload")
            proof_source = _nonempty(proofs.get(node_id), f"{case_id}:{node_id} proof")
            nodes.append(
                LeanEnvironmentNode(
                    case_id=case_id,
                    node_id=node_id,
                    theorem_payload=dict(payload),
                    proof_source=proof_source,
                )
            )
    if len(environment_digests) != 1:
        raise LeanEnvironmentInvalid("catalog environment_digest is not unique")
    return LeanEnvironmentInventory(
        nodes=tuple(nodes),
        checker_backed_case_count=len(checker_cases),
        structured_blocked_case_count=len(blocked_cases),
        structured_blocked_node_count=blocked_node_count,
        environment_digest=next(iter(environment_digests)),
        oracle_source_paths=tuple(sorted(oracle_paths)),
    )


def run_lean_environment_test(
    *,
    repository_root: str | Path,
    pass_path: str | Path | None = None,
    project_builder: Callable[[Path], tuple[Path, ...]] | None = None,
    node_checker: Callable[[LeanEnvironmentNode], str] | None = None,
    created_at: str | None = None,
) -> dict[str, object]:
    """独立检查全部 checker-backed node；全通过后原子写 pass 文件。"""

    root = Path(repository_root).resolve()
    destination = _resolve_pass_path(root, pass_path)
    # 新检查开始即撤销旧结论；失败或中断不能继续放行实验启动。
    destination.unlink(missing_ok=True)
    inventory = collect_checker_backed_nodes(root)
    object_paths = tuple(
        path.resolve() for path in (project_builder or _build_project)(root)
    )
    if len(object_paths) != 2:
        raise LeanEnvironmentInvalid("Lean environment test requires two compiled objects")
    for object_path in object_paths:
        _require_inside(root, object_path, "compiled object")
        if not object_path.is_file():
            raise LeanEnvironmentInvalid(f"compiled object is missing: {object_path}")

    if node_checker is None:
        with tempfile.TemporaryDirectory(prefix="tokenshare_experiments_lean_environment_") as temp:
            checker = _real_node_checker(root, Path(temp), created_at or _CREATED_AT)
            _check_all_nodes(inventory.nodes, checker)
    else:
        _check_all_nodes(inventory.nodes, node_checker)

    inputs = _critical_input_digests(root, inventory.oracle_source_paths)
    body: dict[str, object] = {
        "schema_version": LEAN_ENVIRONMENT_PASS_SCHEMA_VERSION,
        "status": "passed",
        "created_at": created_at or _utc_now(),
        "environment_digest": inventory.environment_digest,
        "checker_backed_case_count": inventory.checker_backed_case_count,
        "checked_node_count": len(inventory.nodes),
        "structured_blocked_case_count": inventory.structured_blocked_case_count,
        "structured_blocked_node_count": inventory.structured_blocked_node_count,
        "critical_input_digests": inputs,
        "compiled_objects": {
            path.relative_to(root).as_posix(): _file_digest(path)
            for path in sorted(object_paths)
        },
    }
    body["pass_digest"] = canonical_json_digest(body)
    _write_json_atomic(destination, body)
    return body


def validate_lean_environment_pass(
    repository_root: str | Path,
    pass_path: str | Path | None = None,
) -> dict[str, object]:
    """启动路径只读文件和 hash；绝不调用 Lean、Lake 或其他进程。"""

    root = Path(repository_root).resolve()
    path = _resolve_pass_path(root, pass_path)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LeanEnvironmentInvalid(f"Lean environment pass is unavailable: {path}") from exc
    if not isinstance(body, dict):
        raise LeanEnvironmentInvalid("Lean environment pass must be a JSON object")
    if body.get("schema_version") != LEAN_ENVIRONMENT_PASS_SCHEMA_VERSION:
        raise LeanEnvironmentInvalid("Lean environment pass schema is invalid")
    if body.get("status") != "passed":
        raise LeanEnvironmentInvalid("Lean environment pass status is not passed")
    digest_body = dict(body)
    recorded_digest = digest_body.pop("pass_digest", None)
    if recorded_digest != canonical_json_digest(digest_body):
        raise LeanEnvironmentInvalid("Lean environment pass digest mismatch")
    inputs = _mapping(body.get("critical_input_digests"), "critical_input_digests")
    for relative, expected in inputs.items():
        path_in_repo = root / relative
        if not path_in_repo.is_file() or _file_digest(path_in_repo) != expected:
            raise LeanEnvironmentInvalid(f"critical input digest mismatch: {relative}")
    compiled_objects = _mapping(body.get("compiled_objects"), "compiled_objects")
    if len(compiled_objects) != 2:
        raise LeanEnvironmentInvalid("Lean environment pass must bind two compiled objects")
    for relative, expected in compiled_objects.items():
        object_path = (root / relative).resolve()
        _require_inside(root, object_path, "compiled object")
        if not object_path.is_file() or _file_digest(object_path) != expected:
            raise LeanEnvironmentInvalid(f"compiled object digest mismatch: {relative}")
    return body


def _check_all_nodes(
    nodes: tuple[LeanEnvironmentNode, ...],
    checker: Callable[[LeanEnvironmentNode], str],
) -> None:
    for node in nodes:
        status = checker(node)
        if status != "accepted":
            raise LeanEnvironmentInvalid(
                f"Lean environment check failed for {node.case_id}:{node.node_id}: {status}"
            )


def _build_project(repository_root: Path) -> tuple[Path, ...]:
    project = repository_root / _PROJECT_PATH
    lake = _tool_root() / "lake.exe"
    if not lake.is_file():
        raise LeanEnvironmentInvalid(f"Lake executable is missing: {lake}")
    completed = subprocess.run(
        [str(lake), "build"],
        cwd=project,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        env=_lean_subprocess_environment(),
        check=False,
    )
    if completed.returncode != 0:
        excerpt = (completed.stdout + completed.stderr)[:1600]
        raise LeanEnvironmentInvalid(f"Lean project build failed: {excerpt}")
    for library_root in (
        project / ".lake/build/lib/lean",
        project / ".lake/build/lib",
    ):
        objects = tuple(
            library_root / relative
            for relative in (
                "TokenShare/LemmaGraphOracle.olean",
                "TokenShare/LemmaGraphCases.olean",
            )
        )
        if all(path.is_file() for path in objects):
            return objects
    raise LeanEnvironmentInvalid(
        "TokenShare.LemmaGraphOracle.olean or LemmaGraphCases.olean was not built"
    )


def _real_node_checker(
    repository_root: Path,
    artifact_root: Path,
    created_at: str,
) -> Callable[[LeanEnvironmentNode], str]:
    project = repository_root / _PROJECT_PATH
    tools = _tool_root()
    manifest = LeanEnvironmentManifest.from_project(
        project_root=project,
        lean_executable=tools / "lean.exe",
        lake_executable=tools / "lake.exe",
        lean_version=_LEAN_VERSION,
        lake_version=_LAKE_VERSION,
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=created_at,
        imports=[_ORACLE_MODULE],
    )
    store = ArtifactStore(artifact_root)
    environment_ref = EnvironmentRef(
        environment_id="experiments_lean_environment_test",
        environment_digest=str(manifest.environment_digest),
        runtime="lean",
        tool_versions={
            "lean_version": manifest.lean_version,
            "lake_version": manifest.lake_version,
        },
        resource_limits=dict(manifest.resource_limits),
        fixture_profile_digest=manifest.fixture_profile_digest,
        seed=None,
        clock_policy="fixed",
        created_at=created_at,
    )

    def check(node: LeanEnvironmentNode) -> str:
        payload_data = dict(node.theorem_payload)
        payload_data["theorem_id"] = f"lean_theorem:{node.case_id}:{node.node_id}"
        payload = LeanTheoremPayload.from_dict(payload_data)
        safe = _safe_id(f"{node.case_id}_{node.node_id}")
        theorem_ref = store.save_json(
            payload.to_dict(),
            artifact_id=f"{safe}_theorem",
            artifact_type="LeanTheoremPayload",
            artifact_schema_id="lean_proof.theorem_payload",
            artifact_schema_version="v1",
            source={"kind": "experiments_lean_environment_test"},
            metadata={"case_id": node.case_id, "node_id": node.node_id},
            created_at=created_at,
        )
        proof_ref = store.save_json(
            {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": f"proof_candidate:{node.case_id}:{node.node_id}:environment",
                "theorem_payload_digest": payload.payload_digest,
                "proof_source": node.proof_source,
                "created_at": created_at,
            },
            artifact_id=f"{safe}_proof",
            artifact_type="LeanProofCandidate",
            artifact_schema_id="lean_proof.proof_candidate",
            artifact_schema_version="v1",
            source={"kind": "experiments_lean_environment_test"},
            metadata={"case_id": node.case_id, "node_id": node.node_id},
            created_at=created_at,
        )
        report = check_lean_proof(
            LeanCheckerRequest(
                request_id=f"experiments_lean_environment:{node.case_id}:{node.node_id}",
                theorem_payload_ref=theorem_ref,
                proof_candidate_ref=proof_ref,
                environment_ref=environment_ref,
                checker_mode=LeanCheckerMode.DIRECT_PROOF,
                timeout_seconds=int(payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(payload.resource_limits["max_output_bytes"]),
                created_at=created_at,
            ),
            artifact_store=store,
            environment_manifest=manifest,
        )
        return report.status.value

    return check


def _critical_input_digests(
    repository_root: Path,
    oracle_source_paths: tuple[str, ...],
) -> dict[str, str]:
    paths = {
        _CATALOG_PATH.as_posix(),
        (_PROJECT_PATH / "lean-toolchain").as_posix(),
        (_PROJECT_PATH / "lakefile.lean").as_posix(),
        (_PROJECT_PATH / "TokenShare.lean").as_posix(),
        (_PROJECT_PATH / "TokenShare/LemmaGraphCases.lean").as_posix(),
        "src/tokenshare/plugins/lean_proof/checker.py",
        *oracle_source_paths,
    }
    result: dict[str, str] = {}
    for relative in sorted(paths):
        path = repository_root / relative
        if not path.is_file():
            raise LeanEnvironmentInvalid(f"critical Lean input is missing: {relative}")
        result[relative] = _file_digest(path)
    return result


def _physical_lean_source_path(source_path: str) -> str:
    if source_path.startswith(_LEGACY_PROJECT_PREFIX):
        suffix = source_path.removeprefix(_LEGACY_PROJECT_PREFIX)
        return (_PROJECT_PATH / suffix).as_posix()
    return source_path


def _resolve_pass_path(repository_root: Path, pass_path: str | Path | None) -> Path:
    path = Path(pass_path) if pass_path is not None else DEFAULT_LEAN_ENVIRONMENT_PASS_PATH
    return path if path.is_absolute() else repository_root / path


def _write_json_atomic(path: Path, body: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(body, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    try:
        rows = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LeanEnvironmentInvalid(f"Lean graph catalog is unavailable: {path}") from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise LeanEnvironmentInvalid("Lean graph catalog must contain JSON objects")
    return rows


def _tool_root() -> Path:
    return (
        Path.home()
        / "AppData"
        / "Local"
        / "TokenShare"
        / "LeanToolchain"
        / "elan-home"
        / "bin"
    )


def _lean_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    tools = _tool_root()
    environment["ELAN_HOME"] = str(tools.parent)
    environment["PATH"] = f"{tools}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise LeanEnvironmentInvalid(f"{field_name} must be an object")
    return value


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LeanEnvironmentInvalid(f"{field_name} must be non-empty text")
    return value


def _digest(value: object, field_name: str) -> str:
    text = _nonempty(value, field_name)
    if not text.startswith("sha256:") or len(text) != 71:
        raise LeanEnvironmentInvalid(f"{field_name} must be a sha256 digest")
    return text


def _require_inside(root: Path, path: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LeanEnvironmentInvalid(f"{label} must be inside repository root") from exc


def _file_digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _normalized_text_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return f"sha256:{sha256(text.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10)).encode('utf-8')).hexdigest()}"


def _safe_id(value: str) -> str:
    return "".join(item if item.isalnum() or item == "_" else "_" for item in value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DEFAULT_LEAN_ENVIRONMENT_PASS_PATH",
    "LEAN_ENVIRONMENT_PASS_SCHEMA_VERSION",
    "LeanEnvironmentInvalid",
    "LeanEnvironmentInventory",
    "LeanEnvironmentNode",
    "collect_checker_backed_nodes",
    "run_lean_environment_test",
    "validate_lean_environment_pass",
]
