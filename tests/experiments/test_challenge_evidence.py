"""Challenge evidence joins must distinguish exposure from protocol termination."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tokenshare.experiments.projector import RootProjectionError, _challenge_projection
from tokenshare.experiments.scenarios import ModeBlindChallengeController, ResolvedChallengePlanV1
from tokenshare.experiments.schema import ChallengeObservationV1, SchemaValidationError
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventType


FAMILIES = {
    "PARSER_REQUIRED_CANONICAL_JSON": "before_plugin_parser",
    "INVALID_PARSED_CANDIDATE": "after_parser_before_verification",
    "RECOVERABLE_NO_RETURN": "after_source_usage",
    "REQUIRED_CHILD_DELAY": "after_source_usage",
}


class Evidence:
    def __init__(self, tmp_path, family, domain="factorization"):
        self.store = ArtifactStore(tmp_path)
        self.plan = ResolvedChallengePlanV1(
            challenge_plan_id="plan", case_id="case", repeat_id=0,
            challenge_family=family, target_planned_ai_unit_ids=("range_0", "range_1"),
            attempt_rule="every_attempt", injection_boundary=FAMILIES[family],
        )
        self.controller = ModeBlindChallengeController(
            challenge_plan=self.plan, domain=domain, artifact_store=self.store,
        )
        self.events = []
        self.attempts = []
        self.requests = {}
        self.domain = domain
        for index in range(2):
            self.events.append(SimpleNamespace(
                event_type=EventType.TASK_UNIT_CREATED, task_id="task",
                payload={"task_unit": {
                    "unit_id": f"unit-{index}", "unit_type": "child",
                    "plugin_payload": {"summary": {"child_index": index}},
                    "metadata": {"child_logical_key": f"range_{index}"},
                }},
            ))

    def artifact(self, body):
        return self.store.save_json(
            body, artifact_id=f"evidence-{len(self.events)}",
            artifact_type="test", artifact_schema_id="test", artifact_schema_version="1",
            source={}, metadata={}, created_at="2026-09-05T00:00:00Z",
        ).to_dict()

    def request(self, index=0, ordinal=0, *, completed=False, record=False):
        attempt_id = f"attempt-{index}-{ordinal}"
        request = dict(
            request_id=f"request-{index}-{ordinal}", task_id="task",
            unit_id=f"unit-{index}", attempt_id=attempt_id,
            soft_hints={"planned_ai_unit_id": f"range_{index}"}, attempt_ordinal=ordinal,
        )
        self.requests[attempt_id] = request
        self.events.append(SimpleNamespace(
            event_type=EventType.EXECUTION_REQUEST_RECORDED, task_id="task",
            payload={**request, "request_ref": self.artifact(request)},
        ))
        if completed:
            self.attempts.append(SimpleNamespace(
                attempt_id=attempt_id, unit_id=request["unit_id"],
                planned_ai_unit_id=f"range_{index}", attempt_ordinal=ordinal,
            ))
        if record:
            self.controller._record(
                request=SimpleNamespace(**request), boundary=self.plan.injection_boundary,
            )
        return attempt_id

    def submission(self, attempt_id, records):
        request = self.requests[attempt_id]
        document = {
            **request, "result_kind": "success",
            "environment_summary": {"experiments_challenge_observations": records},
        }
        self.events.append(SimpleNamespace(
            event_type=EventType.EXECUTION_SUBMISSION_RECORDED, task_id="task",
            payload={**request, "result_kind": "success", "submission_ref": self.artifact(document)},
        ))

    def project(self):
        return _challenge_projection(
            scenario=SimpleNamespace(challenge_plan=self.plan, challenge_controller=self.controller),
            assembly=SimpleNamespace(artifact_store=self.store), events=self.events,
            verification_status={}, canonical_attempt_ids=set(), recoveries=(),
            verified_correct=False, domain=self.domain,
            planned_ai_unit_ids=("range_0", "range_1"), attempts=self.attempts,
        )


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("domain", ["factorization", "lean"])
def test_unrequested_targets_are_unexposed_independent_of_outcome(tmp_path, family, domain):
    observations, reasons = Evidence(tmp_path, family, domain).project()
    assert observations == []
    assert reasons["challenge_observations"] == "challenge_target_not_dispatched"


@pytest.mark.parametrize("family", FAMILIES)
def test_prepared_request_is_not_proof_of_injection_boundary(tmp_path, family):
    evidence = Evidence(tmp_path, family)
    evidence.request()
    observations, reasons = evidence.project()
    assert observations == []
    assert reasons["challenge_observations"] == "challenge_boundary_not_observed"


@pytest.mark.parametrize("family", FAMILIES)
def test_partial_exposure_retains_actual_record_and_unreached_target(tmp_path, family):
    evidence = Evidence(tmp_path, family)
    evidence.request(completed=True, record=True)
    observations, reasons = evidence.project()
    assert len(observations) == 1
    assert observations[0].injected is True
    assert reasons["challenge_targets.range_1"] == "challenge_target_not_dispatched"
    assert "challenge_observations" not in reasons


@pytest.mark.parametrize("gap", ["another_target", "retry", "persisted_submission"])
def test_one_record_cannot_hide_another_completed_attempt_missing_record(tmp_path, gap):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    evidence.request(completed=True, record=True)
    if gap == "another_target":
        evidence.request(index=1, completed=True)
    elif gap == "retry":
        evidence.request(ordinal=1, completed=True)
    else:
        attempt_id = evidence.request(index=1)
        evidence.submission(attempt_id, [])
    with pytest.raises(RootProjectionError, match="lacks persisted actual injection evidence"):
        evidence.project()


def test_ordinal_zero_rule_does_not_require_retry_injection(tmp_path):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    evidence.plan = replace(evidence.plan, attempt_rule="ordinal_0")
    evidence.request(completed=True, record=True)
    evidence.request(ordinal=1, completed=True)
    assert len(evidence.project()[0]) == 1


@pytest.mark.parametrize("completion", ["inflight", "persisted_only", "duplicate_copy"])
def test_actual_record_survives_without_completed_adapter_or_is_deduplicated(tmp_path, completion):
    evidence = Evidence(tmp_path, "PARSER_REQUIRED_CANONICAL_JSON")
    attempt_id = evidence.request(record=True)
    if completion in {"persisted_only", "duplicate_copy"}:
        evidence.submission(attempt_id, list(evidence.controller.injection_records))
    if completion == "persisted_only":
        evidence.controller.injection_records.clear()
    observations, _ = evidence.project()
    assert len(observations) == 1
    assert observations[0].injected is True
    assert observations[0].reached_verification is False


@pytest.mark.parametrize("field,value", [
    ("kind", "UNKNOWN"), ("kind", "INVALID_PARSED_CANDIDATE"),
    ("planned_ai_unit_id", "foreign"), ("attempt_id", "foreign"),
    ("attempt_ordinal", 1), ("attempt_ordinal", True), ("attempt_ordinal", -1),
    ("boundary", "wrong_boundary"), ("opportunity", "true"), ("injected", 1),
    ("opportunity", False), ("source_semantics_preserved", "true"),
    ("independently_wrong", "false"),
])
def test_foreign_or_malformed_records_are_rejected(tmp_path, field, value):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    evidence.request(completed=True, record=True)
    evidence.controller.injection_records[0][field] = value
    with pytest.raises(RootProjectionError):
        evidence.project()


def test_conflicting_memory_and_persisted_records_are_rejected(tmp_path):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    attempt_id = evidence.request(completed=True, record=True)
    record = {**evidence.controller.injection_records[0], "source_semantics_preserved": True}
    evidence.submission(attempt_id, [record])
    with pytest.raises(RootProjectionError, match="conflict"):
        evidence.project()


@pytest.mark.parametrize("kind", ["request", "submission"])
def test_evidence_artifact_hash_is_verified(tmp_path, kind):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    attempt_id = evidence.request(completed=True, record=True)
    if kind == "submission":
        evidence.submission(attempt_id, list(evidence.controller.injection_records))
    ref = evidence.events[-1].payload[f"{kind}_ref"]
    (evidence.store.root_path / ref["uri"]).write_text("{}", encoding="utf-8")
    with pytest.raises(RootProjectionError, match="challenge .* evidence"):
        evidence.project()


def test_submission_cannot_carry_another_attempts_record(tmp_path):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    evidence.request(completed=True, record=True)
    attempt_id = evidence.request(index=1)
    evidence.submission(attempt_id, list(evidence.controller.injection_records))
    with pytest.raises(RootProjectionError):
        evidence.project()


def test_memory_record_cannot_mask_missing_persisted_record(tmp_path):
    evidence = Evidence(tmp_path, "REQUIRED_CHILD_DELAY")
    attempt_id = evidence.request(completed=True, record=True)
    evidence.submission(attempt_id, [])
    with pytest.raises(RootProjectionError, match="lacks persisted actual injection evidence"):
        evidence.project()


@pytest.mark.parametrize("content", [None, "model returned non-JSON"])
def test_parser_challenge_records_explicit_no_op_for_unavailable_source(tmp_path, content):
    evidence = Evidence(tmp_path, "PARSER_REQUIRED_CANONICAL_JSON")
    attempt_id = evidence.request()
    request = SimpleNamespace(**evidence.requests[attempt_id])
    assert evidence.controller.transform_content(request, content) == content
    assert evidence.controller.injection_records[0]["opportunity"] is False
    assert evidence.controller.injection_records[0]["injected"] is False


def test_controller_rejects_conflicting_repeat_at_same_boundary(tmp_path):
    evidence = Evidence(tmp_path, "PARSER_REQUIRED_CANONICAL_JSON")
    attempt_id = evidence.request(record=True)
    with pytest.raises(ValueError, match="conflict"):
        evidence.controller._record(
            request=SimpleNamespace(**evidence.requests[attempt_id]),
            boundary=evidence.plan.injection_boundary, opportunity=False, injected=False,
        )


def test_schema_rejects_injection_without_opportunity():
    observation = ChallengeObservationV1(
        challenge_plan_id="plan", challenge_family="REQUIRED_CHILD_DELAY",
        target_planned_ai_unit_id="range_0", attempt_ordinal=0,
        injection_boundary="after_source_usage", opportunity=False, injected=True,
    )
    with pytest.raises(SchemaValidationError):
        observation.validate()


@pytest.mark.parametrize("change", ["plan", "family", "target", "ordinal", "boundary", "rule", "duplicate"])
def test_schema_rejects_root_plan_mismatch_and_duplicate_observations(change):
    from tests.experiments.test_reducer_golden import _inventory, _result

    row = _result(_inventory("exp4", "condition", "case", mode="FULL", challenge_plan_id="plan"))
    observation = row.challenge_observations[0]
    if change == "duplicate":
        row.challenge_observations.append(replace(observation))
    elif change == "rule":
        row.challenge_attempt_ordinal_rule = "unknown"
    else:
        field, value = {
            "plan": ("challenge_plan_id", "foreign"),
            "family": ("challenge_family", "foreign"),
            "target": ("target_planned_ai_unit_id", "foreign"),
            "ordinal": ("attempt_ordinal", 1),
            "boundary": ("injection_boundary", "unknown"),
        }[change]
        setattr(observation, field, value)
    with pytest.raises(SchemaValidationError):
        row.validate()
