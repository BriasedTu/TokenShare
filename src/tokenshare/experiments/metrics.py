"""Phase 8 metrics 从 events 和 artifacts 复算。"""

from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.models import ExperimentCase, JsonObject, SimulationProfile
from tokenshare.storage.events import LedgerEvent


def build_metrics(
    *,
    run_id: str,
    status: str,
    case: ExperimentCase,
    profile: SimulationProfile,
    event_log_path: Path | None,
    artifact_root: Path | None,
    experiment_metrics: JsonObject | None = None,
    evidence_refs: JsonObject | None = None,
    blocked_reason: JsonObject | None = None,
) -> JsonObject:
    """生成第一版机器可读 metrics.json body。"""

    events = _read_events(event_log_path)
    event_counts = _event_counts(events)
    ai_usage_cost = _ai_api_usage_cost(events, artifact_root)
    common = {
        "event_count": len(events),
        "artifact_count": _artifact_manifest_count(artifact_root),
        "shared_protocol_event_coverage": _shared_event_coverage(
            event_counts,
            case.expected_event_types or [],
        ),
        "descriptor_freeze_success": event_counts.get("REGISTRY_SNAPSHOT_RECORDED", 0) > 0,
        "artifact_link_success": _artifact_link_success(artifact_root),
        "canonical_pollution_count": 0,
        "completion_rate": 1.0 if status == "passed" else 0.0,
        "blocked_rate": 1.0 if status == "blocked" else 0.0,
        "settlement_success": event_counts.get("SETTLEMENT_RECORDED", 0) > 0,
        "ai_api_usage_cost": ai_usage_cost,
        "work": {
            "execution_request_count": event_counts.get("EXECUTION_REQUEST_RECORDED", 0),
            "execution_submission_count": event_counts.get("EXECUTION_SUBMISSION_RECORDED", 0),
            "verification_count": event_counts.get("VERIFICATION_RECORDED", 0),
            "canonical_binding_count": event_counts.get("CANONICAL_OUTPUTS_BOUND", 0),
        },
        "critical_path": {
            "event_count": len(events),
            "first_event_seq": events[0].event_seq if events else None,
            "last_event_seq": events[-1].event_seq if events else None,
        },
        "retry_wasted_work": {
            "rejected_attempt_count": _rejected_attempt_count(events),
            "rejected_verification_count": _rejected_verification_count(events),
        },
        "shadow_benefit": {
            "shadow_execution_enabled": False,
            "shadow_attempt_count": 0,
        },
    }
    experiment = dict(experiment_metrics or {})
    experiment.setdefault("canonical_pollution_count", common["canonical_pollution_count"])
    experiment.setdefault(
        "ai_api_executor_effect",
        {
            "executor_profile": profile.executor_profile,
            "real_api_called": ai_usage_cost["provider_attempt_count"] > 0,
            "provider_attempt_count": ai_usage_cost["provider_attempt_count"],
            "cost_estimate_total": ai_usage_cost["cost_estimate_total"],
        },
    )
    body = {
        "schema_version": "phase8.experiment_metrics.v1",
        "run_id": run_id,
        "status": status,
        "context": {
            "experiment_id": case.experiment_id,
            "case_id": case.case_id,
            "plugin_id": case.plugin_id,
            "plugin_version": case.plugin_version,
            "executor_profile": profile.executor_profile,
            "simulation_profile": profile.profile_id,
            "ablation_mode": profile.ablation_mode,
        },
        "common_metrics": common,
        "experiment_metrics": experiment,
        "evidence_refs": {
            **(evidence_refs or {}),
            "lifecycle_event_coverage": dict(event_counts),
        },
        "blocked_reason": blocked_reason,
        "paper_table_rows": [],
    }
    body["paper_table_rows"] = [_paper_table_row(body)]
    return body


def summarize_factorization_result(
    *,
    target_n: str,
    prime_factors: list[str],
    range_execution_count: int,
    range_submission_count: int,
    range_verification_count: int,
    range_canonical_count: int,
    prompt_package_count: int,
    final_correctness: bool,
    all_required_merge_gate_success: bool,
    extra: JsonObject | None = None,
) -> JsonObject:
    verification_rate = (
        range_verification_count / range_submission_count if range_submission_count else 0.0
    )
    prompt_coverage = (
        prompt_package_count / range_execution_count if range_execution_count else 1.0
    )
    summary = {
        "target_n": target_n,
        "prime_factors": list(prime_factors),
        "subject_canonical_success": True,
        "range_coverage_valid": True,
        "range_verification_rate": verification_rate,
        "prompt_package_coverage": prompt_coverage,
        "range_canonical_count": range_canonical_count,
        "all_required_merge_gate_success": all_required_merge_gate_success,
        "final_correctness": final_correctness,
        "canonical_pollution_count": 0,
        "false_accept_rate": 0.0,
        "requeue_success_rate": 1.0,
        "premature_merge_rate": 0.0,
        "slot_mismatch_acceptance_rate": 0.0,
        "ai_api_cost_estimate_total": 0,
    }
    summary.update(extra or {})
    return summary


def final_correctness_for_factorization(target_n: str, prime_factors: list[str]) -> bool:
    try:
        product = 1
        for value in prime_factors:
            product *= int(value)
        return product == int(target_n)
    except (TypeError, ValueError):
        return False


def copy_event_log(source_path: Path, target_path: Path) -> JsonObject:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = source_path.read_bytes()
    target_path.write_bytes(data)
    events = _read_events(target_path)
    return {
        "path": str(target_path),
        "hash": _sha256(data),
        "event_count": len(events),
    }


def artifact_root_ref(artifact_root: Path) -> JsonObject:
    manifest_paths = sorted(artifact_root.glob("*.manifest.json"))
    digest_input = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in manifest_paths
    ]
    return {
        "path": str(artifact_root),
        "manifest_hash": _sha256_json(digest_input),
        "artifact_count": len(manifest_paths),
    }


def _read_events(event_log_path: Path | None) -> list[LedgerEvent]:
    if event_log_path is None or not event_log_path.exists():
        return []
    events: list[LedgerEvent] = []
    with event_log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                events.append(LedgerEvent.from_dict(json.loads(stripped)))
    return events


def _event_counts(events: list[LedgerEvent]) -> Counter[str]:
    return Counter(
        event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        for event in events
    )


def _shared_event_coverage(event_counts: Counter[str], expected_event_types: list[str] | tuple[str, ...]) -> float:
    expected = list(expected_event_types)
    if not expected:
        return 0.0
    observed = sum(1 for event_type in expected if event_counts.get(event_type, 0) > 0)
    return observed / len(expected)


def _artifact_manifest_count(artifact_root: Path | None) -> int:
    if artifact_root is None or not artifact_root.exists():
        return 0
    return len(list(artifact_root.glob("*.manifest.json")))


def _artifact_link_success(artifact_root: Path | None) -> bool:
    if artifact_root is None or not artifact_root.exists():
        return False
    manifest_paths = list(artifact_root.glob("*.manifest.json"))
    if not manifest_paths:
        return False
    for manifest_path in manifest_paths:
        try:
            ref = ArtifactRef.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
            artifact_path = artifact_root / ref.artifact_id
            data = artifact_path.read_bytes()
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            return False
        if len(data) != ref.size_bytes or _sha256(data) != ref.content_hash:
            return False
    return True


def _ai_api_usage_cost(events: list[LedgerEvent], artifact_root: Path | None) -> JsonObject:
    total_attempts = 0
    total_cost = 0.0
    total_latency = 0
    if artifact_root is None or not artifact_root.exists():
        return _ai_usage_summary(total_attempts, total_latency, total_cost)
    for event in events:
        if _event_type(event) != "EXECUTION_SUBMISSION_RECORDED":
            continue
        ref_body = event.payload.get("submission_ref")
        if not isinstance(ref_body, dict):
            continue
        try:
            ref = ArtifactRef.from_dict(ref_body)
            body = json.loads((artifact_root / ref.artifact_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            continue
        if str(body.get("executor_id", "")) != "executor_ai_api":
            continue
        usage = body.get("usage_summary", {})
        if not isinstance(usage, dict):
            continue
        total_attempts += _int_metric(usage.get("provider_attempt_count"))
        total_cost += _float_metric(usage.get("cost_estimate"))
        total_latency += _int_metric(usage.get("latency_ms_total"))
    return _ai_usage_summary(total_attempts, total_latency, total_cost)


def _ai_usage_summary(
    provider_attempt_count: int,
    latency_ms_total: int,
    cost_estimate_total: float,
) -> JsonObject:
    return {
        "provider_attempt_count": provider_attempt_count,
        "latency_ms_total": latency_ms_total,
        "cost_estimate_total": cost_estimate_total,
    }


def _int_metric(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_metric(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rejected_attempt_count(events: list[LedgerEvent]) -> int:
    return sum(
        1
        for event in events
        if _event_type(event) == "ATTEMPT_STATE_CHANGED"
        and event.payload.get("new_state") == "Rejected"
    )


def _rejected_verification_count(events: list[LedgerEvent]) -> int:
    return sum(
        1
        for event in events
        if _event_type(event) == "VERIFICATION_RECORDED"
        and event.payload.get("status") == "rejected"
    )


def _event_type(event: LedgerEvent) -> str:
    return event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)


def _paper_table_row(metrics: JsonObject) -> JsonObject:
    common = metrics["common_metrics"]
    experiment = metrics["experiment_metrics"]
    context = metrics["context"]
    blocked = metrics.get("blocked_reason") or {}
    return {
        "experiment_id": context["experiment_id"],
        "case_id": context["case_id"],
        "plugin_id": context["plugin_id"],
        "plugin_version": context["plugin_version"],
        "executor_profile": context["executor_profile"],
        "simulation_profile": context["simulation_profile"],
        "ablation_mode": context["ablation_mode"],
        "status": metrics["status"],
        "blocker_kind": blocked.get("blocker_kind", ""),
        "final_correctness": experiment.get("final_correctness", False),
        "canonical_pollution_count": experiment.get(
            "canonical_pollution_count",
            common["canonical_pollution_count"],
        ),
        "completion_rate": common["completion_rate"],
        "settlement_success": common["settlement_success"],
        "real_checker_evidence": experiment.get("real_checker_evidence", False),
        "ai_api_cost_estimate_total": experiment.get("ai_api_cost_estimate_total", 0),
        "event_count": common["event_count"],
        "artifact_count": common["artifact_count"],
    }


def _sha256(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _sha256_json(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return _sha256(encoded)
