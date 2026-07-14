"""Frozen paper catalog loading and validation."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from math import isqrt
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import (
    PAPER_DIFFICULTIES,
    PAPER_DOMAINS,
    JsonObject,
    digest_json,
)
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.preflight import run_lean_preflight
from tokenshare.storage.artifacts import ArtifactStore


DIFFICULTIES = PAPER_DIFFICULTIES
CREATED_AT = "2026-07-14T00:00:00Z"
LEAN_RESOURCE_LIMITS: JsonObject = {"timeout_seconds": 30, "max_output_bytes": 65536}
LEAN_VERSION = "Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)"
LAKE_VERSION = "Lake version 5.0.0-df668f0 (Lean version 4.8.0)"
_LEAN_PREFLIGHT_CACHE: dict[str, JsonObject] = {}


@dataclass(frozen=True, kw_only=True)
class PaperInputCatalogManifest:
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    generator_version: str
    case_count: int
    domain_counts: dict[str, int]
    difficulty_counts: dict[str, dict[str, int]]
    oracle_validation_status: str
    lean_preflight_status: str
    lean_preflight_summary: JsonObject
    created_at: str
    source_files: list[str]
    factorization_cases: tuple[JsonObject, ...]
    lean_cases: tuple[JsonObject, ...]
    schema_version: str = "tokenshare.paper_input_catalog_manifest.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "generator_version": self.generator_version,
            "case_count": self.case_count,
            "domain_counts": dict(self.domain_counts),
            "difficulty_counts": {
                domain: dict(counts)
                for domain, counts in self.difficulty_counts.items()
            },
            "oracle_validation_status": self.oracle_validation_status,
            "lean_preflight_status": self.lean_preflight_status,
            "lean_preflight_summary": dict(self.lean_preflight_summary),
            "created_at": self.created_at,
            "source_files": list(self.source_files),
        }

    def cases_for(self, *, domain: str, difficulty: str) -> tuple[JsonObject, ...]:
        if domain not in PAPER_DOMAINS:
            raise ValueError("domain must be factorization or lean_proof")
        if difficulty not in DIFFICULTIES:
            raise ValueError("difficulty must be easy, medium, or hard")
        cases = self.factorization_cases if domain == "factorization" else self.lean_cases
        return tuple(case for case in cases if case["difficulty"] == difficulty)


def load_paper_catalogs(
    *,
    factorization_path: str | Path,
    lean_path: str | Path,
) -> PaperInputCatalogManifest:
    factor_path = Path(factorization_path)
    lean_catalog_path = Path(lean_path)
    factorization_cases = tuple(_load_jsonl(factor_path))
    lean_cases = tuple(_load_jsonl(lean_catalog_path))

    _validate_unique_case_ids(factorization_cases + lean_cases)
    for case in factorization_cases:
        _validate_factorization_case(case)
    for case in lean_cases:
        _validate_lean_case(case)
    _validate_distribution("factorization", factorization_cases)
    _validate_distribution("lean_proof", lean_cases)
    lean_preflight_summary = _run_lean_catalog_preflight(lean_cases)

    body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
    }
    domain_counts = {
        "factorization": len(factorization_cases),
        "lean_proof": len(lean_cases),
    }
    difficulty_counts = {
        "factorization": _difficulty_counts(factorization_cases),
        "lean_proof": _difficulty_counts(lean_cases),
    }
    return PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v1",
        catalog_digest=digest_json(body),
        generator_version="tokenshare.paper_catalog.static.v1",
        case_count=len(factorization_cases) + len(lean_cases),
        domain_counts=domain_counts,
        difficulty_counts=difficulty_counts,
        oracle_validation_status="passed",
        lean_preflight_status=str(lean_preflight_summary["status"]),
        lean_preflight_summary=lean_preflight_summary,
        created_at=CREATED_AT,
        source_files=[factor_path.as_posix(), lean_catalog_path.as_posix()],
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
    )


def estimated_ai_units_for_case(case: JsonObject) -> int:
    if case["schema_version"] == "tokenshare.paper_factorization_case.v1":
        split_params = case.get("split_params", {})
        requested = int(split_params.get("requested_child_count", 1))
        return max(1, requested)
    return max(1, int(case.get("expected_child_count", 1)))


def _load_jsonl(path: Path) -> list[JsonObject]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        rows.append(row)
    return rows


def _validate_unique_case_ids(cases: tuple[JsonObject, ...]) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)


def _validate_distribution(domain: str, cases: tuple[JsonObject, ...]) -> None:
    if len(cases) != 30:
        raise ValueError(f"{domain} catalog must contain exactly 30 cases")
    counts = _difficulty_counts(cases)
    expected = {difficulty: 10 for difficulty in DIFFICULTIES}
    if counts != expected:
        raise ValueError(f"{domain} catalog must contain 10 cases per difficulty")


def _difficulty_counts(cases: tuple[JsonObject, ...]) -> dict[str, int]:
    return {
        difficulty: sum(1 for case in cases if case.get("difficulty") == difficulty)
        for difficulty in DIFFICULTIES
    }


def _validate_factorization_case(case: JsonObject) -> None:
    _require_schema(case, "tokenshare.paper_factorization_case.v1")
    for field_name in (
        "case_id",
        "target_n",
        "oracle_prime_factors",
        "candidate_start",
        "candidate_end",
        "candidate_divisor_count",
        "factor_position_quantile",
        "difficulty",
        "split_params",
        "source_seed",
    ):
        if field_name not in case:
            raise ValueError(f"factorization case missing {field_name}")
    target_n = _decimal(case["target_n"], "target_n", min_value=2)
    candidate_start = _decimal(case["candidate_start"], "candidate_start", min_value=2)
    candidate_end = _decimal(case["candidate_end"], "candidate_end", min_value=2)
    if candidate_start > candidate_end:
        raise ValueError("candidate_start must be <= candidate_end")
    if candidate_end > isqrt(target_n):
        raise ValueError("candidate_end must be <= floor_sqrt(target_n)")
    if int(case["candidate_divisor_count"]) != candidate_end - candidate_start + 1:
        raise ValueError("candidate_divisor_count does not match candidate range")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError("invalid difficulty")
    factors = case["oracle_prime_factors"]
    if not isinstance(factors, list) or not factors:
        raise ValueError("oracle_prime_factors must be a non-empty list")
    product = 1
    for factor in factors:
        if not isinstance(factor, dict):
            raise ValueError("oracle prime factor must be an object")
        prime = _decimal(factor.get("prime"), "oracle prime", min_value=2)
        exponent = int(factor.get("exponent"))
        if exponent < 1:
            raise ValueError("oracle exponent must be >= 1")
        if not _is_prime(prime):
            raise ValueError("oracle prime factor must be prime")
        product *= prime**exponent
    if product != target_n:
        raise ValueError("oracle prime factors do not multiply to target_n")
    if not isinstance(case["split_params"], dict):
        raise ValueError("split_params must be an object")
    if isinstance(case["source_seed"], bool) or not isinstance(case["source_seed"], int):
        raise ValueError("source_seed must be an integer")


def _validate_lean_case(case: JsonObject) -> None:
    _require_schema(case, "tokenshare.paper_lean_case.v1")
    required = (
        "case_id",
        "theorem_payload",
        "difficulty",
        "expected_split_kind",
        "expected_child_count",
        "minimum_proof_steps",
        "context_item_count",
        "oracle_proof_ref",
        "environment_digest",
    )
    for field_name in required:
        if field_name not in case:
            raise ValueError(f"lean case missing {field_name}")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError("invalid difficulty")
    payload = case["theorem_payload"]
    if not isinstance(payload, dict):
        raise ValueError("theorem_payload must be an object")
    for field_name in ("schema_version", "theorem_name", "parameters_source", "statement_source"):
        if not payload.get(field_name):
            raise ValueError(f"theorem_payload missing {field_name}")
    if payload["schema_version"] != "lean_proof.theorem_payload.v1":
        raise ValueError("invalid theorem_payload schema_version")
    if case["expected_split_kind"] not in {"conjunction", "iff"}:
        raise ValueError("expected_split_kind must be conjunction or iff")
    if int(case["expected_child_count"]) < 1:
        raise ValueError("expected_child_count must be positive")
    minimum_steps = int(case["minimum_proof_steps"])
    if case["difficulty"] == "easy" and minimum_steps < 1:
        raise ValueError("easy Lean cases require at least 1 proof step")
    if case["difficulty"] == "medium" and minimum_steps < 2:
        raise ValueError("medium Lean cases require at least 2 proof steps")
    if case["difficulty"] == "hard" and minimum_steps < 4:
        raise ValueError("hard Lean cases require at least 4 proof steps")
    if int(case["context_item_count"]) < 0:
        raise ValueError("context_item_count must be non-negative")
    proof_ref = case["oracle_proof_ref"]
    if not isinstance(proof_ref, dict) or proof_ref.get("preflight_status") != "passed":
        raise ValueError("oracle_proof_ref must record passed preflight")
    if not isinstance(proof_ref.get("proof_source"), str) or not proof_ref["proof_source"]:
        raise ValueError("oracle_proof_ref must include proof_source")
    _require_digest("environment_digest", case["environment_digest"])


def _run_lean_catalog_preflight(lean_cases: tuple[JsonObject, ...]) -> JsonObject:
    environment_manifest = _default_lean_environment_manifest()
    cache_key = digest_json(
        {
            "lean_cases": lean_cases,
            "environment_digest": environment_manifest.environment_digest,
        }
    )
    cached = _LEAN_PREFLIGHT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    accepted_reports: list[JsonObject] = []
    with tempfile.TemporaryDirectory(prefix="tokenshare_paper_lean_preflight_") as root:
        artifact_store = ArtifactStore(Path(root))
        for case in lean_cases:
            report = _check_lean_catalog_case(
                case,
                artifact_store=artifact_store,
                environment_manifest=environment_manifest,
            )
            if report["status"] != LeanCheckerStatus.ACCEPTED.value:
                excerpt = str(report["diagnostics"].get("combined_excerpt", ""))[:600]
                raise ValueError(
                    f"Lean preflight rejected case {case['case_id']}: {excerpt}"
                )
            accepted_reports.append(report)

    summary: JsonObject = {
        "schema_version": "tokenshare.paper_lean_preflight_summary.v1",
        "status": "passed",
        "checked_case_count": len(lean_cases),
        "accepted_case_count": len(accepted_reports),
        "checker_mode": LeanCheckerMode.DIRECT_PROOF.value,
        "environment_digest": environment_manifest.environment_digest,
        "proof_digest_bundle": digest_json(
            [
                {
                    "case_id": report["case_id"],
                    "status": report["status"],
                    "proof_digest": report["proof_digest"],
                    "normalized_theorem_digest": report["normalized_theorem_digest"],
                }
                for report in accepted_reports
            ]
        ),
    }
    _LEAN_PREFLIGHT_CACHE[cache_key] = dict(summary)
    return summary


def _check_lean_catalog_case(
    case: JsonObject,
    *,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> JsonObject:
    theorem_payload = _lean_theorem_payload_from_case(case)
    case_id = str(case["case_id"])
    theorem_ref = artifact_store.save_json(
        theorem_payload.to_dict(),
        artifact_id=_safe_artifact_id(f"{case_id}_theorem_payload"),
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "paper_catalog_preflight", "case_id": case_id},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{case_id}:oracle_preflight",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": case["oracle_proof_ref"]["proof_source"],
            "created_at": CREATED_AT,
        },
        artifact_id=_safe_artifact_id(f"{case_id}_oracle_proof_candidate"),
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "paper_catalog_preflight", "case_id": case_id},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"paper_catalog_preflight:{case_id}",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=int(LEAN_RESOURCE_LIMITS["timeout_seconds"]),
            max_output_bytes=int(LEAN_RESOURCE_LIMITS["max_output_bytes"]),
            created_at=CREATED_AT,
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    return {
        "case_id": case_id,
        "status": report.status.value,
        "proof_digest": report.proof_digest,
        "normalized_theorem_digest": report.normalized_theorem_digest,
        "diagnostics": report.diagnostics,
    }


def _lean_theorem_payload_from_case(case: JsonObject) -> LeanTheoremPayload:
    payload = case["theorem_payload"]
    return LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case['case_id']}",
        theorem_name=payload["theorem_name"],
        imports=list(payload.get("imports", ["Init"])),
        namespace=payload.get("namespace", "TokenSharePaperCatalog"),
        open_namespaces=list(payload.get("open_namespaces", [])),
        options=dict(payload.get("options", {})),
        parameters_source=str(payload.get("parameters_source", "")),
        statement_source=payload["statement_source"],
        theorem_source=payload.get("theorem_source"),
        proof_candidate_ref=None,
        library_context=dict(
            payload.get(
                "library_context",
                {
                    "project": "tokenshare_lean",
                    "module": "TokenSharePaperCatalog",
                    "case_id": case["case_id"],
                },
            )
        ),
        decomposition_policy=dict(
            payload.get(
                "decomposition_policy",
                {
                    "policy_id": "lean_proof.deterministic_tactic_split.v1",
                    "allowed_rules": ["conjunction_intro", "iff_intro"],
                    "max_depth": 1,
                    "max_children": int(case["expected_child_count"]),
                    "unsupported_policy": "return_unsupported",
                },
            )
        ),
        resource_limits=dict(payload.get("resource_limits", LEAN_RESOURCE_LIMITS)),
    )


def _default_lean_environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_bin = tools_root / "elan-home" / "bin"
    preflight = run_lean_preflight(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_bin / "lean.exe",
        lake_executable=elan_bin / "lake.exe",
        lean_version=LEAN_VERSION,
        lake_version=LAKE_VERSION,
        resource_limits=LEAN_RESOURCE_LIMITS,
        created_at=CREATED_AT,
    )
    if not preflight.ready or preflight.environment_manifest is None:
        raise ValueError(f"Lean preflight blocked: {preflight.blocked_reason}")
    return preflight.environment_manifest

def _require_schema(case: JsonObject, schema_version: str) -> None:
    if case.get("schema_version") != schema_version:
        raise ValueError(f"schema_version must be {schema_version}")


def _decimal(value: Any, field_name: str, *, min_value: int) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError(f"{field_name} must be a decimal string")
    parsed = int(value)
    if parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}")
    return parsed


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _safe_artifact_id(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in value
    )
