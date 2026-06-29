"""Lean toolchain environment manifests and EnvironmentRef mapping."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import EnvironmentRef
from tokenshare.plugins.lean_proof.models import canonical_json_digest
from tokenshare.plugins.lean_proof.schemas import LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True, kw_only=True)
class LeanEnvironmentManifest:
    project_root: str
    lean_executable: str
    lake_executable: str
    lean_version: str
    lake_version: str
    toolchain_file_digest: str
    lakefile_digest: str
    import_set_digest: str
    helper_sources_digest: str
    fixture_profile_digest: str
    resource_limits: JsonObject
    created_at: str
    environment_digest: str | None = None
    schema_version: str = LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION}"
            )
        for field_name in (
            "project_root",
            "lean_executable",
            "lake_executable",
            "lean_version",
            "lake_version",
            "created_at",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        for field_name in (
            "toolchain_file_digest",
            "lakefile_digest",
            "import_set_digest",
            "helper_sources_digest",
            "fixture_profile_digest",
        ):
            _require_digest(field_name, getattr(self, field_name))
        if not isinstance(self.resource_limits, dict):
            raise ValueError("resource_limits must be an object")
        _set_or_check_digest(self, "environment_digest", self._digest_body())

    @classmethod
    def from_project(
        cls,
        *,
        project_root: Path,
        lean_executable: Path,
        lake_executable: Path,
        lean_version: str,
        lake_version: str,
        resource_limits: JsonObject,
        created_at: str,
        imports: list[str] | None = None,
    ) -> "LeanEnvironmentManifest":
        root = project_root.resolve()
        helper_digests = _helper_source_digests(root)
        helper_sources_digest = canonical_json_digest(helper_digests)
        return cls(
            project_root=str(root),
            lean_executable=str(lean_executable.resolve()),
            lake_executable=str(lake_executable.resolve()),
            lean_version=lean_version,
            lake_version=lake_version,
            toolchain_file_digest=_file_digest(root / "lean-toolchain"),
            lakefile_digest=_lakefile_digest(root),
            import_set_digest=canonical_json_digest(imports or ["Init"]),
            helper_sources_digest=helper_sources_digest,
            fixture_profile_digest=canonical_json_digest(
                {
                    "project_root": str(root),
                    "toolchain_file_digest": _file_digest(root / "lean-toolchain"),
                    "lakefile_digest": _lakefile_digest(root),
                    "helper_sources_digest": helper_sources_digest,
                    "imports": imports or ["Init"],
                }
            ),
            resource_limits=dict(resource_limits),
            created_at=created_at,
        )

    @classmethod
    def from_dict(cls, data: JsonObject) -> "LeanEnvironmentManifest":
        return cls(
            schema_version=data.get(
                "schema_version",
                LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION,
            ),
            project_root=data["project_root"],
            lean_executable=data["lean_executable"],
            lake_executable=data["lake_executable"],
            lean_version=data["lean_version"],
            lake_version=data["lake_version"],
            toolchain_file_digest=data["toolchain_file_digest"],
            lakefile_digest=data["lakefile_digest"],
            import_set_digest=data["import_set_digest"],
            helper_sources_digest=data["helper_sources_digest"],
            fixture_profile_digest=data["fixture_profile_digest"],
            resource_limits=dict(data["resource_limits"]),
            created_at=data["created_at"],
            environment_digest=data.get("environment_digest"),
        )

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["created_at"] = self.created_at
        body["environment_digest"] = self.environment_digest
        return body

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "lean_executable": self.lean_executable,
            "lake_executable": self.lake_executable,
            "lean_version": self.lean_version,
            "lake_version": self.lake_version,
            "toolchain_file_digest": self.toolchain_file_digest,
            "lakefile_digest": self.lakefile_digest,
            "import_set_digest": self.import_set_digest,
            "helper_sources_digest": self.helper_sources_digest,
            "fixture_profile_digest": self.fixture_profile_digest,
            "resource_limits": dict(self.resource_limits),
        }


def build_lean_environment_ref(manifest: LeanEnvironmentManifest) -> EnvironmentRef:
    digest_component = (manifest.environment_digest or "sha256:missing").replace("sha256:", "")
    return EnvironmentRef(
        environment_id=f"lean_environment:{digest_component[:16]}",
        environment_digest=manifest.environment_digest or "",
        runtime="lean",
        tool_versions={
            "lean_version": manifest.lean_version,
            "lake_version": manifest.lake_version,
            "lean_executable": manifest.lean_executable,
            "lake_executable": manifest.lake_executable,
            "toolchain_file_digest": manifest.toolchain_file_digest,
            "lakefile_digest": manifest.lakefile_digest,
            "import_set_digest": manifest.import_set_digest,
            "helper_sources_digest": manifest.helper_sources_digest,
            "lake_project_root": manifest.project_root,
        },
        resource_limits=dict(manifest.resource_limits),
        fixture_profile_digest=manifest.fixture_profile_digest,
        seed=None,
        clock_policy="fixed",
        created_at=manifest.created_at,
    )


def _helper_source_digests(project_root: Path) -> dict[str, str]:
    helper_dir = project_root / "TokenShare"
    if not helper_dir.is_dir():
        raise FileNotFoundError(f"missing Lean helper directory: {helper_dir}")
    digests: dict[str, str] = {}
    for path in sorted(helper_dir.rglob("*.lean")):
        relative = path.relative_to(project_root).as_posix()
        digests[relative] = _file_digest(path)
    if not digests:
        raise FileNotFoundError(f"missing Lean helper sources: {helper_dir}")
    return digests


def _lakefile_digest(project_root: Path) -> str:
    lakefile_lean = project_root / "lakefile.lean"
    lakefile_toml = project_root / "lakefile.toml"
    if lakefile_lean.is_file():
        return _file_digest(lakefile_lean)
    if lakefile_toml.is_file():
        return _file_digest(lakefile_toml)
    raise FileNotFoundError(f"missing lakefile in {project_root}")


def _file_digest(path: Path) -> str:
    data = path.read_bytes()
    return f"sha256:{sha256(data).hexdigest()}"


def _set_or_check_digest(instance: object, field_name: str, data: Any) -> None:
    expected = canonical_json_digest(data)
    current = getattr(instance, field_name)
    if current is None:
        object.__setattr__(instance, field_name, expected)
        return
    if current != expected:
        raise ValueError(f"{field_name} must match canonical JSON digest")


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")
