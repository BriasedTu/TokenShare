"""Content-addressed evidence models for Lean catalog preflight audits."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable, Sequence

from tokenshare.core.models import JsonObject
from tokenshare.experiments.paper_models import digest_json
from tokenshare.plugins.lean_proof.checker import render_lean_source
from tokenshare.plugins.lean_proof.models import (
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.semantic_authority import (
    load_lean_semantic_authority,
)


LEAN_CATALOG_PREFLIGHT_MANIFEST_SCHEMA_VERSION = (
    "tokenshare.lean_catalog_preflight_manifest.v1"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CHECKER_IMPLEMENTATION_PATHS = (
    Path(__file__).resolve(),
    _REPOSITORY_ROOT / "src/tokenshare/experiments/paper_catalog.py",
    _REPOSITORY_ROOT / "src/tokenshare/plugins/lean_proof/checker.py",
    _REPOSITORY_ROOT / "src/tokenshare/plugins/lean_proof/environment.py",
    _REPOSITORY_ROOT / "src/tokenshare/plugins/lean_proof/models.py",
    _REPOSITORY_ROOT / "src/tokenshare/plugins/lean_proof/preflight.py",
)
DEFAULT_TRACKED_MANIFEST_PATH = (
    _REPOSITORY_ROOT / "benchmarks/paper/lean_checker_preflight.v1.json"
)
DEFAULT_LOCAL_CACHE_MANIFEST_PATH = (
    _REPOSITORY_ROOT / "local/cache/lean_checker/manifest.v1.json"
)
DEFAULT_LEAN_CATALOG_PATH = (
    _REPOSITORY_ROOT / "benchmarks/paper/lean_catalog.v1.jsonl"
)
DEFAULT_LEAN_LEMMA_GRAPH_CATALOG_PATH = (
    _REPOSITORY_ROOT / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)


class LeanCatalogAuditError(RuntimeError):
    """Raised when fresh checker evidence cannot safely replace prior evidence."""


def lean_preflight_entry_key(
    *,
    generated_source_digest: str,
    oracle_proof_digest: str,
    environment_digest: str,
    checker_implementation_digest: str,
    checker_mode: str,
    resource_limits: JsonObject,
) -> str:
    """Return the cache identity for every input that can affect one check."""

    return digest_json(
        {
            "generated_source_digest": generated_source_digest,
            "oracle_proof_digest": oracle_proof_digest,
            "environment_digest": environment_digest,
            "checker_implementation_digest": checker_implementation_digest,
            "checker_mode": checker_mode,
            "resource_limits": dict(resource_limits),
        }
    )


@dataclass(frozen=True, kw_only=True)
class LeanCatalogAuditCandidate:
    """One deterministic Lean invocation before checker evidence is attached."""

    entry_id: str
    case_id: str
    node_id: str | None
    theorem_payload: LeanTheoremPayload
    proof_source: str
    environment_digest: str
    checker_implementation_digest: str
    checker_mode: str
    resource_limits: JsonObject

    @property
    def generated_source_digest(self) -> str:
        source = render_lean_source(self.theorem_payload, self.proof_source)
        return _bytes_digest(source.encode("utf-8"))

    @property
    def oracle_proof_digest(self) -> str:
        return canonical_json_digest({"proof_source": self.proof_source})

    @property
    def normalized_theorem_digest(self) -> str:
        return canonical_json_digest(
            {
                "theorem_name": self.theorem_payload.theorem_name,
                "imports": self.theorem_payload.imports,
                "namespace": self.theorem_payload.namespace,
                "parameters_source": self.theorem_payload.parameters_source,
                "statement_source": self.theorem_payload.statement_source,
            }
        )

    @property
    def proof_digest(self) -> str:
        return canonical_json_digest(
            {
                "theorem_payload_digest": self.theorem_payload.payload_digest,
                "proof_source": self.proof_source,
            }
        )

    @property
    def entry_key(self) -> str:
        return lean_preflight_entry_key(
            generated_source_digest=self.generated_source_digest,
            oracle_proof_digest=self.oracle_proof_digest,
            environment_digest=self.environment_digest,
            checker_implementation_digest=self.checker_implementation_digest,
            checker_mode=self.checker_mode,
            resource_limits=self.resource_limits,
        )

    def accepted_entry(self) -> "LeanCatalogPreflightEntry":
        """Materialize evidence only after the caller observed checker acceptance."""

        return LeanCatalogPreflightEntry(
            entry_id=self.entry_id,
            case_id=self.case_id,
            node_id=self.node_id,
            checker_mode=self.checker_mode,
            generated_source_digest=self.generated_source_digest,
            oracle_proof_digest=self.oracle_proof_digest,
            environment_digest=self.environment_digest,
            checker_implementation_digest=self.checker_implementation_digest,
            resource_limits=dict(self.resource_limits),
            entry_key=self.entry_key,
            normalized_theorem_digest=self.normalized_theorem_digest,
            proof_digest=self.proof_digest,
            status="accepted",
        )


@dataclass(frozen=True, kw_only=True)
class LeanCatalogPreflightEntry:
    entry_id: str
    case_id: str
    node_id: str | None
    checker_mode: str
    generated_source_digest: str
    oracle_proof_digest: str
    environment_digest: str
    checker_implementation_digest: str
    resource_limits: JsonObject
    entry_key: str
    normalized_theorem_digest: str
    proof_digest: str | None
    status: str

    @classmethod
    def from_dict(cls, body: JsonObject) -> "LeanCatalogPreflightEntry":
        return cls(
            entry_id=str(body["entry_id"]),
            case_id=str(body["case_id"]),
            node_id=str(body["node_id"]) if body.get("node_id") is not None else None,
            checker_mode=str(body["checker_mode"]),
            generated_source_digest=str(body["generated_source_digest"]),
            oracle_proof_digest=str(body["oracle_proof_digest"]),
            environment_digest=str(body["environment_digest"]),
            checker_implementation_digest=str(body["checker_implementation_digest"]),
            resource_limits=dict(body["resource_limits"]),
            entry_key=str(body["entry_key"]),
            normalized_theorem_digest=str(body["normalized_theorem_digest"]),
            proof_digest=(
                str(body["proof_digest"])
                if body.get("proof_digest") is not None
                else None
            ),
            status=str(body["status"]),
        )

    def to_dict(self) -> JsonObject:
        return {
            "entry_id": self.entry_id,
            "case_id": self.case_id,
            "node_id": self.node_id,
            "checker_mode": self.checker_mode,
            "generated_source_digest": self.generated_source_digest,
            "oracle_proof_digest": self.oracle_proof_digest,
            "environment_digest": self.environment_digest,
            "checker_implementation_digest": self.checker_implementation_digest,
            "resource_limits": dict(self.resource_limits),
            "entry_key": self.entry_key,
            "normalized_theorem_digest": self.normalized_theorem_digest,
            "proof_digest": self.proof_digest,
            "status": self.status,
        }


@dataclass(frozen=True, kw_only=True)
class LeanCatalogPreflightManifest:
    source_digests: dict[str, str]
    environment_digest: str
    checker_implementation_digest: str
    entries: tuple[LeanCatalogPreflightEntry, ...]
    checked_direct_case_count: int
    checked_graph_case_count: int
    checked_graph_node_count: int
    total_entry_count: int
    proof_digest_bundle: str
    status: str
    generated_at: str
    manifest_digest: str
    schema_version: str = LEAN_CATALOG_PREFLIGHT_MANIFEST_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, body: JsonObject) -> "LeanCatalogPreflightManifest":
        return cls(
            schema_version=str(body["schema_version"]),
            source_digests={
                str(path): str(digest)
                for path, digest in dict(body["source_digests"]).items()
            },
            environment_digest=str(body["environment_digest"]),
            checker_implementation_digest=str(body["checker_implementation_digest"]),
            entries=tuple(
                LeanCatalogPreflightEntry.from_dict(dict(entry))
                for entry in body["entries"]
            ),
            checked_direct_case_count=int(body["checked_direct_case_count"]),
            checked_graph_case_count=int(body["checked_graph_case_count"]),
            checked_graph_node_count=int(body["checked_graph_node_count"]),
            total_entry_count=int(body["total_entry_count"]),
            proof_digest_bundle=str(body["proof_digest_bundle"]),
            status=str(body["status"]),
            generated_at=str(body["generated_at"]),
            manifest_digest=str(body["manifest_digest"]),
        )

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "source_digests": dict(sorted(self.source_digests.items())),
            "environment_digest": self.environment_digest,
            "checker_implementation_digest": self.checker_implementation_digest,
            "entries": [entry.to_dict() for entry in self.entries],
            "checked_direct_case_count": self.checked_direct_case_count,
            "checked_graph_case_count": self.checked_graph_case_count,
            "checked_graph_node_count": self.checked_graph_node_count,
            "total_entry_count": self.total_entry_count,
            "proof_digest_bundle": self.proof_digest_bundle,
            "status": self.status,
            "generated_at": self.generated_at,
        }

    def to_dict(self) -> JsonObject:
        return {**self._digest_body(), "manifest_digest": self.manifest_digest}


@dataclass(frozen=True, kw_only=True)
class LeanCatalogAuditResult:
    manifest: LeanCatalogPreflightManifest
    reused_entry_count: int
    rechecked_entry_count: int
    canary_entry_count: int
    invalidated_by: tuple[str, ...]
    provider_calls_made: int = 0

    @property
    def status(self) -> str:
        return self.manifest.status

    def summary(self) -> JsonObject:
        return {
            "total_entry_count": self.manifest.total_entry_count,
            "reused_entry_count": self.reused_entry_count,
            "rechecked_entry_count": self.rechecked_entry_count,
            "canary_entry_count": self.canary_entry_count,
            "invalidated_by": list(self.invalidated_by),
            "status": self.status,
            "provider_calls_made": self.provider_calls_made,
            "manifest_digest": self.manifest.manifest_digest,
        }


def build_lean_catalog_preflight_manifest(
    *,
    entries: Iterable[LeanCatalogPreflightEntry],
    source_digests: dict[str, str],
    environment_digest: str,
    checker_implementation_digest: str,
    generated_at: str,
) -> LeanCatalogPreflightManifest:
    ordered = tuple(sorted(entries, key=lambda entry: entry.entry_id))
    direct_entries = tuple(entry for entry in ordered if entry.node_id is None)
    graph_entries = tuple(entry for entry in ordered if entry.node_id is not None)
    graph_case_ids = {entry.case_id for entry in graph_entries}
    proof_digest_bundle = _proof_digest_bundle(ordered)
    status = "passed" if all(entry.status == "accepted" for entry in ordered) else "failed"
    partial = LeanCatalogPreflightManifest(
        source_digests=dict(sorted(source_digests.items())),
        environment_digest=environment_digest,
        checker_implementation_digest=checker_implementation_digest,
        entries=ordered,
        checked_direct_case_count=len(direct_entries),
        checked_graph_case_count=len(graph_case_ids),
        checked_graph_node_count=len(graph_entries),
        total_entry_count=len(ordered),
        proof_digest_bundle=proof_digest_bundle,
        status=status,
        generated_at=generated_at,
        manifest_digest="",
    )
    return LeanCatalogPreflightManifest(
        **{
            **partial.__dict__,
            "manifest_digest": digest_json(partial._digest_body()),
        }
    )


def validate_lean_catalog_preflight_manifest(
    body: JsonObject,
    *,
    expected_entry_ids: set[str] | None = None,
) -> LeanCatalogPreflightManifest:
    manifest = LeanCatalogPreflightManifest.from_dict(body)
    if manifest.schema_version != LEAN_CATALOG_PREFLIGHT_MANIFEST_SCHEMA_VERSION:
        raise ValueError("Lean catalog preflight manifest schema_version is unsupported")

    entry_ids = [entry.entry_id for entry in manifest.entries]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("Lean catalog preflight manifest coverage contains duplicate entries")
    if manifest.total_entry_count != len(manifest.entries):
        raise ValueError("Lean catalog preflight manifest coverage count does not match entries")
    if expected_entry_ids is not None and set(entry_ids) != expected_entry_ids:
        raise ValueError("Lean catalog preflight manifest coverage does not match expected entries")
    if manifest.status != "passed" or any(
        entry.status != "accepted" or entry.proof_digest is None
        for entry in manifest.entries
    ):
        raise ValueError("Lean catalog preflight manifest requires accepted entries")

    for entry in manifest.entries:
        expected_key = lean_preflight_entry_key(
            generated_source_digest=entry.generated_source_digest,
            oracle_proof_digest=entry.oracle_proof_digest,
            environment_digest=entry.environment_digest,
            checker_implementation_digest=entry.checker_implementation_digest,
            checker_mode=entry.checker_mode,
            resource_limits=entry.resource_limits,
        )
        if entry.entry_key != expected_key:
            raise ValueError("Lean catalog preflight entry_key does not match checker inputs")
        if entry.environment_digest != manifest.environment_digest:
            raise ValueError("Lean catalog preflight entry environment_digest mismatch")
        if entry.checker_implementation_digest != manifest.checker_implementation_digest:
            raise ValueError("Lean catalog preflight entry checker implementation mismatch")

    if manifest.proof_digest_bundle != _proof_digest_bundle(manifest.entries):
        raise ValueError("Lean catalog preflight proof_digest_bundle mismatch")
    if manifest.manifest_digest != digest_json(manifest._digest_body()):
        raise ValueError("Lean catalog preflight manifest_digest mismatch")
    return manifest


def validate_manifest_against_candidates(
    manifest: LeanCatalogPreflightManifest,
    *,
    candidates: Iterable[LeanCatalogAuditCandidate],
) -> tuple[LeanCatalogPreflightEntry, ...]:
    """Fail closed unless every requested candidate has matching accepted evidence."""

    entries_by_id = {entry.entry_id: entry for entry in manifest.entries}
    matched: list[LeanCatalogPreflightEntry] = []
    for candidate in candidates:
        entry = entries_by_id.get(candidate.entry_id)
        if entry is None:
            raise ValueError(
                f"Lean catalog preflight manifest is stale: missing {candidate.entry_id}"
            )
        expected = candidate.accepted_entry()
        if entry.to_dict() != expected.to_dict():
            raise ValueError(
                "Lean catalog preflight manifest is stale: "
                f"checker inputs changed for {candidate.entry_id}"
            )
        matched.append(entry)
    return tuple(matched)


def audit_lean_catalog(
    *,
    candidates: Iterable[LeanCatalogAuditCandidate],
    previous_manifest: LeanCatalogPreflightManifest | None,
    source_digests: dict[str, str],
    generated_at: str,
    checker: Callable[[LeanCatalogAuditCandidate], LeanCatalogPreflightEntry],
    force_all: bool = False,
    canary_entry_ids: Iterable[str] = (),
    global_invalidation_reasons: Iterable[str] = (),
) -> LeanCatalogAuditResult:
    """Reuse matching evidence and invoke the checker only for invalidated entries."""

    ordered_candidates = tuple(sorted(candidates, key=lambda item: item.entry_id))
    if not ordered_candidates:
        raise LeanCatalogAuditError("Lean catalog audit requires at least one candidate")
    candidate_ids = [candidate.entry_id for candidate in ordered_candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise LeanCatalogAuditError("Lean catalog audit candidates contain duplicate entries")

    environment_digests = {
        candidate.environment_digest for candidate in ordered_candidates
    }
    checker_digests = {
        candidate.checker_implementation_digest for candidate in ordered_candidates
    }
    if len(environment_digests) != 1 or len(checker_digests) != 1:
        raise LeanCatalogAuditError(
            "Lean catalog audit candidates must share environment and checker digests"
        )
    environment_digest = next(iter(environment_digests))
    checker_digest = next(iter(checker_digests))

    explicit_global_reasons = tuple(dict.fromkeys(global_invalidation_reasons))
    if any(not reason for reason in explicit_global_reasons):
        raise LeanCatalogAuditError("global invalidation reasons must be non-empty")
    invalidated_by: list[str] = list(explicit_global_reasons)
    previous_by_id: dict[str, LeanCatalogPreflightEntry] = {}
    global_invalidation = (
        force_all or previous_manifest is None or bool(explicit_global_reasons)
    )
    if force_all:
        invalidated_by.append("force_all")
    elif previous_manifest is None:
        invalidated_by.append("missing_previous_manifest")
    else:
        previous_by_id = {
            entry.entry_id: entry for entry in previous_manifest.entries
        }
        if previous_manifest.environment_digest != environment_digest:
            global_invalidation = True
            invalidated_by.append("environment_digest_changed")
        if previous_manifest.checker_implementation_digest != checker_digest:
            global_invalidation = True
            invalidated_by.append("checker_implementation_changed")

    check_ids: set[str] = set(candidate_ids) if global_invalidation else set()
    if not global_invalidation:
        for candidate in ordered_candidates:
            previous = previous_by_id.get(candidate.entry_id)
            if previous is None or previous.to_dict() != candidate.accepted_entry().to_dict():
                check_ids.add(candidate.entry_id)
        if check_ids:
            invalidated_by.append("entry_key_changed")

    requested_canaries = set(canary_entry_ids)
    unknown_canaries = requested_canaries.difference(candidate_ids)
    if unknown_canaries:
        raise LeanCatalogAuditError(
            "Lean catalog audit canary entries are unknown: "
            + ", ".join(sorted(unknown_canaries))
        )
    canary_only_ids: set[str] = set()
    if check_ids:
        canary_only_ids = requested_canaries.difference(check_ids)
        check_ids.update(requested_canaries)

    final_entries: list[LeanCatalogPreflightEntry] = []
    for candidate in ordered_candidates:
        if candidate.entry_id not in check_ids:
            final_entries.append(previous_by_id[candidate.entry_id])
            continue
        checked = checker(candidate)
        expected = candidate.accepted_entry()
        if checked.status != "accepted" or checked.proof_digest is None:
            raise LeanCatalogAuditError(
                f"Lean catalog audit checker rejected {candidate.entry_id}"
            )
        if checked.to_dict() != expected.to_dict():
            raise LeanCatalogAuditError(
                f"Lean catalog audit checker evidence mismatch for {candidate.entry_id}"
            )
        final_entries.append(checked)

    reusable_manifest = (
        previous_manifest is not None
        and not check_ids
        and previous_manifest.source_digests == dict(sorted(source_digests.items()))
        and previous_manifest.entries == tuple(final_entries)
    )
    if reusable_manifest:
        manifest = previous_manifest
    else:
        manifest = build_lean_catalog_preflight_manifest(
            entries=final_entries,
            source_digests=source_digests,
            environment_digest=environment_digest,
            checker_implementation_digest=checker_digest,
            generated_at=generated_at,
        )
    return LeanCatalogAuditResult(
        manifest=manifest,
        reused_entry_count=len(ordered_candidates) - len(check_ids),
        rechecked_entry_count=len(check_ids),
        canary_entry_count=len(canary_only_ids),
        invalidated_by=tuple(invalidated_by),
    )


def refresh_lean_catalog_preflight_manifest(
    *,
    manifest_path: str | Path,
    candidates: Iterable[LeanCatalogAuditCandidate],
    previous_manifest: LeanCatalogPreflightManifest | None,
    source_digests: dict[str, str],
    generated_at: str,
    checker: Callable[[LeanCatalogAuditCandidate], LeanCatalogPreflightEntry],
    force_all: bool = False,
    canary_entry_ids: Iterable[str] = (),
    global_invalidation_reasons: Iterable[str] = (),
) -> LeanCatalogAuditResult:
    """Audit first, then atomically replace the tracked manifest on success."""

    result = audit_lean_catalog(
        candidates=candidates,
        previous_manifest=previous_manifest,
        source_digests=source_digests,
        generated_at=generated_at,
        checker=checker,
        force_all=force_all,
        canary_entry_ids=canary_entry_ids,
        global_invalidation_reasons=global_invalidation_reasons,
    )
    _write_manifest_atomic(Path(manifest_path), result.manifest)
    return result


def lean_checker_implementation_digest(
    paths: Iterable[str | Path] | None = None,
) -> str:
    """Digest checker and source-assembly code by content, not a manual version tag."""

    selected = tuple(Path(path).resolve() for path in paths) if paths else (
        _DEFAULT_CHECKER_IMPLEMENTATION_PATHS
    )
    body: dict[str, str] = {}
    for path in selected:
        try:
            relative = path.relative_to(_REPOSITORY_ROOT).as_posix()
        except ValueError:
            relative = path.as_posix()
        body[relative] = _bytes_digest(path.read_bytes())
    return digest_json(dict(sorted(body.items())))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally verify or refresh Lean catalog checker evidence."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--refresh", action="store_true")
    parser.add_argument("--force-all", action="store_true")
    args = parser.parse_args(argv)

    result = _run_default_catalog_audit(
        refresh=bool(args.refresh),
        force_all=bool(args.force_all),
    )
    print(json.dumps(result.summary(), ensure_ascii=False, sort_keys=True))
    return 0


def _run_default_catalog_audit(
    *,
    refresh: bool,
    force_all: bool,
) -> LeanCatalogAuditResult:
    # 延迟导入避免 paper_catalog 在普通 loader 路径中形成模块循环。
    import tokenshare.experiments.paper_catalog as paper_catalog
    from tokenshare.storage.artifacts import ArtifactStore

    lean_cases = tuple(
        paper_catalog._with_lean_v1_paper_difficulty(case)
        for case in paper_catalog._load_jsonl(DEFAULT_LEAN_CATALOG_PATH)
    )
    graph_cases = tuple(
        paper_catalog._load_lean_lemma_graph_cases(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG_PATH
        )
    )
    paper_catalog._validate_unique_case_ids(lean_cases + graph_cases)
    for case in lean_cases:
        paper_catalog._validate_lean_case(case)
    for case in graph_cases:
        paper_catalog._validate_lean_lemma_graph_case(case)

    semantic_authority = load_lean_semantic_authority(
        repository_root=_REPOSITORY_ROOT
    )
    environment_digest = semantic_authority.authority_environment_digest
    paper_catalog._validate_lean_environment_digest(
        lean_cases,
        expected_digest=environment_digest,
    )
    paper_catalog._validate_lean_lemma_graph_environment_digest(
        graph_cases,
        expected_digest=environment_digest,
    )
    candidates = paper_catalog._lean_catalog_audit_candidates(
        lean_cases=lean_cases,
        lean_lemma_graph_cases=graph_cases,
        environment_digest=environment_digest,
        checker_implementation_digest=(
            semantic_authority.authority_checker_implementation_digest
        ),
    )
    source_digests = {
        "lean_catalog": paper_catalog._file_digest(DEFAULT_LEAN_CATALOG_PATH),
        "lean_lemma_graph_catalog": paper_catalog._file_digest(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG_PATH
        ),
    }
    previous_manifest = _best_previous_manifest(
        DEFAULT_LOCAL_CACHE_MANIFEST_PATH,
        DEFAULT_TRACKED_MANIFEST_PATH,
    )
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    with tempfile.TemporaryDirectory(prefix="tokenshare_lean_catalog_audit_") as root:
        artifact_store = ArtifactStore(Path(root))
        real_environment: list[object] = []
        built_oracle_paths: set[str] = set()

        def checker(candidate: LeanCatalogAuditCandidate) -> LeanCatalogPreflightEntry:
            if not real_environment:
                current_authority = load_lean_semantic_authority(
                    repository_root=_REPOSITORY_ROOT
                )
                if (
                    current_authority.sidecar_digest
                    != semantic_authority.sidecar_digest
                    or current_authority.semantic_environment_digest
                    != semantic_authority.semantic_environment_digest
                    or current_authority.semantic_checker_digest
                    != semantic_authority.semantic_checker_digest
                ):
                    raise LeanCatalogAuditError(
                        "Lean semantic authority changed between audit and preflight"
                    )
                environment_manifest = paper_catalog._default_lean_environment_manifest()
                real_environment.append(environment_manifest)
            environment_manifest = real_environment[0]

            if candidate.node_id is not None:
                for case in graph_cases:
                    oracle_ref = case.get("oracle_proof_package_ref")
                    if not isinstance(oracle_ref, dict):
                        continue
                    source_path = str(oracle_ref["source_path"])
                    if source_path in built_oracle_paths:
                        continue
                    paper_catalog._build_lean_oracle_package(
                        oracle_ref,
                        environment_manifest=environment_manifest,
                    )
                    built_oracle_paths.add(source_path)
            return _run_real_candidate_checker(
                candidate,
                artifact_store=artifact_store,
                environment_manifest=environment_manifest,
            )

        audit_kwargs = {
            "candidates": candidates,
            "previous_manifest": previous_manifest,
            "source_digests": source_digests,
            "generated_at": generated_at,
            "checker": checker,
            "force_all": force_all,
        }
        if refresh:
            result = refresh_lean_catalog_preflight_manifest(
                manifest_path=DEFAULT_TRACKED_MANIFEST_PATH,
                **audit_kwargs,
            )
        else:
            result = audit_lean_catalog(**audit_kwargs)
        _write_manifest_atomic(DEFAULT_LOCAL_CACHE_MANIFEST_PATH, result.manifest)
        return result


def _run_real_candidate_checker(
    candidate: LeanCatalogAuditCandidate,
    *,
    artifact_store: object,
    environment_manifest: object,
) -> LeanCatalogPreflightEntry:
    from tokenshare.plugins.lean_proof.checker import (
        LeanCheckerMode,
        LeanCheckerRequest,
        check_lean_proof,
    )
    from tokenshare.plugins.lean_proof.environment import build_lean_environment_ref

    artifact_id = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in candidate.entry_id
    )
    theorem_ref = artifact_store.save_json(
        candidate.theorem_payload.to_dict(),
        artifact_id=f"{artifact_id}_theorem_payload",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "lean_catalog_audit", "entry_id": candidate.entry_id},
        metadata={"theorem_name": candidate.theorem_payload.theorem_name},
        created_at="2026-07-22T00:00:00Z",
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{candidate.entry_id}:audit",
            "theorem_payload_digest": candidate.theorem_payload.payload_digest,
            "proof_source": candidate.proof_source,
            "created_at": "2026-07-22T00:00:00Z",
        },
        artifact_id=f"{artifact_id}_proof_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "lean_catalog_audit", "entry_id": candidate.entry_id},
        metadata={"theorem_name": candidate.theorem_payload.theorem_name},
        created_at="2026-07-22T00:00:00Z",
    )
    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"lean_catalog_audit:{candidate.entry_id}",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode(candidate.checker_mode),
            timeout_seconds=int(candidate.resource_limits["timeout_seconds"]),
            max_output_bytes=int(candidate.resource_limits["max_output_bytes"]),
            created_at="2026-07-22T00:00:00Z",
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    return LeanCatalogPreflightEntry(
        entry_id=candidate.entry_id,
        case_id=candidate.case_id,
        node_id=candidate.node_id,
        checker_mode=candidate.checker_mode,
        generated_source_digest=candidate.generated_source_digest,
        oracle_proof_digest=candidate.oracle_proof_digest,
        environment_digest=candidate.environment_digest,
        checker_implementation_digest=candidate.checker_implementation_digest,
        resource_limits=dict(candidate.resource_limits),
        entry_key=candidate.entry_key,
        normalized_theorem_digest=report.normalized_theorem_digest,
        proof_digest=report.proof_digest,
        status=report.status.value,
    )


def _best_previous_manifest(*paths: Path) -> LeanCatalogPreflightManifest | None:
    for path in paths:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                return validate_lean_catalog_preflight_manifest(body)
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


def _proof_digest_bundle(entries: Iterable[LeanCatalogPreflightEntry]) -> str:
    return digest_json(
        [
            {
                "entry_id": entry.entry_id,
                "status": entry.status,
                "normalized_theorem_digest": entry.normalized_theorem_digest,
                "proof_digest": entry.proof_digest,
            }
            for entry in entries
        ]
    )


def _bytes_digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _write_manifest_atomic(
    path: Path,
    manifest: LeanCatalogPreflightManifest,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(
                manifest.to_dict(),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
