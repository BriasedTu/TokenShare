import pytest

from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.models import (
    CandidateRangeCoverageProof,
    FactorIntegerSubject,
    FactorSearchRangeInput,
    FactorizationMergeResult,
    PrimeFactor,
    PrimeFactorizationResult,
    RangeResult,
)
from tokenshare.plugins.factorization.schemas import (
    FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_SCHEMA_VERSION,
)


CREATED_AT = "2026-06-27T00:00:00Z"


def test_factorization_descriptor_declares_unit_types_contracts_and_policies() -> None:
    descriptor = build_factorization_plugin_descriptor()
    descriptor_retry = build_factorization_plugin_descriptor()
    body = descriptor.to_dict()

    assert descriptor.plugin_id == "factorization"
    assert descriptor.plugin_version == "0.1.0"
    assert descriptor.supported_task_types == [
        "root",
        "factor_integer",
        "factor_search_range",
        "factorization_merge",
    ]
    assert descriptor.descriptor_digest == descriptor_retry.descriptor_digest
    assert body["descriptor_digest"] == descriptor.descriptor_digest

    output_contracts = body["output_contracts"]
    assert output_contracts["factor_integer_subject"]["output_contract_id"] == (
        "factorization.factor_integer_subject.contract.v1"
    )
    assert output_contracts["factor_integer_subject"]["required_outputs"] == [
        "factor_integer_subject"
    ]
    assert output_contracts["factor_integer_subject"]["output_schema_refs"] == {
        "factor_integer_subject": {
            "schema_version": FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
            "artifact_schema_id": "factorization.factor_integer_subject",
            "artifact_schema_version": "v1",
        }
    }
    assert output_contracts["range_result"]["output_contract_id"] == (
        "factorization.range_result.contract.v1"
    )
    assert output_contracts["range_result"]["required_outputs"] == ["range_result"]
    assert output_contracts["range_result"]["output_schema_refs"] == {
        "range_result": {
            "schema_version": RANGE_RESULT_SCHEMA_VERSION,
            "artifact_schema_id": "factorization.range_result",
            "artifact_schema_version": "v1",
        }
    }
    assert output_contracts["factorization_result"]["output_contract_id"] == (
        "factorization.merge_result.contract.v1"
    )

    split_strategy = body["split_strategies"]["factorization.candidate_range_partition.v1"]
    assert split_strategy["allowed_unit_types"] == ["factor_search_range"]
    assert split_strategy["validator_policy_id"] == "factorization.range_result.validator.v1"
    assert split_strategy["merge_policy_id"] == "factorization.all_required_range_merge.v1"
    assert split_strategy["durable_subgoal_policy"] == {
        "only_promote_unit_types": ["factor_search_range"],
        "requires_bounded_candidate_range": True,
        "executor_may_define_task_graph": False,
    }
    assert split_strategy["candidate_artifact_policy"] == {
        "required_structured_output": "range_result",
        "required_schema_version": RANGE_RESULT_SCHEMA_VERSION,
        "raw_text_authoritative": False,
        "executor_may_submit_candidates": True,
        "executor_may_define_task_graph": False,
    }

    assert body["validator_policy_id"] == "factorization.range_result.validator.v1"
    assert body["merge_policy_id"] == "factorization.all_required_range_merge.v1"
    assert set(body["execution_contracts"]) == {
        "deterministic_local",
        "mock_ai_bounded_search",
        "environment_policy",
    }
    assert body["execution_contracts"]["mock_ai_bounded_search"]["prompt_package"] == {
        "required": True,
        "builder": "factorization.build_factor_search_prompt_package.v1",
        "prompt_owner": "factorization_plugin",
        "executor_may_define_prompt": False,
        "executor_may_define_output_schema": False,
        "prompt_package_schema": "phase3.prompt_package.v1",
    }
    assert body["metadata"]["first_slice_limitations"] == {
        "early_success": "deferred",
        "sibling_pruning": "deferred",
        "composite_cofactor_recursive_resolution": "deferred",
    }
    assert body["metadata"]["recursive_policy"] == {
        "same_plugin_for_recursive_factor_integer": True,
        "continuation_plugin_allowed": False,
    }


def test_factor_integer_subject_rejects_invalid_decimal_integer() -> None:
    subject = _make_factor_integer_subject(target_n="21")

    assert subject.schema_version == FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION
    assert subject.target_n == "21"
    assert subject.target_n_digest.startswith("sha256:")
    assert subject.to_dict()["target_n"] == "21"

    for invalid_target_n in ["", "0", "1", "01", "+2", "-3", "2.0", " 2", "2 "]:
        with pytest.raises(ValueError, match="target_n"):
            _make_factor_integer_subject(target_n=invalid_target_n)

    with pytest.raises(TypeError, match="target_n"):
        _make_factor_integer_subject(target_n=21)  # type: ignore[arg-type]


def test_range_result_requires_found_factor_fields_only_for_found_factor() -> None:
    found = _make_range_result(
        result_kind="found_factor",
        target_n="21",
        range_start="2",
        range_end="4",
        found_factor="3",
        cofactor="7",
    )

    assert found.schema_version == RANGE_RESULT_SCHEMA_VERSION
    assert found.to_dict()["found_factor"] == "3"
    assert found.to_dict()["cofactor"] == "7"

    no_factor = _make_range_result(
        result_kind="no_factor_in_range",
        target_n="21",
        range_start="4",
        range_end="4",
        found_factor=None,
        cofactor=None,
    )
    assert no_factor.to_dict()["found_factor"] is None
    assert no_factor.to_dict()["cofactor"] is None

    with pytest.raises(ValueError, match="found_factor"):
        _make_range_result(
            result_kind="found_factor",
            target_n="21",
            range_start="2",
            range_end="4",
            found_factor=None,
            cofactor=None,
        )
    with pytest.raises(ValueError, match="cofactor"):
        _make_range_result(
            result_kind="found_factor",
            target_n="21",
            range_start="2",
            range_end="4",
            found_factor="3",
            cofactor=None,
        )
    with pytest.raises(ValueError, match="no_factor_in_range"):
        _make_range_result(
            result_kind="no_factor_in_range",
            target_n="21",
            range_start="2",
            range_end="4",
            found_factor="3",
            cofactor="7",
        )
    with pytest.raises(ValueError, match="found_factor"):
        _make_range_result(
            result_kind="found_factor",
            target_n="21",
            range_start="4",
            range_end="4",
            found_factor="3",
            cofactor="7",
        )


def test_range_result_rejects_range_end_beyond_sqrt_bound() -> None:
    with pytest.raises(ValueError, match="range_end must be <= floor_sqrt"):
        _make_range_result(
            result_kind="no_factor_in_range",
            target_n="21",
            range_start="2",
            range_end="20",
            found_factor=None,
            cofactor=None,
        )


def test_factorization_models_reject_bool_integer_fields() -> None:
    with pytest.raises(TypeError, match="child_index"):
        FactorSearchRangeInput(
            target_n="21",
            range_start="2",
            range_end="4",
            coverage_id="coverage_21",
            child_index=True,  # type: ignore[arg-type]
            child_count=2,
            partition_params_digest="sha256:params",
        )
    with pytest.raises(TypeError, match="child_count"):
        FactorSearchRangeInput(
            target_n="21",
            range_start="2",
            range_end="4",
            coverage_id="coverage_21",
            child_index=0,
            child_count=True,  # type: ignore[arg-type]
            partition_params_digest="sha256:params",
        )
    with pytest.raises(TypeError, match="child_index"):
        _make_range_result(
            result_kind="no_factor_in_range",
            target_n="21",
            range_start="2",
            range_end="4",
            found_factor=None,
            cofactor=None,
            child_index=True,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="checked_divisor_count"):
        _make_range_result(
            result_kind="no_factor_in_range",
            target_n="21",
            range_start="2",
            range_end="4",
            found_factor=None,
            cofactor=None,
            checked_divisor_count=True,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="range_count"):
        CandidateRangeCoverageProof(
            coverage_id="coverage_21",
            target_n="21",
            domain_start="2",
            domain_end="4",
            range_count=True,  # type: ignore[arg-type]
            ranges_digest="sha256:ranges",
            no_gap=True,
            no_overlap=True,
            full_domain_covered=True,
            sqrt_bound_checked=True,
            created_by_strategy_id="factorization.candidate_range_partition.v1",
        )
    with pytest.raises(TypeError, match="exponent"):
        PrimeFactor(prime="2", exponent=True)  # type: ignore[arg-type]


def test_prime_factorization_result_requires_prime_factors_product_check() -> None:
    result = _make_prime_factorization_result(
        target_n="12",
        prime_factors=[
            PrimeFactor(prime="2", exponent=2),
            PrimeFactor(prime="3", exponent=1),
        ],
    )

    assert result.schema_version == PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION
    assert result.product_check_passed is True
    assert result.factor_multiset_digest.startswith("sha256:")
    assert result.to_dict()["prime_factors"] == [
        {"prime": "2", "exponent": 2},
        {"prime": "3", "exponent": 1},
    ]
    assert result.to_dict()["primality_evidence"]["verified_prime_values"] == ["2", "3"]

    with pytest.raises(ValueError, match="ascending"):
        _make_prime_factorization_result(
            target_n="6",
            prime_factors=[
                PrimeFactor(prime="3", exponent=1),
                PrimeFactor(prime="2", exponent=1),
            ],
        )
    with pytest.raises(ValueError, match="exponent"):
        _make_prime_factorization_result(
            target_n="6",
            prime_factors=[
                PrimeFactor(prime="2", exponent=0),
                PrimeFactor(prime="3", exponent=1),
            ],
        )
    with pytest.raises(ValueError, match="primality_evidence"):
        PrimeFactorizationResult(
            result_id="prime_factorization:missing:evidence",
            target_n="12",
            prime_factors=[PrimeFactor(prime="4", exponent=1)],
            source_kind="semiprime_merge",
            source_merge_result_id="merge_result_1",
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="verified_prime_values"):
        PrimeFactorizationResult(
            result_id="prime_factorization:bad:evidence",
            target_n="4",
            prime_factors=[PrimeFactor(prime="4", exponent=1)],
            source_kind="semiprime_merge",
            source_merge_result_id="merge_result_1",
            primality_evidence={
                "policy_id": "factorization.trial_division_primality.v1",
                "verified_prime_values": ["2"],
                "verification_scope": "merge_policy_budgeted_check",
            },
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="product"):
        _make_prime_factorization_result(
            target_n="12",
            prime_factors=[
                PrimeFactor(prime="2", exponent=1),
                PrimeFactor(prime="3", exponent=1),
            ],
        )
    with pytest.raises(ValueError, match="unique"):
        _make_prime_factorization_result(
            target_n="12",
            prime_factors=[
                PrimeFactor(prime="2", exponent=1),
                PrimeFactor(prime="2", exponent=1),
                PrimeFactor(prime="3", exponent=1),
            ],
        )


def test_factorization_merge_result_requires_complete_required_slot_set_for_final_outputs() -> None:
    with pytest.raises(ValueError, match="range_result_count"):
        _make_factorization_merge_result(
            result_kind="prime_certificate",
            range_result_count=1,
            required_slot_count=4,
            slot_result_digests=["sha256:slot1"],
            found_factor=None,
            cofactor=None,
            prime_factorization_ref=None,
        )
    with pytest.raises(ValueError, match="slot_result_digests"):
        _make_factorization_merge_result(
            result_kind="prime_certificate",
            range_result_count=4,
            required_slot_count=4,
            slot_result_digests=["sha256:slot1"],
            found_factor=None,
            cofactor=None,
            prime_factorization_ref=None,
        )


def _make_factor_integer_subject(target_n: str) -> FactorIntegerSubject:
    return FactorIntegerSubject(
        subject_id="factor_subject:task_1:unit_1:target",
        task_id="task_1",
        unit_id="unit_1",
        target_n=target_n,
        source_kind="root_input",
        source_ref={"artifact_id": "root_input_1", "content_hash": "sha256:root"},
        requested_output="prime_factorization_result",
        created_at=CREATED_AT,
    )


def _make_range_result(
    *,
    result_kind: str,
    target_n: str,
    range_start: str,
    range_end: str,
    found_factor: str | None,
    cofactor: str | None,
    child_index: int = 0,
    checked_divisor_count: int = 4,
) -> RangeResult:
    return RangeResult(
        range_result_id="range_result:unit_2:attempt_1:coverage_1:0",
        result_kind=result_kind,
        target_n=target_n,
        range_start=range_start,
        range_end=range_end,
        coverage_id="coverage_1",
        child_index=child_index,
        partition_params_digest="sha256:params",
        found_factor=found_factor,
        cofactor=cofactor,
        checked_divisor_count=checked_divisor_count,
        executor_summary={"checked": "bounded range"},
        created_at=CREATED_AT,
    )


def _make_prime_factorization_result(
    *,
    target_n: str,
    prime_factors: list[PrimeFactor],
) -> PrimeFactorizationResult:
    return PrimeFactorizationResult(
        result_id="prime_factorization:target:factors",
        target_n=target_n,
        prime_factors=prime_factors,
        source_kind="semiprime_merge",
        source_merge_result_id="merge_result_1",
        primality_evidence={
            "policy_id": "factorization.trial_division_primality.v1",
            "verified_prime_values": [factor.prime for factor in prime_factors],
            "verification_scope": "merge_policy_budgeted_check",
        },
        created_at=CREATED_AT,
    )


def _make_factorization_merge_result(
    *,
    result_kind: str,
    range_result_count: int,
    required_slot_count: int,
    slot_result_digests: list[str],
    found_factor: str | None,
    cofactor: str | None,
    prime_factorization_ref: dict | None,
) -> FactorizationMergeResult:
    return FactorizationMergeResult(
        merge_result_id="factorization_merge:merge_plan_1:unit_merge",
        target_n="101",
        coverage_id="coverage_1",
        partition_params_digest="sha256:params",
        result_kind=result_kind,
        range_result_count=range_result_count,
        required_slot_count=required_slot_count,
        coverage_digest="sha256:coverage",
        slot_result_digests=slot_result_digests,
        found_factor=found_factor,
        cofactor=cofactor,
        prime_factorization_ref=prime_factorization_ref,
        created_at=CREATED_AT,
    )
