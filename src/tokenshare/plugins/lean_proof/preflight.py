"""Preflight checks for the pinned local Lean proof environment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import EnvironmentRef
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)


class LeanPreflightStatus(str, Enum):
    READY = "ready"
    BLOCKED_MISSING_TOOLCHAIN = "blocked_missing_toolchain"
    BLOCKED_MISSING_FIXTURE_PROJECT = "blocked_missing_fixture_project"
    BLOCKED_ENVIRONMENT_DIGEST_MISMATCH = "blocked_environment_digest_mismatch"


@dataclass(frozen=True)
class LeanPreflightResult:
    status: LeanPreflightStatus
    ready: bool
    environment_ref: EnvironmentRef | None
    environment_manifest: LeanEnvironmentManifest | None
    blocked_reason: JsonObject | None

    def to_dict(self) -> JsonObject:
        return {
            "status": self.status.value,
            "ready": self.ready,
            "environment_ref": (
                self.environment_ref.to_dict() if self.environment_ref is not None else None
            ),
            "environment_manifest": (
                self.environment_manifest.to_dict()
                if self.environment_manifest is not None
                else None
            ),
            "blocked_reason": dict(self.blocked_reason or {}),
        }


def run_lean_preflight(
    *,
    project_root: Path,
    lean_executable: Path,
    lake_executable: Path,
    resource_limits: JsonObject,
    created_at: str,
    lean_version: str | None = None,
    lake_version: str | None = None,
) -> LeanPreflightResult:
    missing_tools = [
        str(path)
        for path in (lean_executable, lake_executable)
        if not Path(path).is_file()
    ]
    if missing_tools:
        return _blocked(
            LeanPreflightStatus.BLOCKED_MISSING_TOOLCHAIN,
            {
                "blocker_kind": "blocked_missing_toolchain",
                "missing_paths": missing_tools,
            },
        )

    missing_project_files = _missing_fixture_project_files(project_root)
    if missing_project_files:
        return _blocked(
            LeanPreflightStatus.BLOCKED_MISSING_FIXTURE_PROJECT,
            {
                "blocker_kind": "blocked_missing_fixture_project",
                "missing_paths": missing_project_files,
            },
        )

    manifest = LeanEnvironmentManifest.from_project(
        project_root=project_root,
        lean_executable=lean_executable,
        lake_executable=lake_executable,
        lean_version=lean_version or "unknown",
        lake_version=lake_version or "unknown",
        resource_limits=resource_limits,
        created_at=created_at,
    )
    return LeanPreflightResult(
        status=LeanPreflightStatus.READY,
        ready=True,
        environment_ref=build_lean_environment_ref(manifest),
        environment_manifest=manifest,
        blocked_reason=None,
    )


def _missing_fixture_project_files(project_root: Path) -> list[str]:
    required = [
        project_root / "lean-toolchain",
        project_root / "TokenShare" / "Helper.lean",
        project_root / "TokenShare" / "SplitRules.lean",
        project_root / "TokenShare" / "Merge.lean",
    ]
    if not (project_root / "lakefile.lean").is_file() and not (
        project_root / "lakefile.toml"
    ).is_file():
        required.append(project_root / "lakefile.lean")
    return [str(path) for path in required if not path.is_file()]


def _blocked(status: LeanPreflightStatus, reason: JsonObject) -> LeanPreflightResult:
    return LeanPreflightResult(
        status=status,
        ready=False,
        environment_ref=None,
        environment_manifest=None,
        blocked_reason=reason,
    )
