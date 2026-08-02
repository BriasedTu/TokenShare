from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.experiments.historical_real_factorization_single_leaf import (
    classify_20260731_exp34_negative,
    reproject_historical_real_factorization_single_leaf,
    run_historical_real_factorization_single_leaf,
)
from tokenshare.experiments.paper_direct_results import PaperDirectRootResult
from tokenshare.experiments.paper_historical_fixture import (
    POSITIVE_SOURCE_ROOT,
    load_historical_real_fixture,
    validate_positive_source,
)
from tokenshare.core.models import ArtifactRef
from tokenshare.core.verification import digest_json
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger
from tokenshare.storage.events import EventType


FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "fixtures"
    / "paper"
    / "historical_real_factorization_v1"
)
EXPECTED_CASE_ROW_DIGEST = "sha256:e5a64df6b3a006a89a9f100823cf35473574ba90a86b4832f05422899386ec27"
EXPECTED_BATCH_REPORT_DIGEST = "sha256:391556f8d3d59a6fa26d90dff30ee2fe7f40891c16705657d215226e56138ad3"
EXPECTED_RAW_MODEL_OUTPUT_DIGEST = "sha256:da5c0cff478969af32a84637593d031db3512ae65e8a7e43086155c7b4ce5b69"
EXPECTED_PROVENANCE_DIGEST = "sha256:e7fd14cc4602c496be1a93921a8ff13d29310f9505b606bd452aaf704cb3c0e2"
EXPECTED_POSITIVE_SOURCE_TREE_DIGEST = "sha256:b3e61b280c635006a894719e5b6d816efdceab49c24f0c41d5d3db9711e46404"
EXPECTED_TRACKED_FIXTURE_TREE_DIGEST = "sha256:e7e6fd2d86b3099aab521dabb9aa03666858ddc85e3f740daab16b135196f9c8"


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _bytes_digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _tree_digest(root: Path) -> str:
    digest = sha256()
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        content_digest = sha256(data).hexdigest()
        digest.update(f"{relative}\0{len(data)}\0{content_digest}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _read_ref(store: ArtifactStore, value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    ref = ArtifactRef.from_dict(value)
    body = json.loads(store.read_bytes(ref).decode("utf-8"))
    assert isinstance(body, dict)
    return body


def _secret_like_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, member in value.items():
            if any(token in key.lower() for token in ("api_key", "authorization", "secret")):
                found.add(key)
            found.update(_secret_like_keys(member))
    elif isinstance(value, list):
        for member in value:
            found.update(_secret_like_keys(member))
    return found


def test_real_success_predicate_uses_per_number_passed_correct_oracle_not_missing_summary_passed() -> None:
    fixture = load_historical_real_fixture(FIXTURE_ROOT)

    assert fixture.success is True
    assert fixture.per_number_result["status"] == "passed"
    assert fixture.per_number_result["final_correctness"] is True
    assert fixture.per_number_result["oracle_match"] is True
    assert "summary" not in fixture.batch_report
    assert "passed" not in fixture.batch_report


def test_case_row_batch_raw_provenance_and_full_tree_digests_are_exact() -> None:
    fixture = load_historical_real_fixture(FIXTURE_ROOT)

    assert fixture.source_digests == {
        "case_row": EXPECTED_CASE_ROW_DIGEST,
        "batch_report": EXPECTED_BATCH_REPORT_DIGEST,
        "raw_model_output": EXPECTED_RAW_MODEL_OUTPUT_DIGEST,
        "provenance": EXPECTED_PROVENANCE_DIGEST,
        "full_tree": EXPECTED_POSITIVE_SOURCE_TREE_DIGEST,
    }
    assert _tree_digest(FIXTURE_ROOT) == EXPECTED_TRACKED_FIXTURE_TREE_DIGEST
    manifest = _json(FIXTURE_ROOT / "fixture_manifest.json")
    fixture_objects = manifest["fixture_objects"]
    assert isinstance(fixture_objects, dict)
    for name, expected in fixture_objects.items():
        assert _bytes_digest(FIXTURE_ROOT / name) == expected
    if POSITIVE_SOURCE_ROOT.is_dir():
        source_paths = {
            "case_row": POSITIVE_SOURCE_ROOT / "per_number_results.jsonl",
            "batch_report": POSITIVE_SOURCE_ROOT / "batch_report.json",
            "raw_model_output": POSITIVE_SOURCE_ROOT / "runs/run_000000/artifacts/raw_model_output_submission_factorization_500_0",
            "provenance": POSITIVE_SOURCE_ROOT / "runs/run_000000/artifacts/ai_provider_provenance_submission_factorization_500_0",
        }
        assert {name: _bytes_digest(path) for name, path in source_paths.items()} == {
            "case_row": EXPECTED_CASE_ROW_DIGEST,
            "batch_report": EXPECTED_BATCH_REPORT_DIGEST,
            "raw_model_output": EXPECTED_RAW_MODEL_OUTPUT_DIGEST,
            "provenance": EXPECTED_PROVENANCE_DIGEST,
        }
        before = _tree_digest(POSITIVE_SOURCE_ROOT)
        validated = validate_positive_source(POSITIVE_SOURCE_ROOT)
        after = _tree_digest(POSITIVE_SOURCE_ROOT)
        assert validated == fixture.source_digests
        assert before == after == EXPECTED_POSITIVE_SOURCE_TREE_DIGEST


def test_tracked_fixture_contains_minimal_sanitized_unmodified_payload_and_source_hashes() -> None:
    fixture = load_historical_real_fixture(FIXTURE_ROOT)
    fixture_files = {path.name for path in FIXTURE_ROOT.iterdir() if path.is_file()}

    assert fixture_files == {
        "fixture_manifest.json",
        "batch_report.json",
        "per_number_result.json",
        "raw_model_output.json",
        "provenance.json",
        "request.json",
        "usage.json",
    }
    assert fixture.raw_payload_bytes == fixture.raw_model_output["content_text"].encode("utf-8")
    assert fixture.raw_payload_digest == fixture.manifest["raw_payload_digest"]
    assert not _secret_like_keys(
        [_json(path) for path in sorted(FIXTURE_ROOT.glob("*.json"))]
    )
    assert fixture.manifest["source"]["file_count"] == 24


def test_dedicated_single_leaf_adapter_runs_normal_coordinator_parser_verifier_canonical_merge_ledger_to_table(
    tmp_path: Path,
) -> None:
    result = run_historical_real_factorization_single_leaf(
        fixture_root=FIXTURE_ROOT,
        output_root=tmp_path,
    )

    assert isinstance(result.direct_result, PaperDirectRootResult)
    assert result.direct_result.final_result_reference_complete is True
    assert result.direct_result.independently_verified_correct is True
    assert result.direct_result.ineligibility_reasons == ("regression_only",)
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "historical_single_leaf.jsonl")
    events = ledger.read_all()
    assert ledger.verify_hash_chain() is True
    assert tuple(event.event_seq for event in events) == tuple(range(1, len(events) + 1))
    manifests = [_json(path) for path in sorted((tmp_path / "artifacts").glob("*.manifest.json"))]
    raw = next(ref for ref in manifests if ref["artifact_type"] == "RawModelOutput")
    parsed = next(ref for ref in manifests if ref["artifact_type"] == "ParsedModelOutput")
    assert parsed["source"]["role"] == "parser_result"
    assert raw["artifact_id"] != parsed["artifact_id"]
    canonical = next(
        ref
        for ref in manifests
        if ref["artifact_type"] == "canonical_output"
        and isinstance(ref["source"], dict)
        and (ref["source"].get("parsed_output_ref") or {}).get("artifact_id") == parsed["artifact_id"]
    )
    candidate_ref = canonical["source"]["candidate_output_ref"]
    assert candidate_ref["artifact_type"] == "CandidateOutput"
    assert candidate_ref["artifact_id"] != parsed["artifact_id"]
    leaf_submission_event = next(
        event
        for event in events
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["unit_id"] != result.protocol_result.root_unit_id
        and _read_ref(store, event.payload["submission_ref"])["raw_output_ref"] is not None
    )
    leaf_submission = _read_ref(store, leaf_submission_event.payload["submission_ref"])
    assert leaf_submission["parsed_output_ref"]["artifact_type"] == "ParsedModelOutput"
    assert leaf_submission["candidate_output_refs"]["direct_factorization_answer"]["artifact_type"] == "canonical_output"
    final = result.direct_result.final_result_ref
    assert final is not None
    assert final.source_role == "final_result"
    assert result.direct_result.canonical_acceptance_ref.event_seq < result.direct_result.merge_ref.event_seq
    assert result.direct_result.merge_ref.event_seq < result.direct_result.terminal_root_event_ref.event_seq
    assert {ref.source_role for ref in result.direct_result.verifier_checker_refs} == {
        "independent_verdict",
        "verification_report",
    }
    row = result.regression_table[0]
    direct_body = result.direct_result.to_dict()
    assert row["direct_result"] == direct_body
    assert row["direct_result_digest"] == digest_json(direct_body)
    assert row["execution_binding_digest"] == result.direct_result.execution_binding.binding_digest
    assert row["facility_success"] is result.direct_result.independently_verified_correct

    verdict = next(
        ref
        for ref in result.direct_result.verifier_checker_refs
        if ref.source_role == "independent_verdict"
    )
    (tmp_path / "artifacts" / f"{verdict.artifact_id}.manifest.json").unlink()
    with pytest.raises(ValueError, match="artifact|verdict|manifest|verification"):
        reproject_historical_real_factorization_single_leaf(
            fixture_root=FIXTURE_ROOT,
            output_root=tmp_path,
        )


def test_adapter_rejects_formal_range_catalog_and_never_claims_range_semantics(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="single-leaf|range"):
        run_historical_real_factorization_single_leaf(
            fixture_root=FIXTURE_ROOT,
            output_root=tmp_path / "range",
            formal_range_catalog={"catalog_id": "paper_factorization_ranges"},
        )

    result = run_historical_real_factorization_single_leaf(
        fixture_root=FIXTURE_ROOT,
        output_root=tmp_path / "single",
    )
    assert result.semantics == "facility_path_only/not_formal_range_semantics"
    assert result.formal_range_semantics is False


def test_fixture_provider_calls_zero_and_classification_regression_only(
    tmp_path: Path,
) -> None:
    result = run_historical_real_factorization_single_leaf(
        fixture_root=FIXTURE_ROOT,
        output_root=tmp_path,
    )

    assert result.provider_call_count == 0
    assert result.network_call_count == 0
    assert result.classification == "regression_only"
    assert result.paper_eligible is False


def test_fixture_can_never_be_trace_paper_eligible(tmp_path: Path) -> None:
    result = run_historical_real_factorization_single_leaf(
        fixture_root=FIXTURE_ROOT,
        output_root=tmp_path,
    )

    with pytest.raises(ValueError, match="paper|trace"):
        result.require_evidence_class("real_model_trace_protocol_run")
    assert result.require_evidence_class("regression_only") == "regression_only"


def test_20260731_exp34_is_negative_only_and_source_hash_unchanged(
    tmp_path: Path,
) -> None:
    negative_root = tmp_path / "exp34-smoke-20260731-041442"
    negative_root.mkdir()
    evidence = negative_root / "immutable.json"
    evidence.write_bytes(b'{"status":"expected_failure"}')
    before_bytes = evidence.read_bytes()

    classification = classify_20260731_exp34_negative(negative_root)

    assert classification["source_id"] == "exp34-smoke-20260731-041442"
    assert classification["classification"] == "negative_only_expected_fail"
    assert classification["paper_eligible"] is False
    assert classification["tree_digest_before"] == classification["tree_digest_after"]
    assert evidence.read_bytes() == before_bytes
