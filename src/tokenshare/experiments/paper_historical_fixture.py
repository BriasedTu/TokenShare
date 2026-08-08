"""Task 23 冻结历史真实 Factorization 正向样本的只读导入边界。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


FIXTURE_SCHEMA_VERSION = "tokenshare.paper_historical_real_fixture.v1"
FIXTURE_ID = "historical_real_factorization_single_leaf.v1"
CLASSIFICATION = "regression_only"
FACILITY_SEMANTICS = "facility_path_only/not_formal_range_semantics"
POSITIVE_SOURCE_ROOT = Path(
    r"E:\TokenEcnomic\TokenShareData\outputs\experiments\heiyucode_gpt56_smoke_20260716"
)

EXPECTED_CASE_ROW_DIGEST = (
    "sha256:e5a64df6b3a006a89a9f100823cf35473574ba90a86b4832f05422899386ec27"
)
EXPECTED_BATCH_REPORT_DIGEST = (
    "sha256:391556f8d3d59a6fa26d90dff30ee2fe7f40891c16705657d215226e56138ad3"
)
EXPECTED_RAW_MODEL_OUTPUT_DIGEST = (
    "sha256:da5c0cff478969af32a84637593d031db3512ae65e8a7e43086155c7b4ce5b69"
)
EXPECTED_PROVENANCE_DIGEST = (
    "sha256:e7fd14cc4602c496be1a93921a8ff13d29310f9505b606bd452aaf704cb3c0e2"
)
EXPECTED_RAW_PAYLOAD_DIGEST = (
    "sha256:fa9402c04ac4b7a6bb0c9d70af386084ab4cc844f131bf110c60902cf6742baf"
)
EXPECTED_POSITIVE_SOURCE_TREE_DIGEST = (
    "sha256:b3e61b280c635006a894719e5b6d816efdceab49c24f0c41d5d3db9711e46404"
)

_FIXTURE_FILES = frozenset(
    {
        "fixture_manifest.json",
        "batch_report.json",
        "per_number_result.json",
        "raw_model_output.json",
        "provenance.json",
        "request.json",
        "usage.json",
    }
)
_SOURCE_OBJECTS = {
    "case_row": EXPECTED_CASE_ROW_DIGEST,
    "batch_report": EXPECTED_BATCH_REPORT_DIGEST,
    "raw_model_output": EXPECTED_RAW_MODEL_OUTPUT_DIGEST,
    "provenance": EXPECTED_PROVENANCE_DIGEST,
    "full_tree": EXPECTED_POSITIVE_SOURCE_TREE_DIGEST,
}
_MINIMAL_KEYS = {
    "batch_report.json": {
        "accuracy",
        "attempted_count",
        "correct_count",
        "failed_count",
        "provider_attempt_count",
        "schema_version",
        "source_object_digest",
    },
    "per_number_result.json": {
        "final_correctness",
        "normalized_prime_factors",
        "oracle_match",
        "oracle_prime_factors",
        "primality_check_passed",
        "product_check_passed",
        "schema_version",
        "source_object_digest",
        "status",
        "target_n",
    },
    "raw_model_output.json": {
        "content_text",
        "finish_reason",
        "model",
        "provider_family",
        "schema_version",
        "source_object_digest",
    },
    "provenance.json": {
        "acquisition_result_kind",
        "entry_id",
        "latency_ms",
        "model",
        "provider_family",
        "sanitization",
        "schema_version",
        "source_object_digest",
    },
    "request.json": {
        "direct_output_contract_id",
        "parser_id",
        "request_id",
        "schema_version",
        "source_object_digest",
        "target_n",
    },
    "usage.json": {
        "completion_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "schema_version",
        "total_tokens",
    },
}
_SECRET_KEY = re.compile(r"api[_-]?key|authorization|secret", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HistoricalRealFixture:
    root: Path
    manifest: dict[str, Any]
    batch_report: dict[str, Any]
    per_number_result: dict[str, Any]
    raw_model_output: dict[str, Any]
    provenance: dict[str, Any]
    request: dict[str, Any]
    usage: dict[str, Any]

    @property
    def success(self) -> bool:
        row = self.per_number_result
        return (
            row.get("status") == "passed"
            and row.get("final_correctness") is True
            and row.get("oracle_match") is True
        )

    @property
    def source_digests(self) -> dict[str, str]:
        return dict(_SOURCE_OBJECTS)

    @property
    def raw_payload_bytes(self) -> bytes:
        return str(self.raw_model_output["content_text"]).encode("utf-8")

    @property
    def raw_payload_digest(self) -> str:
        return _digest_bytes(self.raw_payload_bytes)


def load_historical_real_fixture(root: str | Path) -> HistoricalRealFixture:
    """加载且完整校验 tracked 脱敏包；缺失对象时严格失败。"""

    fixture_root = Path(root)
    if not fixture_root.is_dir():
        raise FileNotFoundError(f"historical fixture root is missing: {fixture_root}")
    actual_files = {path.name for path in fixture_root.iterdir() if path.is_file()}
    if actual_files != _FIXTURE_FILES:
        raise ValueError("historical fixture must contain the exact seven tracked objects")

    fixture_bytes = {
        name: _read_canonical_fixture_json_bytes(fixture_root / name)
        for name in _FIXTURE_FILES
    }
    objects = {
        name: _parse_json_object(fixture_bytes[name], object_name=name)
        for name in _FIXTURE_FILES
    }
    manifest = objects["fixture_manifest.json"]
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported historical fixture schema")
    if manifest.get("fixture_id") != FIXTURE_ID or manifest.get("marker") != FIXTURE_ID:
        raise ValueError("historical fixture marker mismatch")
    if manifest.get("classification") != CLASSIFICATION:
        raise ValueError("historical fixture classification must be regression_only")
    if manifest.get("paper_eligible") is not False:
        raise ValueError("historical fixture can never be paper eligible")
    if manifest.get("semantics") != FACILITY_SEMANTICS:
        raise ValueError("historical fixture semantics mismatch")
    source = _mapping(manifest.get("source"), "source")
    if source.get("file_count") != 24:
        raise ValueError("historical source file count mismatch")
    if source.get("tree_digest") != EXPECTED_POSITIVE_SOURCE_TREE_DIGEST:
        raise ValueError("historical source tree digest mismatch")
    source_objects = _mapping(source.get("objects"), "source.objects")
    for name, expected in _SOURCE_OBJECTS.items():
        if name == "full_tree":
            continue
        if source_objects.get(name) != expected:
            raise ValueError(f"historical source object digest mismatch: {name}")

    fixture_objects = _mapping(manifest.get("fixture_objects"), "fixture_objects")
    expected_fixture_names = _FIXTURE_FILES - {"fixture_manifest.json"}
    if set(fixture_objects) != expected_fixture_names:
        raise ValueError("fixture object digest inventory mismatch")
    for name in sorted(expected_fixture_names):
        if fixture_objects[name] != _digest_bytes(fixture_bytes[name]):
            raise ValueError(f"tracked fixture object digest mismatch: {name}")
        if set(objects[name]) != _MINIMAL_KEYS[name]:
            raise ValueError(f"tracked fixture object is not the frozen minimal shape: {name}")

    _reject_secrets(objects)
    fixture = HistoricalRealFixture(
        root=fixture_root,
        manifest=manifest,
        batch_report=objects["batch_report.json"],
        per_number_result=objects["per_number_result.json"],
        raw_model_output=objects["raw_model_output.json"],
        provenance=objects["provenance.json"],
        request=objects["request.json"],
        usage=objects["usage.json"],
    )
    if not fixture.success:
        raise ValueError("historical success requires passed/correct/oracle per-number row")
    if "summary" in fixture.batch_report or "passed" in fixture.batch_report:
        raise ValueError("historical batch report must not invent summary.passed")
    if {
        "attempted_count": fixture.batch_report.get("attempted_count"),
        "correct_count": fixture.batch_report.get("correct_count"),
        "failed_count": fixture.batch_report.get("failed_count"),
        "accuracy": fixture.batch_report.get("accuracy"),
    } != {
        "attempted_count": 1,
        "correct_count": 1,
        "failed_count": 0,
        "accuracy": 1.0,
    }:
        raise ValueError("historical batch report success counts mismatch")
    if fixture.per_number_result.get("target_n") != manifest.get("target_n"):
        raise ValueError("historical fixture target mismatch")
    if fixture.raw_payload_digest != manifest.get("raw_payload_digest"):
        raise ValueError("historical raw payload bytes were modified")
    if fixture.raw_payload_digest != EXPECTED_RAW_PAYLOAD_DIGEST:
        raise ValueError("historical raw payload digest mismatch")
    fixture_source_bindings = {
        "batch_report.json": EXPECTED_BATCH_REPORT_DIGEST,
        "per_number_result.json": EXPECTED_CASE_ROW_DIGEST,
        "raw_model_output.json": EXPECTED_RAW_MODEL_OUTPUT_DIGEST,
        "provenance.json": EXPECTED_PROVENANCE_DIGEST,
    }
    for name, expected in fixture_source_bindings.items():
        if objects[name].get("source_object_digest") != expected:
            raise ValueError(f"tracked fixture source binding mismatch: {name}")
    json.loads(fixture.raw_payload_bytes.decode("utf-8"))
    return fixture


def validate_positive_source(root: str | Path) -> dict[str, str]:
    """只读复核冻结 positive source；不会在缺口处发起在线补全。"""

    source_root = Path(root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"positive historical source is missing: {source_root}")
    paths = {
        "case_row": source_root / "per_number_results.jsonl",
        "batch_report": source_root / "batch_report.json",
        "raw_model_output": source_root
        / "runs/run_000000/artifacts/raw_model_output_submission_factorization_500_0",
        "provenance": source_root
        / "runs/run_000000/artifacts/ai_provider_provenance_submission_factorization_500_0",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"positive source object is missing: {name}")
        if _digest_bytes(path.read_bytes()) != _SOURCE_OBJECTS[name]:
            raise ValueError(f"positive source digest mismatch: {name}")
    if len([path for path in source_root.rglob("*") if path.is_file()]) != 24:
        raise ValueError("positive source file count mismatch")
    rows = [
        json.loads(line)
        for line in paths["case_row"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("positive source requires one unique per-number row")
    row = rows[0]
    if not (
        row.get("status") == "passed"
        and row.get("final_correctness") is True
        and row.get("oracle_match") is True
    ):
        raise ValueError("positive source per-number success predicate failed")
    batch = _read_json_object(paths["batch_report"])
    if "summary" in batch or "passed" in batch:
        raise ValueError("positive source has no summary.passed field")
    if (
        batch.get("attempted_count"),
        batch.get("correct_count"),
        batch.get("failed_count"),
        batch.get("accuracy"),
    ) != (1, 1, 0, 1.0):
        raise ValueError("positive source batch counts mismatch")
    raw = _read_json_object(paths["raw_model_output"])
    content_text = raw.get("content_text")
    if not isinstance(content_text, str):
        raise ValueError("positive source raw object is missing direct payload bytes")
    if _digest_bytes(content_text.encode("utf-8")) != EXPECTED_RAW_PAYLOAD_DIGEST:
        raise ValueError("positive source direct payload digest mismatch")
    tree_digest = source_tree_digest(source_root)
    if tree_digest != EXPECTED_POSITIVE_SOURCE_TREE_DIGEST:
        raise ValueError("positive source full tree digest mismatch")
    return dict(_SOURCE_OBJECTS)


def source_tree_digest(root: str | Path) -> str:
    """实现计划冻结的 ordinal POSIX relative-path `sha256_tree_v1`。"""

    source_root = Path(root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"source tree is missing: {source_root}")
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    digest = sha256()
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        data = path.read_bytes()
        content_digest = _digest_bytes(data).removeprefix("sha256:")
        record = f"{relative}\0{len(data)}\0{content_digest}\n"
        digest.update(record.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required fixture object is missing: {path.name}")
    return _parse_json_object(path.read_bytes(), object_name=path.name)


def _read_canonical_fixture_json_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"required fixture object is missing: {path.name}")
    data = path.read_bytes()
    without_crlf = data.replace(b"\r\n", b"")
    if b"\r" in without_crlf:
        raise ValueError(f"historical fixture has invalid line ending: {path.name}")
    return data.replace(b"\r\n", b"\n")


def _parse_json_object(data: bytes, *, object_name: str) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"fixture object must be a JSON object: {object_name}")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _reject_secrets(value: object, *, key: str = "") -> None:
    if key and _SECRET_KEY.search(key):
        raise ValueError(f"tracked historical fixture contains forbidden secret field: {key}")
    if isinstance(value, Mapping):
        for name, member in value.items():
            _reject_secrets(member, key=str(name))
    elif isinstance(value, list):
        for member in value:
            _reject_secrets(member, key=key)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("tracked historical fixture contains a secret-like value")
