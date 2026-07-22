"""Content-addressed evidence models for Lean catalog preflight audits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tokenshare.core.models import JsonObject
from tokenshare.experiments.paper_models import digest_json


LEAN_CATALOG_PREFLIGHT_MANIFEST_SCHEMA_VERSION = (
    "tokenshare.lean_catalog_preflight_manifest.v1"
)


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
