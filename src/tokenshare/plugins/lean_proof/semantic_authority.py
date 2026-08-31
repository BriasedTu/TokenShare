"""兼容不可变 Lean v1 证据的跨 checkout 语义权威。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from tokenshare.core.models import JsonObject
from tokenshare.plugins.lean_proof.models import canonical_json_digest


LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION = (
    "tokenshare.lean_environment_semantic_authority.v1"
)
LEAN_V1_AUTHORITY_BINDING_SCHEMA_VERSION = "tokenshare.lean_v1_authority_binding.v1"
LEAN_SEMANTIC_PROJECTION_SCHEMA_VERSION = (
    "tokenshare.lean_environment_semantic_projection.v1"
)
LEAN_TEXT_NORMALIZATION = "utf8_lf.v1"

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LEAN_SEMANTIC_AUTHORITY_PATH = (
    _REPOSITORY_ROOT
    / "benchmarks/paper/lean_environment_semantic_authority.v1.json"
)

_RAW_AUTHORITY_PATHS = {
    "direct_catalog": "benchmarks/paper/lean_catalog.v1.jsonl",
    "graph_catalog": "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    "preflight_manifest": "benchmarks/paper/lean_checker_preflight.v1.json",
}
_ENVIRONMENT_SOURCE_PATHS = (
    "fixtures/lean_proof_project/lean-toolchain",
    "fixtures/lean_proof_project/lakefile.lean",
    "fixtures/lean_proof_project/TokenShare.lean",
    "fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean",
    "fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean",
    "fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean",
    "fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean",
    "fixtures/lean_proof_project/TokenShare/Helper.lean",
    "fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean",
    "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean",
    "fixtures/lean_proof_project/TokenShare/Merge.lean",
    "fixtures/lean_proof_project/TokenShare/SplitRules.lean",
)
_CHECKER_SOURCE_PATHS = (
    "src/tokenshare/plugins/lean_proof/checker.py",
)


@dataclass(frozen=True, kw_only=True)
class LeanSemanticAuthority:
    authority_environment_digest: str
    authority_checker_implementation_digest: str
    raw_authority_digests: dict[str, str]
    semantic_environment_digest: str
    semantic_checker_digest: str
    sidecar_digest: str
    repository_root: Path
    sidecar_path: Path
    schema_version: str = LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION


def load_lean_semantic_authority(
    *,
    repository_root: str | Path | None = None,
    sidecar_path: str | Path | None = None,
) -> LeanSemanticAuthority:
    """验证不可变 v1 证据，再验证当前 checkout 的等价语义内容。"""

    root = Path(repository_root or _REPOSITORY_ROOT).resolve()
    path = (
        root / "benchmarks/paper/lean_environment_semantic_authority.v1.json"
        if sidecar_path is None
        else Path(sidecar_path)
    )
    if not path.is_absolute():
        path = root / path
    body = _read_sidecar(path)
    _require_exact_keys(
        "semantic authority sidecar",
        body,
        {"schema_version", "authority", "semantic_projection", "sidecar_digest"},
    )
    if body["schema_version"] != LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION:
        raise ValueError("semantic authority sidecar schema_version is unsupported")
    expected_sidecar_digest = canonical_json_digest(
        {
            "schema_version": body["schema_version"],
            "authority": body["authority"],
            "semantic_projection": body["semantic_projection"],
        }
    )
    if body["sidecar_digest"] != expected_sidecar_digest:
        raise ValueError("semantic authority sidecar_digest mismatch")

    authority = _require_mapping("authority", body["authority"])
    projection = _require_mapping("semantic_projection", body["semantic_projection"])
    environment_digest, checker_digest, raw_digests = _validate_v1_authority(
        repository_root=root,
        authority=authority,
    )
    semantic_environment_digest, semantic_checker_digest = (
        _validate_semantic_projection(
            repository_root=root,
            projection=projection,
        )
    )
    return LeanSemanticAuthority(
        authority_environment_digest=environment_digest,
        authority_checker_implementation_digest=checker_digest,
        raw_authority_digests=raw_digests,
        semantic_environment_digest=semantic_environment_digest,
        semantic_checker_digest=semantic_checker_digest,
        sidecar_digest=expected_sidecar_digest,
        repository_root=root,
        sidecar_path=path.resolve(),
    )


def normalized_text_digest(path: str | Path) -> str:
    """返回 UTF-8 文本经 LF 规范化后的内容摘要。"""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"semantic authority source is unreadable: {source}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return _bytes_digest(normalized.encode("utf-8"))


def semantic_environment_projection(
    repository_root: str | Path,
) -> JsonObject:
    """构造 sidecar 使用的确定性环境语义投影。"""

    root = Path(repository_root).resolve()
    files = {
        relative: normalized_text_digest(root / relative)
        for relative in _ENVIRONMENT_SOURCE_PATHS
    }
    body: JsonObject = {
        "schema_version": LEAN_SEMANTIC_PROJECTION_SCHEMA_VERSION,
        "normalization": LEAN_TEXT_NORMALIZATION,
        "files": files,
    }
    return {**body, "digest": canonical_json_digest(body)}


def semantic_checker_projection(
    repository_root: str | Path,
) -> JsonObject:
    """构造实际 checker 源码的确定性语义投影。"""

    root = Path(repository_root).resolve()
    files = {
        relative: normalized_text_digest(root / relative)
        for relative in _CHECKER_SOURCE_PATHS
    }
    body: JsonObject = {
        "schema_version": "tokenshare.lean_checker_semantic_projection.v1",
        "normalization": LEAN_TEXT_NORMALIZATION,
        "files": files,
    }
    return {**body, "digest": canonical_json_digest(body)}


def _validate_v1_authority(
    *,
    repository_root: Path,
    authority: Mapping[str, Any],
) -> tuple[str, str, dict[str, str]]:
    _require_exact_keys(
        "authority",
        authority,
        {
            "schema_version",
            "environment_digest",
            "checker_implementation_digest",
            "raw_files",
        },
    )
    if authority["schema_version"] != LEAN_V1_AUTHORITY_BINDING_SCHEMA_VERSION:
        raise ValueError("semantic authority binding schema_version is unsupported")
    environment_digest = _require_digest(
        "authority environment_digest", authority["environment_digest"]
    )
    checker_digest = _require_digest(
        "authority checker_implementation_digest",
        authority["checker_implementation_digest"],
    )
    raw_files = _require_mapping("authority raw_files", authority["raw_files"])
    if set(raw_files) != set(_RAW_AUTHORITY_PATHS.values()):
        raise ValueError("semantic authority raw_files coverage mismatch")
    raw_digests: dict[str, str] = {}
    for relative in _RAW_AUTHORITY_PATHS.values():
        expected = _require_digest(f"raw authority {relative}", raw_files[relative])
        actual = _bytes_digest((repository_root / relative).read_bytes())
        if actual != expected:
            raise ValueError(f"v1 authority byte drift: {relative}")
        raw_digests[relative] = actual

    direct_rows = _read_jsonl(repository_root / _RAW_AUTHORITY_PATHS["direct_catalog"])
    graph_rows = _read_jsonl(repository_root / _RAW_AUTHORITY_PATHS["graph_catalog"])
    for row in direct_rows + graph_rows:
        if row.get("environment_digest") != environment_digest:
            raise ValueError("v1 authority environment_digest mismatch")

    preflight = _read_json_object(
        repository_root / _RAW_AUTHORITY_PATHS["preflight_manifest"]
    )
    if preflight.get("environment_digest") != environment_digest:
        raise ValueError("v1 preflight environment_digest mismatch")
    if preflight.get("checker_implementation_digest") != checker_digest:
        raise ValueError("v1 preflight checker_implementation_digest mismatch")
    source_digests = _require_mapping(
        "v1 preflight source_digests", preflight.get("source_digests")
    )
    # 历史 preflight 字段属于被 manifest_digest 覆盖的审计记录；当前三份
    # legacy 文件的逐字节权威绑定只来自 sidecar.authority.raw_files。
    if set(source_digests) != {"lean_catalog", "lean_lemma_graph_catalog"}:
        raise ValueError("v1 preflight source_digests coverage mismatch")
    for source_name, source_digest in source_digests.items():
        _require_digest(f"v1 preflight source_digests {source_name}", source_digest)
    entries = preflight.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("v1 preflight entries are missing")
    if any(
        not isinstance(entry, dict)
        or entry.get("environment_digest") != environment_digest
        or entry.get("checker_implementation_digest") != checker_digest
        for entry in entries
    ):
        raise ValueError("v1 preflight entry authority mismatch")
    manifest_digest = preflight.get("manifest_digest")
    digest_body = {key: value for key, value in preflight.items() if key != "manifest_digest"}
    if manifest_digest != canonical_json_digest(digest_body):
        raise ValueError("v1 preflight manifest_digest mismatch")
    return environment_digest, checker_digest, raw_digests


def _validate_semantic_projection(
    *,
    repository_root: Path,
    projection: Mapping[str, Any],
) -> tuple[str, str]:
    _require_exact_keys(
        "semantic_projection",
        projection,
        {"schema_version", "normalization", "environment", "checker"},
    )
    if projection["schema_version"] != LEAN_SEMANTIC_PROJECTION_SCHEMA_VERSION:
        raise ValueError("semantic projection schema_version is unsupported")
    if projection["normalization"] != LEAN_TEXT_NORMALIZATION:
        raise ValueError("semantic projection normalization is unsupported")
    expected_environment = _require_mapping("environment projection", projection["environment"])
    expected_checker = _require_mapping("checker projection", projection["checker"])
    current_environment = semantic_environment_projection(repository_root)
    current_checker = semantic_checker_projection(repository_root)
    _validate_projection_files(
        label="environment",
        expected=expected_environment,
        current=current_environment,
    )
    _validate_projection_files(
        label="checker",
        expected=expected_checker,
        current=current_checker,
    )
    return str(current_environment["digest"]), str(current_checker["digest"])


def _validate_projection_files(
    *,
    label: str,
    expected: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    _require_exact_keys(
        f"{label} projection",
        expected,
        {"schema_version", "normalization", "files", "digest"},
    )
    expected_files = _require_mapping(f"{label} projection files", expected["files"])
    current_files = _require_mapping(f"current {label} projection files", current["files"])
    if set(expected_files) != set(current_files):
        raise ValueError(f"{label} semantic projection coverage mismatch")
    for relative, actual in current_files.items():
        if expected_files[relative] != actual:
            raise ValueError(f"semantic content drift: {relative}")
    if expected["digest"] != current["digest"]:
        raise ValueError(f"{label} semantic projection digest mismatch")


def _read_sidecar(path: Path) -> JsonObject:
    try:
        return _read_json_object(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"semantic authority sidecar is unavailable: {path}") from exc


def _read_json_object(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"JSON authority root must be an object: {path}")
    return body


def _read_jsonl(path: Path) -> tuple[JsonObject, ...]:
    rows: list[JsonObject] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"JSONL authority row must be an object: {path}")
        rows.append(row)
    if not rows:
        raise ValueError(f"JSONL authority is empty: {path}")
    return tuple(rows)


def _require_mapping(field_name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _require_exact_keys(
    field_name: str,
    value: Mapping[str, Any],
    expected: set[str],
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field_name} fields mismatch")


def _require_digest(field_name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _bytes_digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"
