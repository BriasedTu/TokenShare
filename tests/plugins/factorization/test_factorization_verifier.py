from tokenshare.core.models import ArtifactRef
from tokenshare.plugins.factorization.models import (
    FactorIntegerSubject,
    FactorSearchRangeInput,
    RangeResult,
    RootInput,
)
from tokenshare.plugins.factorization.schemas import REQUESTED_OUTPUT_PRIME_FACTORIZATION
from tokenshare.plugins.factorization.validator import (
    verify_factor_integer_subject,
    verify_range_result,
)
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-27T00:00:00Z"


def test_found_factor_verifier_rejects_factor_outside_range() -> None:
    child_input = _range_input(target_n="221", range_start="2", range_end="10")
    result = _found_result(target_n="221", range_start="10", range_end="14", factor="13")
    forged = {**result.to_dict(), "range_start": "2", "range_end": "10"}

    verification = verify_range_result(forged, child_input=child_input)

    assert verification.accepted is False
    assert verification.status == "rejected"
    assert verification.layer_summary["status"] == "rejected"
    assert verification.layer_summary["reason_code"] == "factor_outside_range"


def test_found_factor_verifier_rejects_non_divisor() -> None:
    child_input = _range_input(target_n="91", range_start="2", range_end="9")
    forged = {
        **_found_result(
            target_n="91",
            range_start="2",
            range_end="9",
            factor="7",
            cofactor="13",
        ).to_dict(),
        "found_factor": "8",
        "cofactor": "11",
    }

    verification = verify_range_result(forged, child_input=child_input)

    assert verification.accepted is False
    assert verification.layer_summary["reason_code"] == "non_divisor"
    assert verification.failure_summary == {
        "failure_kind": "invalid_output",
        "failed_layer": "plugin_domain_check",
        "message": "found_factor does not divide target_n",
        "evidence_refs": [],
    }


def test_range_verifier_rejects_target_or_coverage_mismatch() -> None:
    child_input = _range_input(target_n="221", range_start="2", range_end="10")

    target_mismatch = verify_range_result(
        _no_factor_result(
            target_n="229",
            range_start="2",
            range_end="10",
            coverage_id=child_input.coverage_id,
            partition_params_digest=child_input.partition_params_digest,
        ),
        child_input=child_input,
    )
    assert target_mismatch.accepted is False
    assert target_mismatch.layer_summary["reason_code"] == "target_mismatch"

    coverage_mismatch = verify_range_result(
        _no_factor_result(
            target_n="221",
            range_start="2",
            range_end="10",
            coverage_id="coverage_other",
            partition_params_digest=child_input.partition_params_digest,
        ),
        child_input=child_input,
    )
    assert coverage_mismatch.accepted is False
    assert coverage_mismatch.layer_summary["reason_code"] == "coverage_mismatch"

    params_mismatch = verify_range_result(
        _no_factor_result(
            target_n="221",
            range_start="2",
            range_end="10",
            coverage_id=child_input.coverage_id,
            partition_params_digest="sha256:other",
        ),
        child_input=child_input,
    )
    assert params_mismatch.accepted is False
    assert params_mismatch.layer_summary["reason_code"] == "partition_params_mismatch"


def test_no_factor_verifier_rechecks_range_and_rejects_false_claim() -> None:
    child_input = _range_input(target_n="221", range_start="10", range_end="14")
    result = _no_factor_result(
        target_n="221",
        range_start="10",
        range_end="14",
        coverage_id=child_input.coverage_id,
        partition_params_digest=child_input.partition_params_digest,
    )

    verification = verify_range_result(result, child_input=child_input)

    assert verification.accepted is False
    assert verification.layer_summary["reason_code"] == "divisor_exists_in_range"
    assert verification.layer_summary["details"] == {"divisor": "13"}


def test_no_factor_verifier_rejects_range_that_exceeds_recheck_budget() -> None:
    child_input = _range_input(target_n="1000003", range_start="2", range_end="1000")
    result = _no_factor_result(
        target_n="1000003",
        range_start="2",
        range_end="1000",
        coverage_id=child_input.coverage_id,
        partition_params_digest=child_input.partition_params_digest,
    )

    verification = verify_range_result(
        result,
        child_input=child_input,
        no_factor_recheck_max_divisors=10,
    )

    assert verification.accepted is False
    assert verification.layer_summary["reason_code"] == "range_recheck_budget_exceeded"
    assert verification.layer_summary["details"] == {
        "requested_divisor_count": 999,
        "max_divisor_count": 10,
    }


def test_range_verifier_rejects_structured_dict_that_bypasses_range_result_schema() -> None:
    child_input = _range_input(target_n="21", range_start="2", range_end="4")
    incomplete = {
        "result_kind": "found_factor",
        "target_n": "21",
        "range_start": "2",
        "range_end": "4",
        "coverage_id": child_input.coverage_id,
        "child_index": child_input.child_index,
        "partition_params_digest": child_input.partition_params_digest,
        "found_factor": "3",
        "cofactor": "7",
    }

    verification = verify_range_result(incomplete, child_input=child_input)

    assert verification.accepted is False
    assert verification.layer_summary["reason_code"] == "invalid_output"

    wrong_schema = {
        **_found_result(target_n="21", range_start="2", range_end="4", factor="3").to_dict(),
        "schema_version": "factorization.range_result.v999",
    }
    schema_verification = verify_range_result(wrong_schema, child_input=child_input)

    assert schema_verification.accepted is False
    assert schema_verification.layer_summary["reason_code"] == "invalid_output"


def test_factor_integer_subject_verifier_accepts_matching_root_input_artifact(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    root_input = RootInput(
        target_n="91",
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label="subject_ok",
    )
    root_ref = _save_root_input(store, "root_input_91", root_input)
    subject = _subject(target_n="91", root_ref=root_ref)

    verification = verify_factor_integer_subject(
        subject,
        root_input_ref=root_ref,
        root_input_body=root_input.to_dict(),
    )

    assert verification.accepted is True
    assert verification.status == "passed"
    assert verification.layer_summary["reason_code"] == "factor_integer_subject_checked"
    assert verification.layer_summary["details"]["target_n"] == "91"


def test_factor_integer_subject_verifier_rejects_forged_source_ref_digest(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    root_input = RootInput(
        target_n="91",
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label="subject_forged_digest",
    )
    root_ref = _save_root_input(store, "root_input_91", root_input)
    forged_ref = ArtifactRef.from_dict(
        {**root_ref.to_dict(), "content_hash": "sha256:forged"}
    )
    subject = _subject(target_n="91", root_ref=forged_ref)

    verification = verify_factor_integer_subject(
        subject,
        root_input_ref=root_ref,
        root_input_body=root_input.to_dict(),
    )

    assert verification.accepted is False
    assert verification.status == "rejected"
    assert verification.layer_summary["reason_code"] == "source_ref_mismatch"


def test_factor_integer_subject_verifier_rejects_root_input_target_mismatch(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    root_input = RootInput(
        target_n="91",
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label="subject_target_mismatch",
    )
    root_ref = _save_root_input(store, "root_input_91", root_input)
    subject = _subject(target_n="97", root_ref=root_ref)

    verification = verify_factor_integer_subject(
        subject,
        root_input_ref=root_ref,
        root_input_body=root_input.to_dict(),
    )

    assert verification.accepted is False
    assert verification.layer_summary["reason_code"] == "target_mismatch"


def _range_input(
    *,
    target_n: str,
    range_start: str,
    range_end: str,
    coverage_id: str = "coverage_1",
    partition_params_digest: str = "sha256:params",
) -> FactorSearchRangeInput:
    return FactorSearchRangeInput(
        target_n=target_n,
        range_start=range_start,
        range_end=range_end,
        coverage_id=coverage_id,
        child_index=0,
        child_count=1,
        partition_params_digest=partition_params_digest,
    )


def _save_root_input(
    store: ArtifactStore,
    artifact_id: str,
    root_input: RootInput,
) -> ArtifactRef:
    return store.save_json(
        root_input.to_dict(),
        artifact_id=artifact_id,
        artifact_type="RootInput",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"output_name": "root_input"},
        created_at=CREATED_AT,
    )


def _subject(*, target_n: str, root_ref: ArtifactRef) -> FactorIntegerSubject:
    return FactorIntegerSubject(
        subject_id=f"factor_integer_subject:test:{target_n}",
        task_id="task_factor_subject",
        unit_id="unit_factor_subject",
        target_n=target_n,
        source_kind="root_input",
        source_ref=root_ref.to_dict(),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=CREATED_AT,
    )


def _found_result(
    *,
    target_n: str,
    range_start: str,
    range_end: str,
    factor: str,
    cofactor: str | None = None,
    coverage_id: str = "coverage_1",
    partition_params_digest: str = "sha256:params",
) -> RangeResult:
    return RangeResult(
        range_result_id="range_result:unit_2:attempt_1:coverage_1:0",
        result_kind="found_factor",
        target_n=target_n,
        range_start=range_start,
        range_end=range_end,
        coverage_id=coverage_id,
        child_index=0,
        partition_params_digest=partition_params_digest,
        found_factor=factor,
        cofactor=cofactor or str(int(target_n) // int(factor)),
        checked_divisor_count=max(0, int(range_end) - int(range_start) + 1),
        executor_summary={"checked": "bounded range"},
        created_at=CREATED_AT,
    )


def _no_factor_result(
    *,
    target_n: str,
    range_start: str,
    range_end: str,
    coverage_id: str,
    partition_params_digest: str,
) -> RangeResult:
    return RangeResult(
        range_result_id="range_result:unit_2:attempt_1:coverage_1:0",
        result_kind="no_factor_in_range",
        target_n=target_n,
        range_start=range_start,
        range_end=range_end,
        coverage_id=coverage_id,
        child_index=0,
        partition_params_digest=partition_params_digest,
        found_factor=None,
        cofactor=None,
        checked_divisor_count=max(0, int(range_end) - int(range_start) + 1),
        executor_summary={"checked": "bounded range"},
        created_at=CREATED_AT,
    )
