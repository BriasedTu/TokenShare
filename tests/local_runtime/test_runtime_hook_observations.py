from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from types import MappingProxyType

import pytest

from tokenshare.core.models import ArtifactRef
from tokenshare.local_runtime import (
    ExperimentAblationGateAppliedPayloadV1,
    ExperimentFaultInjectedPayloadV1,
    ExperimentPrematureMergeAttemptedPayloadV1,
    GateDirective,
    ParsedCandidateDirective,
    RawOutputDirective,
    RuntimeHookObservationKind,
    RuntimeHookObservationV1,
    WorkerDirective,
    build_experiment_ablation_gate_applied_observation,
    build_experiment_fault_injected_observation,
    build_experiment_premature_merge_attempted_observation,
)
from tokenshare.local_runtime.coordinator import _collect_observations


SCHEMA_VERSION = "tokenshare.runtime_hook_observation.v1"
NOW = "2026-08-01T00:00:00Z"


def _artifact_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-1",
        artifact_type="experiment_evidence",
        uri="artifacts/artifact-1.json",
        content_hash="sha256:" + "1" * 64,
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="tokenshare.test.runtime_hook",
        artifact_schema_version="v1",
        source={"kind": "pytest", "nested": {"sample": [1, 2]}},
        metadata={"score": 1.5},
        created_at=NOW,
    )


def _fault_observation() -> RuntimeHookObservationV1:
    return build_experiment_fault_injected_observation(
        condition_id="condition-1",
        run_id="run-1",
        task_id="task-1",
        unit_id="unit-1",
        selected_target_ai_unit_id="planned-unit-1",
        attempt_id="attempt-1",
        fault_type="no_return",
        protocol_event_refs=(),
        artifact_refs=(_artifact_ref(),),
        occurred_at=NOW,
    )


def _ablation_observation() -> RuntimeHookObservationV1:
    return build_experiment_ablation_gate_applied_observation(
        ablation_mode="NO_MERGE_GATE",
        disabled_mechanism="merge_gate",
        protocol_event_refs=(
            {
                "event_id": "event-1",
                "event_seq": 7,
                "event_type": "RECOVERY_DECISION_RECORDED",
            },
            {
                "event_id": "event-2",
                "event_seq": 8,
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "event_hash": "sha256:" + "2" * 64,
            },
        ),
        artifact_refs=(_artifact_ref(),),
        hook_input={
            "gate_satisfied": False,
            "required_child_unit_ids": ["child-1", "child-2"],
        },
        hook_result={"bypass": True, "stop": False},
    )


def _premature_observation() -> RuntimeHookObservationV1:
    return build_experiment_premature_merge_attempted_observation(
        attempt_schema_version="tokenshare.premature_merge_attempt.v1",
        run_id="run-1",
        task_id="task-1",
        parent_unit_id="parent-1",
        required_child_unit_ids=("child-1", "child-2"),
        canonical_child_unit_ids=("child-1",),
        missing_child_unit_ids=("child-2",),
        attempt_status="executed",
        plugin_result_type=None,
        plugin_error="ValueError: missing child",
        root_check_passed=False,
        failure_kind="merge_readiness_unsatisfied",
        protocol_event_refs=(
            {
                "event_id": "event-2",
                "event_seq": 8,
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "event_hash": "sha256:" + "2" * 64,
            },
        ),
        result_artifact_ref=_artifact_ref(),
    )


@pytest.mark.parametrize(
    ("factory", "kind", "payload_type"),
    (
        (
            _fault_observation,
            RuntimeHookObservationKind.EXPERIMENT_FAULT_INJECTED,
            ExperimentFaultInjectedPayloadV1,
        ),
        (
            _ablation_observation,
            RuntimeHookObservationKind.EXPERIMENT_ABLATION_GATE_APPLIED,
            ExperimentAblationGateAppliedPayloadV1,
        ),
        (
            _premature_observation,
            RuntimeHookObservationKind.EXPERIMENT_PREMATURE_MERGE_ATTEMPTED,
            ExperimentPrematureMergeAttemptedPayloadV1,
        ),
    ),
)
def test_runtime_hook_observation_builders_round_trip_canonical_envelopes(
    factory,
    kind,
    payload_type,
) -> None:
    observation = factory()

    assert observation.schema_version == SCHEMA_VERSION
    assert observation.kind is kind
    assert isinstance(observation.payload, payload_type)
    body = observation.to_dict()
    assert set(body) == {
        "schema_version",
        "kind",
        "payload",
        "observation_digest",
    }
    digest_body = {key: body[key] for key in ("schema_version", "kind", "payload")}
    encoded = json.dumps(
        digest_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert body["observation_digest"] == f"sha256:{sha256(encoded).hexdigest()}"

    parsed = RuntimeHookObservationV1.from_dict(body)

    assert parsed.to_dict() == body
    assert isinstance(parsed.payload, payload_type)


def test_runtime_hook_observation_is_deeply_immutable_and_serializes_fresh_json() -> None:
    observation = _ablation_observation()
    payload = observation.payload
    assert isinstance(payload, ExperimentAblationGateAppliedPayloadV1)
    assert isinstance(payload.hook_input, MappingProxyType)
    assert payload.hook_input["required_child_unit_ids"] == (
        "child-1",
        "child-2",
    )
    with pytest.raises(TypeError):
        payload.hook_input["gate_satisfied"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        payload.artifact_refs[0].source["kind"] = "mutated"

    first = observation.to_dict()
    first["payload"]["hook_input"]["required_child_unit_ids"].append("child-3")
    first["payload"]["artifact_refs"][0]["source"]["nested"]["sample"].append(3)

    second = observation.to_dict()
    assert second["payload"]["hook_input"]["required_child_unit_ids"] == [
        "child-1",
        "child-2",
    ]
    assert second["payload"]["artifact_refs"][0]["source"]["nested"]["sample"] == [
        1,
        2,
    ]


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (lambda body: body.update(extra=True), "exact keys"),
        (lambda body: body.update(schema_version="unknown.v1"), "schema_version"),
        (lambda body: body.update(kind="EXPERIMENT_UNKNOWN"), "kind"),
        (
            lambda body: body.update(observation_digest="sha256:" + "0" * 64),
            "observation_digest mismatch",
        ),
        (lambda body: body["payload"].update(extra=True), "exact keys"),
    ),
)
def test_runtime_hook_observation_from_dict_rejects_unknown_or_mutated_envelope(
    mutator,
    message,
) -> None:
    body = _fault_observation().to_dict()
    mutator(body)

    with pytest.raises((TypeError, ValueError), match=message):
        RuntimeHookObservationV1.from_dict(body)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda body: body["payload"]["protocol_event_refs"][0].update(extra=True),
        lambda body: body["payload"]["protocol_event_refs"][0].pop("event_type"),
        lambda body: body["payload"]["hook_result"].update(extra=True),
        lambda body: body["payload"]["hook_input"].update(extra=True),
    ),
)
def test_runtime_hook_observation_rejects_unknown_nested_shapes(mutator) -> None:
    body = _ablation_observation().to_dict()
    mutator(body)
    body["observation_digest"] = _digest_for(body)

    with pytest.raises((TypeError, ValueError), match="exact keys"):
        RuntimeHookObservationV1.from_dict(body)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda body: body["payload"]["protocol_event_refs"][0].update(event_seq=True),
        lambda body: body["payload"]["artifact_refs"][0].update(size_bytes=True),
        lambda body: body["payload"]["artifact_refs"][0]["metadata"].update(
            score=float("nan")
        ),
        lambda body: body["payload"].update(disabled_mechanism=""),
    ),
)
def test_runtime_hook_observation_rejects_invalid_scalar_types(mutator) -> None:
    body = _ablation_observation().to_dict()
    mutator(body)
    body["observation_digest"] = _digest_for(body)

    with pytest.raises((TypeError, ValueError)):
        RuntimeHookObservationV1.from_dict(body)


def test_runtime_hook_observation_strictly_validates_artifacts_before_construction() -> None:
    invalid_ref = replace(_artifact_ref(), size_bytes=True)

    with pytest.raises((TypeError, ValueError), match="size_bytes"):
        build_experiment_fault_injected_observation(
            condition_id="condition-1",
            run_id="run-1",
            task_id="task-1",
            unit_id="unit-1",
            selected_target_ai_unit_id="planned-unit-1",
            attempt_id="attempt-1",
            fault_type="no_return",
            protocol_event_refs=(),
            artifact_refs=(invalid_ref,),
            occurred_at=NOW,
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda records: RawOutputDirective(experiment_records=records),
        lambda records: ParsedCandidateDirective(
            replacement_candidate_output_refs={},
            experiment_records=records,
        ),
        lambda records: GateDirective(experiment_records=records),
        lambda records: WorkerDirective(action="continue", experiment_records=records),
    ),
)
def test_runtime_directives_accept_only_typed_observation_tuples(factory) -> None:
    observation = _fault_observation()

    assert factory((observation,)).experiment_records == (observation,)
    with pytest.raises(TypeError, match="RuntimeHookObservationV1"):
        factory((observation.to_dict(),))


def test_coordinator_observation_collector_has_no_mapping_fallback() -> None:
    observation = _fault_observation()
    observations: list[RuntimeHookObservationV1] = []

    _collect_observations(
        observations,
        RawOutputDirective(experiment_records=(observation,)),
    )

    assert observations == [observation]

    class LegacyDirective:
        experiment_records = (observation.to_dict(),)

    with pytest.raises(TypeError, match="RuntimeHookObservationV1"):
        _collect_observations(observations, LegacyDirective())


def _digest_for(body: dict) -> str:
    digest_body = {key: body[key] for key in ("schema_version", "kind", "payload")}
    encoded = json.dumps(
        digest_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
