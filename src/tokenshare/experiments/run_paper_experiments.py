"""CLI entrypoint for paper real-AI experiment planning and gated execution."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter_ns

from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibDeepSeekTransport,
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.experiments.paper_budget import (
    EXP1_PILOT_EXPERIMENT_ID,
    Exp1PilotProfile,
    PaperBudgetApprovalError,
    build_exp5_v3_token_ceiling_mapping,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
    load_paper_catalogs,
)
from tokenshare.experiments.paper_catalog_execution_view import (
    EXP3_VIEW_KIND,
    build_prepared_mapping_execution_view,
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_resource_accounting import FrozenPricing
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_exp5_smoke_evidence import (
    EXP5_SMOKE_SUITE_ID,
    Exp5SmokeEvidenceError,
    load_exp5_smoke_evidence_bundle,
    write_exp5_smoke_evidence_bundle,
)
from tokenshare.experiments.paper_models import (
    PAPER_FORMAL_AI_TIMEOUT_SECONDS,
    PaperBudgetResult,
    PaperStatus,
    PaperSuiteResult,
    VersionedPaperEvidenceEligibilityReport,
    digest_json,
)
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
    PaperInfrastructureBlockedError,
    execute_paper_formal_suite,
    recompute_paper_formal_metrics_from_runner_inputs as recompute_paper_formal_metrics,
    replay_paper_formal_suite,
    write_paper_formal_replay_report,
)
from tokenshare.experiments.paper_exp1 import EXP1_FORMAL_REQUEST_CONTROLS
from tokenshare.experiments.paper_exp2_scalability import (
    build_exp2_regression_smoke_factor_binding,
    build_exp2_regression_smoke_lean_bindings,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    build_exp3_regression_smoke_binding,
)
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_formal_gate import (
    CompleteFullBankPrerequisite,
    PaperGateAuthorityDigests,
    PaperGateLevelAttestation,
    PaperGatePrerequisiteEnvelope,
    PaperGateSelectionEnvelope,
    PaperGateTerminalEnvelope,
    PaidAuthorizationAuthorityCommitment,
    SelectedPaidAuthorizationBinding,
    SelectedEvidenceRoute,
    SelectedFormalMetricsAudit,
    SelectedTerminalEvidence,
    build_paid_authorization_authority_commitment,
    load_exp2_post_bank_publication_inputs,
    selected_formal_metrics_audit_digest,
)
from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_formal_report import (
    generate_paper_formal_report,
)
from tokenshare.experiments.paper_runner import (
    build_gate_c_dispatch_plans,
    build_lean_3x3_matrix_plan,
    execute_gate_c_pilot_case,
    execute_exp1_pilot,
    expand_plan_conditions,
    normalize_experiment_ids,
    validate_experiment_dependency_order,
    validate_gate_c_pilot_output_root,
    validate_exp1_selector_output_root,
)
from tokenshare.experiments.paper_smoke import (
    build_paper_smoke_service_authority,
    execute_paper_smoke_suite,
    load_paper_smoke_profile,
    replay_paper_smoke_suite,
    resolve_paper_smoke_execution_plan,
)
from tokenshare.experiments.paper_suite_scale import (
    load_paper_suite_scale_profile,
)
from tokenshare.runtime_paths import default_data_root, resolve_experiment_output_root
from tokenshare.experiments.run_paper_pipeline import (
    PAPER_PIPELINE_COMMANDS,
    main as run_paper_pipeline_main,
)


DEFAULT_FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v2.jsonl")
DEFAULT_EXP1_PILOT_FACTOR_CATALOG = Path(
    "benchmarks/paper/factorization_catalog.v1.jsonl"
)
DEFAULT_LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
DEFAULT_LEAN_LEMMA_GRAPH_CATALOG = Path(
    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)
DEFAULT_EXP1_PILOT_PROFILE = Path(
    "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
)
DEFAULT_PAPER_SUITE_SCALE_PROFILE = Path(
    "benchmarks/paper/paper_suite_scale_profile.v1.json"
)
EXP1_EXP4_ONLY_EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
EXP3_EXP4_ONLY_EXPERIMENT_IDS = (
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG = Path(
    "benchmarks/paper/exp1_baseline_provider_config.v3.json"
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_DIGEST = (
    "sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923"
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID = "exp1_baseline_deepseek"
EXP1_EXP4_REQUIRED_ENTRY_ID = "deepseek_v4_pro_exp1_baseline"
FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT = 304_096
FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT = 0.05
EXP5_V3_EXECUTION_SCHEDULE_PATH = Path(
    "audit/exp5_v3_execution_schedule.json"
)
DEFAULT_EXP5_MODEL_COHORT = Path(
    "benchmarks/paper/model_comparison_cohort.v3.json"
)
DEFAULT_EXP5_MODEL_ENTRY_MAP = Path(
    "benchmarks/paper/model_comparison_entry_map.v3.json"
)
DEFAULT_EXP5_PROVIDER_CONFIG = Path(
    "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
)
MATRIX8_SMOKE_PROFILE_PATHS = tuple(
    Path(f"benchmarks/paper/paper_smoke_exp{number}_matrix8_profile.v1.json")
    for number in range(1, 5)
)


@dataclass(frozen=True, kw_only=True)
class EPD027FormalServiceAuthority:
    """pipeline 与 legacy 可共同消费的无 secret、无 provider side effect authority。"""

    command: str
    plan_digest: str
    inventory_digest: str
    budget_digest: str
    keyword_arguments: Mapping[str, object]
    gate_selection: PaperGateSelectionEnvelope | None = None
    gate_prerequisites: PaperGatePrerequisiteEnvelope | None = None
    publication_gate_factory: object | None = None


@dataclass(frozen=True, kw_only=True)
class Matrix8SmokePlanningContext:
    catalog_manifest: PaperInputCatalogManifest
    profiles: tuple[object, ...]
    execution_plans: tuple[object, ...]
    ai_api_config: object


@dataclass(frozen=True, kw_only=True)
class EPD027FormalPublicationGateFactory:
    """把 official runner terminal 绑定回同一 gate authority；缺 closure 时 fail closed。"""

    selection: PaperGateSelectionEnvelope
    prerequisites: PaperGatePrerequisiteEnvelope
    output_root: Path
    contract: PaperMetricContract
    l3_online_check_root: Path | None = None

    def __call__(self, terminal_result: object) -> PaperGateTerminalEnvelope:
        if not isinstance(terminal_result, PaperSuiteResult):
            raise TypeError("formal publication factory requires PaperSuiteResult")
        if Path(terminal_result.output_root).resolve(strict=False) != self.output_root:
            raise ValueError("formal publication terminal output root mismatch")
        try:
            return self._from_official_closure(terminal_result)
        except (
            FileExistsError,
            FileNotFoundError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            # 缺任何正式 closure 都保留为 typed 空 terminal，由 publication gate 给出原因。
            return self._blocked_terminal()

    def _blocked_terminal(self) -> PaperGateTerminalEnvelope:
        return PaperGateTerminalEnvelope(
            experiment_evidence=(),
            l1_attestation=self.prerequisites.l1_attestation,
            l2_attestation=self.prerequisites.l2_attestation,
            l3_attestation=None,
            l4_attestation=None,
            replay_results=(),
            paid_authorizations=self.prerequisites.paid_authorizations,
            full_bank=self.prerequisites.full_bank,
        )

    def _from_official_closure(
        self,
        terminal_result: PaperSuiteResult,
    ) -> PaperGateTerminalEnvelope:
        from tokenshare.experiments import paper_formal_evidence
        from tokenshare.experiments.paper_formal_metrics import (
            recompute_paper_formal_metrics as recompute_selected_metrics,
        )
        from tokenshare.experiments.paper_formal_runner import (
            load_paper_traceability_replay_input_root,
            load_paper_traceability_replay_inputs,
            recompute_paper_traceability_replay,
        )
        from tokenshare.experiments.paper_formal_report import FormalReportResult

        terminal_status = getattr(
            terminal_result.status, "value", terminal_result.status
        )
        if terminal_status not in {"completed", "completed_with_failures"}:
            return self._blocked_terminal()
        expected_long_ids = {
            "exp1": "exp1_real_ai_feasibility",
            "exp2": "exp2_real_ai_scalability",
            "exp3": "exp3_real_ai_fault_recovery",
            "exp4": "exp4_real_ai_protocol_ablation",
            "exp5": "exp5_real_ai_model_endpoint_comparison",
        }
        if set(terminal_result.experiment_ids) != {
            expected_long_ids[value] for value in self.selection.selected_experiments
        }:
            return self._blocked_terminal()
        route_specs = {
            "exp1": (
                ("online", "primary", "online_real_provider", "exp1_feasibility", ("exp1_feasibility",)),
            ),
            "exp2": (
                ("trace-main", "primary", "real_model_trace_protocol_run", "exp2_trace_scalability", ("exp2_trace_scalability",)),
                ("online-support", "support", "online_real_provider", "exp2_online_concurrency", ("exp2_online_concurrency",)),
            ),
            "exp3": (
                ("trace-main", "primary", "real_model_trace_protocol_run", "exp3_trace_robustness", ("exp3_trace_robustness",)),
                ("online-support", "support", "online_real_provider", "exp3_online_recovery", ("exp3_online_recovery",)),
            ),
            "exp4": (
                ("trace", "primary", "real_model_trace_protocol_run", "exp4_ablation", ("exp4_ablation",)),
            ),
            "exp5": (
                ("online", "primary", "online_real_provider", "experiment_5", ("exp5_quality", "exp5_resources")),
            ),
        }
        needs_support = any(
            source == "support"
            for experiment in self.selection.selected_experiments
            for _route, source, _evidence, _key, _tables in route_specs[experiment]
        )
        if needs_support and self.l3_online_check_root is None:
            return self._blocked_terminal()
        roots_by_source = {"primary": self.output_root}
        if needs_support:
            assert self.l3_online_check_root is not None
            roots_by_source["support"] = self.l3_online_check_root

        closures: dict[str, dict[str, object]] = {}
        for source, root in roots_by_source.items():
            protected = load_paper_traceability_replay_input_root(root)
            loaded = load_paper_traceability_replay_inputs(root)
            direct = getattr(loaded, "direct", None)
            current = getattr(loaded, "current", None)
            source_inputs = getattr(loaded, "source", None)
            current_files = getattr(loaded, "current_evidence_files", None)
            if (
                not isinstance(direct, Mapping)
                or not isinstance(current, Mapping)
                or not isinstance(source_inputs, Mapping)
                or not isinstance(current_files, Sequence)
            ):
                return self._blocked_terminal()
            metrics = []
            for ordinal in (1, 2):
                metrics_root = (
                    self.output_root
                    / "publication_gate"
                    / source
                    / f"selected_metrics_{ordinal}"
                )
                if metrics_root.exists() and any(metrics_root.iterdir()):
                    raise FileExistsError(
                        f"selected metrics root is not fresh: {metrics_root}"
                    )
                for relative_path, content in current_files:
                    target = metrics_root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                metrics.append(
                    recompute_selected_metrics(
                        metrics_root,
                        direct,
                        global_infrastructure_valid=bool(
                            current["global_infrastructure_valid"]
                        ),
                        contract=self.contract,
                        canonical_runtime_evidence=current[
                            "canonical_runtime_evidence"
                        ],
                        requested_lineage_root_ids=current[
                            "requested_lineage_root_ids"
                        ],
                        current_trace_wrappers_by_root=current[
                            "current_trace_wrappers_by_root"
                        ],
                        trace_source_bindings_by_root=source_inputs[
                            "trace_source_bindings_by_root"
                        ],
                        eligibility_facts_by_root=current[
                            "eligibility_facts_by_root"
                        ],
                    )
                )
            closures[source] = {
                "protected": protected,
                "loaded": loaded,
                "direct": direct,
                "current": current,
                "metrics": tuple(metrics),
                "metrics_root": (
                    self.output_root / "publication_gate" / source
                ),
            }

        p0_full = set(self.selection.selected_experiments) == {
            "exp1",
            "exp2",
            "exp3",
            "exp4",
            "exp5",
        }
        replay_results = (
            tuple(
                recompute_paper_traceability_replay(
                    output_root=(
                        self.output_root
                        / "publication_gate"
                        / f"l4_replay_{ordinal}"
                    ),
                    replay_input_root=closures["primary"]["protected"],
                    contract=self.contract,
                )
                for ordinal in (1, 2)
            )
            if p0_full
            else ()
        )
        if p0_full and (
            len(replay_results) != 2
            or any(
                replay.provider_calls != 0
                or replay.source_write_count != 0
                or replay.online_fill_count != 0
                or not isinstance(replay.report, FormalReportResult)
                for replay in replay_results
            )
        ):
            return self._blocked_terminal()
        report = replay_results[0].report if replay_results else None
        experiment_evidence = []
        for experiment in self.selection.selected_experiments:
            routes = []
            for route_id, source, evidence_class, direct_key, table_ids in route_specs[
                experiment
            ]:
                closure = closures[source]
                direct = closure["direct"]
                current = closure["current"]
                route_rows = direct.get(direct_key, ())
                root_ids = tuple(dict.fromkeys(_epd027_direct_root_ids(route_rows)))
                if not root_ids:
                    return self._blocked_terminal()
                if evidence_class == "real_model_trace_protocol_run":
                    facts_by_root = current.get("eligibility_facts_by_root")
                    if not isinstance(facts_by_root, Mapping):
                        return self._blocked_terminal()
                else:
                    loaded = closure["loaded"]
                    facts_by_root = project_epd027_online_eligibility_facts(
                        route_rows=route_rows,
                        canonical_runtime_evidence=current[
                            "canonical_runtime_evidence"
                        ],
                        evidence_store=FormalEvidenceStore(
                            self.output_root
                            / "publication_gate"
                            / source
                            / "selected_metrics_1"
                        ),
                    )
                reports = tuple(
                    paper_formal_evidence.evaluate_versioned_paper_evidence(
                        facts_by_root[root_id]
                    )
                    for root_id in root_ids
                    if root_id in facts_by_root
                )
                if len(reports) != len(root_ids) or not reports:
                    return self._blocked_terminal()
                protected = closure["protected"]
                audits = tuple(
                    build_selected_formal_metrics_audit(
                        protected_descriptor_digest=protected.descriptor_digest,
                        metrics=metrics,
                        selected_table_ids=table_ids,
                        expected_evidence_class=evidence_class,
                        eligibility_reports=reports,
                    )
                    for metrics in closure["metrics"]
                )
                routes.append(
                    SelectedEvidenceRoute(
                        route_id=route_id,
                        evidence_class=evidence_class,
                        table_ids=table_ids,
                        eligibility_reports=reports,
                        selected_audits=audits,
                    )
                )
            route_values = tuple(routes)
            experiment_evidence.append(
                SelectedTerminalEvidence(
                    experiment_id=experiment,
                    terminal_status=str(terminal_status),
                    eligibility=route_values[0].eligibility_reports[0],
                    formal_report=report,
                    routes=route_values,
                )
            )
        experiment_evidence = tuple(experiment_evidence)
        l3_passed = all(
            eligibility.paper_eligible
            for item in experiment_evidence
            for route in item.routes
            for eligibility in route.eligibility_reports
        ) and all(
            audit.non_regression and not audit.publish_blocked_observation_ids
            for item in experiment_evidence
            for route in item.routes
            for audit in route.selected_audits
        )
        l4_passed = l3_passed and (
            not p0_full
            or all(
                replay.audit_level == "L4_artifact_root"
                and replay.provider_calls == 0
                and replay.source_write_count == 0
                and replay.online_fill_count == 0
                for replay in replay_results
            )
        )
        exp2_inputs = (
            load_exp2_post_bank_publication_inputs(
                trace_replay_input_root=self.output_root,
                online_replay_input_root=self.l3_online_check_root,
                contract=self.contract,
                authority=self.selection.authority,
            )
            if "exp2" in self.selection.selected_experiments
            and self.l3_online_check_root is not None
            else None
        )
        return PaperGateTerminalEnvelope(
            experiment_evidence=experiment_evidence,
            l1_attestation=self.prerequisites.l1_attestation,
            l2_attestation=self.prerequisites.l2_attestation,
            l3_attestation=PaperGateLevelAttestation(
                level="L3",
                status="passed" if l3_passed else "blocked",
                classification="formal",
                authority_digest=self.selection.authority.authority_digest,
            ),
            l4_attestation=PaperGateLevelAttestation(
                level="L4",
                status="passed" if l4_passed else "blocked",
                classification="formal",
                authority_digest=self.selection.authority.authority_digest,
            ),
            replay_results=replay_results,
            exp2_post_bank_inputs=exp2_inputs,
            paid_authorizations=self.prerequisites.paid_authorizations,
            full_bank=self.prerequisites.full_bank,
        )


def _epd027_direct_root_ids(value: object) -> tuple[str, ...]:
    root_id = getattr(value, "preregistered_root_run_id", None)
    if isinstance(root_id, str) and root_id:
        return (root_id,)
    for attribute in ("direct_result", "direct_results", "roots"):
        nested = getattr(value, attribute, None)
        if nested is not None:
            return _epd027_direct_root_ids(nested)
    if isinstance(value, Mapping):
        return tuple(
            root_id
            for nested in value.values()
            for root_id in _epd027_direct_root_ids(nested)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(
            root_id
            for nested in value
            for root_id in _epd027_direct_root_ids(nested)
        )
    return ()


def build_selected_formal_metrics_audit(
    *,
    protected_descriptor_digest: str,
    metrics: object,
    selected_table_ids: Sequence[str],
    expected_evidence_class: str,
    eligibility_reports: Sequence[object],
) -> SelectedFormalMetricsAudit:
    """只从 official FormalMetricsResult 构造 selected subset typed audit。"""

    from tokenshare.experiments.paper_formal_metrics import FormalMetricsResult

    if not isinstance(metrics, FormalMetricsResult):
        raise TypeError("selected audit requires FormalMetricsResult")
    table_ids = tuple(selected_table_ids)
    selected = tuple(
        sorted(
            (
                observation
                for observation in metrics.metric_observations
                if observation.table_id in table_ids
            ),
            key=lambda observation: observation.observation_id,
        )
    )
    if not selected or set(value.table_id for value in selected) != set(table_ids):
        raise ValueError("selected audit table observation closure is incomplete")
    provenance = []
    for observation in selected:
        provenance.append(
            {
                "observation_id": observation.observation_id,
                "direct_result_refs": list(observation.direct_result_refs),
                "current_task_attempt_event_refs": [
                    value.to_dict()
                    for value in observation.current_task_attempt_event_refs
                ],
                "parser_verifier_checker_canonical_refs": [
                    value.to_dict()
                    for value in observation.parser_verifier_checker_canonical_refs
                ],
                "ledger_refs": [value.to_dict() for value in observation.ledger_refs],
                "current_provider_object_refs": [
                    value.to_dict() for value in observation.current_provider_object_refs
                ],
                "source_bank_object_locators": [
                    value.to_dict() for value in observation.source_bank_object_locators
                ],
                "current_trace_wrappers": [
                    value.to_dict() for value in observation.current_trace_wrappers
                ],
                "trace_source_bindings": [
                    value.to_dict() for value in observation.trace_source_bindings
                ],
            }
        )
    output_refs = tuple(
        sorted(
            (
                {
                    "path": str(ref["path"]),
                    "content_hash": str(
                        ref.get("content_hash") or ref["content_digest"]
                    ),
                }
                for ref in metrics.output_refs
            ),
            key=lambda ref: ref["path"],
        )
    )
    direct_complete = all(
        observation.direct_result_refs
        and observation.current_task_attempt_event_refs
        and observation.ledger_refs
        and (
            observation.current_provider_object_refs
            if expected_evidence_class == "online_real_provider"
            else observation.source_bank_object_locators
            and observation.current_trace_wrappers
            and observation.trace_source_bindings
        )
        for observation in selected
    )
    selected_drafts = tuple(
        draft for draft in metrics.table_drafts if draft.table_id in table_ids
    )
    draft_values_complete = (
        len(selected_drafts) == len(table_ids)
        and {draft.table_id for draft in selected_drafts} == set(table_ids)
        and all(
            draft.rows
            and all(
                row.cells
                and all(
                    getattr(cell, "value", None) is not None
                    and getattr(cell, "publish_blocked", True) is False
                    for cell in row.cells
                )
                for row in draft.rows
            )
            for draft in selected_drafts
        )
    )
    report_values = tuple(eligibility_reports)
    route_eligibility_complete = bool(report_values) and all(
        getattr(report, "paper_eligible", False) is True
        and getattr(report, "evidence_class", None) == expected_evidence_class
        and (
            getattr(report, "source_manifest_complete", False) is True
            if expected_evidence_class == "real_model_trace_protocol_run"
            else getattr(report, "source_manifest_complete", True) is False
        )
        for report in report_values
    )
    verified_observations_complete = all(
        all(
            value.evidence_verified and value.value is not None
            for value in observation.verified_observations
        )
        and not observation.publish_blocked
        for observation in selected
    )
    selected_values_complete = bool(
        direct_complete
        and draft_values_complete
        and route_eligibility_complete
        and verified_observations_complete
    )
    body: dict[str, object] = {
        "descriptor_digest": protected_descriptor_digest,
        "metrics_digest": metrics.metrics_digest,
        "source_index_digest": metrics.lineage_source_index.index_digest,
        "selected_table_ids": table_ids,
        "observation_ids": tuple(value.observation_id for value in selected),
        "observation_digests": tuple(value.observation_digest for value in selected),
        "observation_table_ids": tuple(value.table_id for value in selected),
        "evidence_classes": tuple(value.evidence_class for value in selected),
        "publish_blocked_observation_ids": tuple(
            value.observation_id
            for value in selected
            if value.publish_blocked
        ),
        "direct_provenance_refs_digest": digest_json(provenance),
        "output_refs_digest": digest_json({"output_refs": list(output_refs)}),
        "output_refs": output_refs,
        "non_regression": selected_values_complete
        and all(value.evidence_class == expected_evidence_class for value in selected),
    }
    return SelectedFormalMetricsAudit(
        **body,
        selected_audit_digest=selected_formal_metrics_audit_digest(body),
    )


def _contains_identity(value: object, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_contains_identity(item, expected) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_identity(item, expected) for item in value)
    return False


def _epd027_direct_results(value: object) -> tuple[object, ...]:
    direct = getattr(value, "direct_result", None)
    if direct is not None:
        return _epd027_direct_results(direct)
    root_id = getattr(value, "preregistered_root_run_id", None)
    if isinstance(root_id, str) and root_id:
        return (value,)
    if isinstance(value, Mapping):
        return tuple(
            direct
            for nested in value.values()
            for direct in _epd027_direct_results(nested)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(
            direct for nested in value for direct in _epd027_direct_results(nested)
        )
    return ()


def project_epd027_online_eligibility_facts(
    *,
    route_rows: object,
    canonical_runtime_evidence: Sequence[object],
    evidence_store: FormalEvidenceStore,
) -> Mapping[str, object]:
    """从 verified formal closure 纯投影 online facts；不信任预塞 facts。"""

    from tokenshare.core.models import ArtifactRef
    from tokenshare.experiments.paper_models import PaperEvidenceEligibilityFacts
    from tokenshare.experiments.paper_unit_commitments import (
        build_ai_unit_binding_from_request,
    )
    from tokenshare.storage.artifacts import ArtifactStore

    if not isinstance(evidence_store, FormalEvidenceStore):
        raise TypeError("online eligibility projector requires FormalEvidenceStore")
    direct_rows = _epd027_direct_results(route_rows)
    evidence_by_root = {
        value.preregistered_root_run_id: value
        for value in canonical_runtime_evidence
        if getattr(value, "preregistered_root_run_id", None)
    }
    if not direct_rows or len(evidence_by_root) != len(canonical_runtime_evidence):
        raise ValueError("online eligibility canonical closure is incomplete")
    results: dict[str, object] = {}
    expected_roles = {
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    }
    for direct in direct_rows:
        root_id = direct.preregistered_root_run_id
        evidence = evidence_by_root.get(root_id)
        binding = getattr(direct, "execution_binding", None)
        if evidence is None or binding is None or direct.evidence_class != (
            "online_real_provider"
        ):
            raise ValueError("online eligibility direct/evidence binding is incomplete")
        roles = {
            value.source_role for value in evidence.current_provider_object_refs
        }
        if roles != expected_roles or len(evidence.current_provider_object_refs) != 8:
            raise ValueError("online eligibility current-provider roles are incomplete")
        records = evidence_store.load_logical_run_records(
            experiment_id=direct.experiment_id,
            condition_id=direct.condition_id,
            repeat_id=direct.repeat_id,
        )
        attempts = tuple(
            attempt
            for attempt in records["attempts"]
            if attempt.get("task_id") == binding.task_id
            and int(attempt.get("provider_attempt_count", 0)) == 1
        )
        if not attempts:
            raise ValueError("online eligibility provider attempts are missing")
        artifact_records = {
            str(record["artifact_id"]): record for record in records["artifacts"]
        }
        events = tuple(records["events"])
        event_ids = {str(event.get("event_id", "")) for event in events}
        terminal = evidence.terminal_root_event_ref
        if terminal is None or terminal.event_id not in event_ids:
            raise ValueError("online eligibility terminal lifecycle is missing")
        with tempfile.TemporaryDirectory(prefix="tokenshare-online-facts-") as staging:
            store = ArtifactStore(staging)
            for record in artifact_records.values():
                source_ref = ArtifactRef.from_dict(record["source_artifact_ref"])
                content = (
                    evidence_store.output_root / str(record["path"])
                ).read_bytes()
                restored = store.save_bytes(
                    content,
                    artifact_id=source_ref.artifact_id,
                    artifact_type=source_ref.artifact_type,
                    media_type=source_ref.media_type,
                    artifact_schema_id=source_ref.artifact_schema_id,
                    artifact_schema_version=source_ref.artifact_schema_version,
                    source=source_ref.source,
                    metadata=source_ref.metadata,
                    created_at=source_ref.created_at,
                )
                if restored.to_dict() != source_ref.to_dict():
                    raise ValueError("online eligibility artifact identity mismatch")
            executed_bindings = []
            attempt_refs = []
            lifecycle_refs = []
            for attempt in sorted(attempts, key=lambda value: str(value["attempt_id"])):
                request_ref = ArtifactRef.from_dict(attempt["request_ref"])
                request_record = artifact_records.get(request_ref.artifact_id)
                if request_record is None:
                    raise ValueError("online eligibility request artifact is missing")
                request_body = json.loads(store.read_bytes(request_ref))
                planned_id = str(attempt.get("planned_ai_unit_id") or "")
                unit_id = str(attempt.get("unit_id") or "")
                attempt_id = str(attempt.get("attempt_id") or "")
                if not planned_id or not unit_id or not attempt_id:
                    raise ValueError("online eligibility attempt identity is incomplete")
                unit_binding = build_ai_unit_binding_from_request(
                    planned_ai_unit_id=planned_id,
                    request_body=request_body,
                    store=store,
                    include_request_artifacts=False,
                )
                if unit_binding["unit_id"] != unit_id:
                    raise ValueError("online eligibility request/unit identity mismatch")
                matching_events = tuple(
                    event
                    for event in events
                    if _contains_identity(event.get("payload", {}), attempt_id)
                )
                if not matching_events:
                    raise ValueError("online eligibility attempt event is missing")
                artifact_ref = attempt.get("provenance_ref") or attempt.get(
                    "raw_output_ref"
                )
                if not isinstance(artifact_ref, Mapping) or str(
                    artifact_ref.get("artifact_id", "")
                ) not in artifact_records:
                    raise ValueError("online eligibility attempt artifact is missing")
                request_digest = digest_json(request_body)
                executed_bindings.append(unit_binding)
                attempt_refs.append(
                    {
                        "schema_version": "tokenshare.paper_current_online_attempt_ref.v1",
                        "planned_ai_unit_id": planned_id,
                        "unit_id": unit_id,
                        "unit_binding_digest": unit_binding["binding_digest"],
                        "attempt_id": attempt_id,
                        "request_identity_digest": request_digest,
                        "real_transport": True,
                        "identity_consistent": True,
                    }
                )
                lifecycle_refs.append(
                    {
                        "schema_version": "tokenshare.paper_current_online_lifecycle_ref.v1",
                        "planned_ai_unit_id": planned_id,
                        "unit_id": unit_id,
                        "unit_binding_digest": unit_binding["binding_digest"],
                        "attempt_ref": attempt_id,
                        "event_ref": str(matching_events[0]["event_id"]),
                        "artifact_ref": str(artifact_ref["artifact_id"]),
                        "terminal_ref": terminal.event_id,
                    }
                )
        facts = PaperEvidenceEligibilityFacts(
            evidence_class="online_real_provider",
            source_classification="current_real_provider",
            executed_ai_unit_count=len(executed_bindings),
            executed_unit_bindings=tuple(executed_bindings),
            current_provider_call_count=len(attempt_refs),
            source_provider_call_count=0,
            current_real_provider_attempt_refs=tuple(attempt_refs),
            current_lifecycle_refs=tuple(lifecycle_refs),
            trace_source_bindings=(),
            source_manifest=None,
            source_inventory_rows=(),
            source_entries=(),
            paid_receipt_claim=None,
            direct_evidence_complete=bool(evidence.paper_evidence_complete),
            identity_consistent=bool(
                evidence.identity_consistent and evidence.infrastructure_valid
            ),
            regression_only=False,
        )
        results[root_id] = facts
    return results


def _epd027_formal_gate_components(
    *,
    command: str,
    profile: object,
    plan_digest: str,
    inventory_digest: str,
    output_root: Path,
    paid_authorization_bindings: Sequence[SelectedPaidAuthorizationBinding],
    full_bank: CompleteFullBankPrerequisite | None,
    l3_online_check_root: Path | None,
) -> tuple[
    PaperGateSelectionEnvelope | None,
    PaperGatePrerequisiteEnvelope | None,
    EPD027FormalPublicationGateFactory | None,
]:
    selected_by_command = {
        "run-trace": ("exp2", "exp3", "exp4"),
        "run-exp1-online": ("exp1",),
        "run-exp5-online": ("exp5",),
    }
    selected = selected_by_command.get(command)
    if selected is None:
        return None, None, None
    contract = load_paper_metric_contract()
    profile_digest = getattr(profile, "profile_digest", None)
    if contract.pipeline_profile_digest != profile_digest:
        raise ValueError("formal gate metric contract/profile binding mismatch")
    authority = PaperGateAuthorityDigests(
        profile_digest=profile_digest,
        contract_digest=contract.contract_digest,
        plan_digest=plan_digest,
        inventory_digest=inventory_digest,
    )
    bindings = tuple(paid_authorization_bindings)
    if any(not isinstance(value, SelectedPaidAuthorizationBinding) for value in bindings):
        raise TypeError("formal gate requires typed paid authorization bindings")
    commitment = (
        build_paid_authorization_authority_commitment(bindings)
        if bindings
        else None
    )
    committed_bindings = tuple(
        replace(
            binding,
            authority_commitment_digest=commitment.commitment_digest,
        )
        for binding in bindings
    )
    selection = PaperGateSelectionEnvelope(
        selected_experiments=selected,
        classification="formal",
        authority=authority,
        authorization_commitment=commitment,
    )
    prerequisites = PaperGatePrerequisiteEnvelope(
        l1_attestation=PaperGateLevelAttestation(
            level="L1",
            status="passed",
            classification="formal",
            authority_digest=authority.authority_digest,
        ),
        l2_attestation=PaperGateLevelAttestation(
            level="L2",
            status="passed",
            classification="formal",
            authority_digest=authority.authority_digest,
        ),
        paid_authorizations=committed_bindings,
        full_bank=full_bank,
    )
    return (
        selection,
        prerequisites,
        EPD027FormalPublicationGateFactory(
            selection=selection,
            prerequisites=prerequisites,
            output_root=output_root,
            contract=contract,
            l3_online_check_root=l3_online_check_root,
        ),
    )


def _select_online_checks_dispatch(
    *,
    dispatch_plans: Sequence[object],
    profile: object,
) -> tuple[tuple[object, ...], dict[str, tuple[str, ...]]]:
    """从 canonical Exp1/2/3 plans 选择 2+24+2 个 authoritative online roots。"""

    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan

    by_experiment = {plan.experiment_id: plan for plan in dispatch_plans}
    selected_plans: list[object] = []
    root_filter: dict[str, tuple[str, ...]] = {}

    exp1 = by_experiment["exp1_real_ai_feasibility"]
    capability_cases = (
        profile.capability.factor.case_id,
        profile.capability.lean.case_id,
    )
    exp1_items = []
    for case_id in capability_cases:
        matches = tuple(
            (condition, selection)
            for condition, selection in exp1.bound_items()
            if case_id in selection.ordered_case_ids
        )
        if len(matches) != 1:
            raise ValueError("online capability case is not uniquely bound")
        condition, selection = matches[0]
        exp1_items.append((condition, selection))
        root_filter[condition.condition_id] = (case_id,)
    selected_plans.append(
        PaperExperimentDispatchPlan(
            experiment_id=exp1.experiment_id,
            output_root=exp1.output_root,
            conditions=tuple(condition for condition, _selection in exp1_items),
            condition_selection_bindings=tuple(
                binding
                for binding in exp1.condition_selection_bindings
                if binding.condition_id
                in {condition.condition_id for condition, _selection in exp1_items}
            ),
            catalog_execution_view=exp1.catalog_execution_view,
        )
    )

    exp2 = by_experiment["exp2_real_ai_scalability"]
    exp2_items = tuple(
        (condition, selection)
        for condition, selection in exp2.bound_items()
        if condition.repeat_id == profile.exp2.repeat_id
        and condition.worker_count in profile.exp2.workers
    )
    if len(exp2_items) != len(profile.exp2.workers):
        raise ValueError("online Exp2 worker conditions are not uniquely bound")
    for condition, _selection in exp2_items:
        cases = tuple(
            item.case_id
            for item in profile.exp2_conditions
            if item.worker_count == condition.worker_count
            and item.repeat_id == condition.repeat_id
        )
        if len(cases) != 4 or len(set(cases)) != 4:
            raise ValueError("online Exp2 condition must bind four fixed cases")
        root_filter[condition.condition_id] = cases
    selected_plans.append(
        PaperExperimentDispatchPlan(
            experiment_id=exp2.experiment_id,
            output_root=exp2.output_root,
            conditions=tuple(condition for condition, _selection in exp2_items),
            condition_selection_bindings=tuple(
                binding
                for binding in exp2.condition_selection_bindings
                if binding.condition_id
                in {condition.condition_id for condition, _selection in exp2_items}
            ),
            catalog_execution_view=exp2.catalog_execution_view,
        )
    )

    exp3 = by_experiment["exp3_real_ai_fault_recovery"]
    exp3_items = []
    for planned in profile.exp3_cases:
        matches = tuple(
            (condition, selection)
            for condition, selection in exp3.bound_items()
            if condition.repeat_id == planned.repeat_id
            and condition.fault_type == planned.check_kind
            and planned.case_id in selection.ordered_case_ids
            and (
                planned.check_kind != "false_positive"
                or condition.fault_rate == 1.0
            )
            and (
                planned.check_kind != "worker_death"
                or "__dead1__p50__" in condition.condition_id
            )
        )
        if len(matches) != 1:
            raise ValueError("online Exp3 condition is not uniquely bound")
        condition, selection = matches[0]
        exp3_items.append((condition, selection))
        root_filter[condition.condition_id] = (planned.case_id,)
    selected_plans.append(
        PaperExperimentDispatchPlan(
            experiment_id=exp3.experiment_id,
            output_root=exp3.output_root,
            conditions=tuple(condition for condition, _selection in exp3_items),
            condition_selection_bindings=tuple(
                binding
                for binding in exp3.condition_selection_bindings
                if binding.condition_id
                in {condition.condition_id for condition, _selection in exp3_items}
            ),
            catalog_execution_view=exp3.catalog_execution_view,
        )
    )
    return tuple(selected_plans), root_filter


def build_epd027_formal_service_authority(
    *,
    command: str,
    profile: object,
    output_root: str | Path,
    resume: bool = False,
    plan_bundle_root: str | Path | None = None,
    external_bank_resolver: object | None = None,
    paid_authorization_bindings: Sequence[SelectedPaidAuthorizationBinding] = (),
    l3_online_check_root: str | Path | None = None,
) -> EPD027FormalServiceAuthority:
    """复算 tracked catalog/scale/config authority；绝不读取 provider secret。"""

    if command not in {
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-online",
    }:
        raise ValueError("unsupported EPD-027 formal authority command")
    root = Path(output_root).resolve(strict=False)
    authorities = getattr(profile, "authorities", None)
    offline_approval = getattr(profile, "offline_approval", None)
    profile_digest = getattr(profile, "profile_digest", None)
    if authorities is None or offline_approval is None or not isinstance(
        profile_digest, str
    ):
        raise ValueError("EPD-027 pipeline profile authority is incomplete")
    plan_digest = getattr(offline_approval, "approved_plan_digest", None)
    if not isinstance(plan_digest, str) or not plan_digest.startswith("sha256:"):
        raise ValueError("EPD-027 approved plan digest is missing")

    catalog_manifest = load_paper_catalogs(
        factorization_path=Path(authorities.factorization_catalog_path),
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=Path(authorities.lean_catalog_path),
    )
    planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
    _validate_exp1_exp4_v3_execution_config(planning_profile.source_provider_config)
    suite_scale_profile = load_paper_suite_scale_profile(
        Path(authorities.paper_suite_scale_profile_path)
    )

    if command == "run-online-checks":
        experiment_ids = EXP1_EXP4_ONLY_EXPERIMENT_IDS[:3]
    elif command == "run-exp1-online":
        experiment_ids = ("exp1_real_ai_feasibility",)
    elif command == "run-exp5-online":
        experiment_ids = ("exp5_real_ai_model_endpoint_comparison",)
    else:
        experiment_ids = EXP1_EXP4_ONLY_EXPERIMENT_IDS[1:]

    model_endpoint_cohort_preflight = None
    execution_configs: dict[str, object] = {}
    if command == "run-exp5-online":
        cohort = load_model_endpoint_cohort(DEFAULT_EXP5_MODEL_COHORT)
        entry_map = load_model_entry_map(DEFAULT_EXP5_MODEL_ENTRY_MAP)
        provider_configs = load_provider_config_map(
            {"siliconflow": DEFAULT_EXP5_PROVIDER_CONFIG}
        )
        if {
            entry.api_key_env
            for config in provider_configs.values()
            for entry in config.entries
            if entry.enabled
        } != {"SILICONFLOW_API_KEY"}:
            raise ValueError("Exp5 v3 API key env authority drift")
        shadow_configs = {
            config_id: replace(
                config,
                entries=tuple(
                    replace(
                        entry,
                        api_key_env="TOKENSHARE_PRESECRET_AUTHORITY_UNSET",
                    )
                    for entry in config.entries
                ),
            )
            for config_id, config in provider_configs.items()
        }
        model_endpoint_cohort_preflight = build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=shadow_configs,
            require_smoke_evidence=False,
            smoke_evidence_bundle=None,
        )
        member_plans = model_endpoint_cohort_preflight.get("member_plans")
        ineligible = model_endpoint_cohort_preflight.get("ineligible_members")
        expected_presecret_reasons = {
            "api_key_env_mismatch",
            "missing_api_key_env",
        }
        if not isinstance(member_plans, Mapping) or not isinstance(ineligible, list):
            raise ValueError("Exp5 v3 structural preflight is malformed")
        if any(
            set(item.get("blocked_reasons", ())) != expected_presecret_reasons
            for item in ineligible
            if isinstance(item, Mapping)
        ) or len(ineligible) != len(member_plans):
            raise ValueError("Exp5 v3 structural preflight has non-secret failures")
        for member_plan in member_plans.values():
            if not isinstance(member_plan, dict) or set(
                member_plan.get("blocked_reasons", ())
            ) != expected_presecret_reasons:
                raise ValueError("Exp5 v3 member structural preflight failed")
            member_plan["api_key_env"] = "SILICONFLOW_API_KEY"
            member_plan["status"] = "planned"
            member_plan["blocked_reasons"] = []
        model_endpoint_cohort_preflight.update(
            {
                "status": "planned",
                "paper_eligible_possible": True,
                "blocked_reason": None,
                "ineligibility_reasons": [],
                "ineligible_members": [],
            }
        )
        if model_endpoint_cohort_preflight.get("status") != "planned":
            raise ValueError("Exp5 v3 model endpoint cohort preflight is not planned")
        execution_configs.update(provider_configs)
        execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY] = {
            "exp5_real_ai_model_endpoint_comparison": model_endpoint_cohort_preflight
        }
        transport: object = _ProviderFamilyTransportRouter(
            provider_family_by_entry_id=(
                _provider_family_bindings_from_execution_configs(execution_configs)
            )
        )
    else:
        execution_configs[
            planning_profile.model_endpoint_identity.provider_config_id
        ] = planning_profile.source_provider_config
        transport = UrlLibDeepSeekTransport()

    lean_3x3_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=catalog_manifest
    )
    dispatch_plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog_manifest,
        lean_3x3_matrix=lean_3x3_matrix,
        experiment_ids=experiment_ids,
        baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        paper_suite_scale_profile=suite_scale_profile,
        output_root=root,
    )
    online_root_filter: dict[str, tuple[str, ...]] | None = None
    if command == "run-online-checks":
        dispatch_plans, online_root_filter = _select_online_checks_dispatch(
            dispatch_plans=dispatch_plans,
            profile=profile,
        )
    conditions = tuple(
        condition
        for dispatch_plan in dispatch_plans
        for condition in dispatch_plan.conditions
    )
    frozen_selection_commitments = [
        {
            **selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
        }
        for dispatch_plan in dispatch_plans
        for condition, selection in dispatch_plan.bound_items()
    ]
    endpoint_token_ceilings = build_exp5_v3_token_ceiling_mapping(
        model_endpoint_cohort_preflight
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog_manifest,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=(
            FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
        ),
        token_upper_bound_by_endpoint_identity_digest=endpoint_token_ceilings,
        cost_upper_bound_per_provider_attempt=(
            FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
        ),
        plan_only=True,
        lean_3x3_matrix=lean_3x3_matrix,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        frozen_selections=frozen_selection_commitments,
        endpoint_identity={
            "baseline": _baseline_endpoint_binding(planning_profile),
            "model_endpoint_cohort_preflight": model_endpoint_cohort_preflight,
        },
        request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
        suite_identity={
            "suite_version": "paper_v1",
            "execution_scope": "formal_matrix",
            "experiment_ids": list(experiment_ids),
            "paper_suite_scale_profile_source_path": suite_scale_profile.source_path,
            "paper_suite_scale_profile_digest": suite_scale_profile.profile_digest,
            "exp5_selection_path": suite_scale_profile.exp5_source_selection_path,
            "exp5_selection_digest": suite_scale_profile.exp5_source_selection_digest,
        },
        output_identity={
            "output_root": root.as_posix(),
            "per_experiment_roots": [
                (root / experiment_id).as_posix()
                for experiment_id in experiment_ids
            ],
            **_shared_exp1_reference_output_identity(experiment_ids),
        },
        budget_approval_required=True,
    )
    dispatch_inventory_digest = digest_json(
        {
            "schema_version": "tokenshare.paper_dispatch_inventory.v1",
            "dispatch_plans": [item.to_dict() for item in dispatch_plans],
        }
    )
    trace_context = None
    full_bank_prerequisite = None
    inventory_digest = dispatch_inventory_digest
    if command == "run-trace":
        if plan_bundle_root is None or external_bank_resolver is None:
            raise ValueError(
                "run-trace requires plan bundle and process-local external resolver"
            )
        from tokenshare.experiments.paper_response_bank import (
            build_paper_formal_trace_context,
            load_acquisition_plan_bundle,
            preflight_formal_trace_inventory,
        )

        bundle = load_acquisition_plan_bundle(plan_bundle_root)
        if bundle.authorized_plan_digest != plan_digest:
            raise ValueError("trace acquisition bundle plan digest mismatch")
        if bundle.profile_digest != profile_digest:
            raise ValueError("trace acquisition bundle profile digest mismatch")
        resolver = external_bank_resolver.open()
        trace_context = build_paper_formal_trace_context(
            inventory_plan=bundle.semantic_inventory_plan,
            resolver=resolver,
        )
        trace_preflight = preflight_formal_trace_inventory(
            required_inventory_entry_ids=tuple(
                row.inventory_entry_id for row in bundle.semantic_inventory_plan.rows
            ),
            available_inventory_entry_ids=tuple(
                entry.inventory_entry_id for entry in resolver.index.entries
            ),
        )
        full_bank_prerequisite = CompleteFullBankPrerequisite(
            plan_digest=plan_digest,
            inventory_digest=bundle.inventory_digest,
            manifest=resolver.index.manifest,
            preflight=trace_preflight,
        )
        inventory_digest = bundle.inventory_digest
    elif command == "run-online-checks":
        from tokenshare.experiments.paper_online_checks import (
            freeze_paper_online_checks_plan,
        )

        inventory_digest = freeze_paper_online_checks_plan().plan_digest

    hard_limits = {
        "max_total_provider_attempts": budget.max_provider_attempts,
        "max_total_tokens": budget.token_upper_bound,
        "max_cost_estimate": budget.cost_upper_bound,
    }
    keyword_arguments: dict[str, object] = {
        "dispatch_plans": dispatch_plans,
        "catalog_manifest": catalog_manifest,
        "budget": budget,
        "budget_approval": {
            "approval_mode": (
                "immutable_response_bank" if command == "run-trace" else "paid_receipt"
            ),
            "budget_digest": budget.budget_digest,
        },
        "output_root": root,
        "ai_api_configs": execution_configs,
        "transport": transport,
        "real_transport": command != "run-trace",
        "hard_limits": hard_limits,
        "resume": bool(resume),
        "replay_only": False,
        "suite_id": f"epd027_{command.removeprefix('run-').replace('-', '_')}",
    }
    if trace_context is not None:
        keyword_arguments["trace_context"] = trace_context
    if online_root_filter is not None:
        keyword_arguments["root_case_filter"] = online_root_filter
        keyword_arguments["hard_limits"] = {
            "max_total_provider_attempts": profile.calls_hard_limit,
            "max_total_tokens": profile.tokens_hard_limit,
            "max_cost_estimate": float(profile.budget.cny_reservation_hard_limit),
        }
    gate_selection, gate_prerequisites, publication_gate_factory = (
        _epd027_formal_gate_components(
            command=command,
            profile=profile,
            plan_digest=plan_digest,
            inventory_digest=inventory_digest,
            output_root=root,
            paid_authorization_bindings=paid_authorization_bindings,
            full_bank=full_bank_prerequisite,
            l3_online_check_root=(
                None
                if l3_online_check_root is None
                else Path(l3_online_check_root).resolve(strict=False)
            ),
        )
    )
    return EPD027FormalServiceAuthority(
        command=command,
        plan_digest=plan_digest,
        inventory_digest=inventory_digest,
        budget_digest=(
            profile.budget_digest
            if command == "run-online-checks"
            else budget.budget_digest
        ),
        keyword_arguments=keyword_arguments,
        gate_selection=gate_selection,
        gate_prerequisites=gate_prerequisites,
        publication_gate_factory=publication_gate_factory,
    )


def build_epd027_capability_smoke_service_authority(
    *,
    profile: object,
    output_root: str | Path,
    resume: bool = False,
    paid_authorization: object | None = None,
):
    """从同一 canonical Exp5 plan 派生 capability smoke authority。"""

    formal = build_epd027_formal_service_authority(
        command="run-exp5-online",
        profile=profile,
        output_root=output_root,
        resume=resume,
    )
    formal_kwargs = dict(formal.keyword_arguments)
    catalog_manifest = formal_kwargs["catalog_manifest"]
    canonical_plans = tuple(formal_kwargs["dispatch_plans"])
    execution_configs = dict(formal_kwargs["ai_api_configs"])
    model_preflight = execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY][
        "exp5_real_ai_model_endpoint_comparison"
    ]
    smoke_profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp5_profile.v3.json")
    )
    execution_plan = resolve_paper_smoke_execution_plan(
        profile=smoke_profile,
        dispatch_plans=canonical_plans,
        catalog_id=catalog_manifest.catalog_id,
        catalog_version=catalog_manifest.catalog_version,
        catalog_digest=catalog_manifest.catalog_digest,
        output_root=output_root,
    )
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    frozen_selections = execution_plan.budget_selection_commitments(
        expected_ai_units_by_case={
            item.case_id: estimated_ai_units_for_case(cases_by_id[item.case_id])
            for item in execution_plan.items
        }
    )
    planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
    suite_scale_profile = load_paper_suite_scale_profile(
        Path(profile.authorities.paper_suite_scale_profile_path)
    )
    lean_3x3_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=catalog_manifest
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog_manifest,
        conditions=tuple(
            condition
            for dispatch_plan in execution_plan.dispatch_plans
            for condition in dispatch_plan.conditions
        ),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=(
            FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
        ),
        token_upper_bound_by_endpoint_identity_digest=(
            build_exp5_v3_token_ceiling_mapping(model_preflight)
        ),
        cost_upper_bound_per_provider_attempt=(
            FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
        ),
        plan_only=True,
        lean_3x3_matrix=lean_3x3_matrix,
        model_endpoint_cohort_preflight=model_preflight,
        frozen_selections=frozen_selections,
        endpoint_identity={
            "baseline": _baseline_endpoint_binding(planning_profile),
            "model_endpoint_cohort_preflight": model_preflight,
        },
        request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
        hard_limits={},
        suite_identity={
            "suite_version": "paper_smoke_v1",
            "execution_scope": "smoke_suite",
            "profile_digest": smoke_profile.profile_digest,
            "execution_plan_digest": execution_plan.execution_plan_digest,
            "experiment_ids": list(smoke_profile.experiment_ids),
            "paper_suite_scale_profile_source_path": suite_scale_profile.source_path,
            "paper_suite_scale_profile_digest": suite_scale_profile.profile_digest,
            "exp5_selection_path": suite_scale_profile.exp5_source_selection_path,
            "exp5_selection_digest": suite_scale_profile.exp5_source_selection_digest,
        },
        output_identity={
            "output_root": Path(output_root).resolve(strict=False).as_posix(),
            "formal_output_root_allowed": False,
            **_shared_exp1_reference_output_identity(
                smoke_profile.experiment_ids,
                baseline_policy=smoke_profile.baseline_policy,
            ),
        },
        budget_approval_required=True,
    )
    execution_transport, _limiter = _exp5_v3_smoke_execution_transport(
        profile=smoke_profile,
        execution_plan=execution_plan,
        model_endpoint_cohort_preflight=model_preflight,
        transport=formal_kwargs["transport"],
        ai_api_configs=execution_configs,
    )
    receipt = getattr(paid_authorization, "receipt", None)
    marker = getattr(paid_authorization, "marker", None)
    launch_manifest = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "authorization_scope": "exp5_capability_smoke",
        "profile_digest": smoke_profile.profile_digest,
        "execution_plan_digest": execution_plan.execution_plan_digest,
        "budget_digest": budget.budget_digest,
        "receipt_digest": getattr(receipt, "receipt_digest", None),
        "output_marker_digest": getattr(marker, "marker_digest", None),
    }
    return build_paper_smoke_service_authority(
        scope="exp5_capability_smoke",
        authorized_plan_digest=profile.offline_approval.approved_plan_digest,
        profile=smoke_profile,
        execution_plan=execution_plan,
        catalog_manifest=catalog_manifest,
        budget=budget,
        ai_api_configs=execution_configs,
        transport=execution_transport,
        hard_limits={
            "max_total_provider_attempts": budget.max_provider_attempts,
            "max_total_tokens": budget.token_upper_bound,
            "max_cost_estimate": budget.cost_upper_bound,
        },
        resume=resume,
        launch_manifest=launch_manifest,
        authorization_budget_digest=profile.budget_digest,
    )


class _ProviderFamilyTransportRouter:
    """为 mixed smoke 按已验证的 entry 绑定选择既有真实 transport。"""

    def __init__(
        self,
        *,
        provider_family_by_entry_id: Mapping[str, str],
    ) -> None:
        self._provider_family_by_entry_id = dict(provider_family_by_entry_id)
        self._transports = {
            "siliconflow": UrlLibSiliconFlowTransport(),
            "openai": UrlLibOpenAITransport(),
            "deepseek": UrlLibDeepSeekTransport(),
        }

    def tokenshare_transport_for_provider(self, provider_family: str):
        try:
            return self._transports[provider_family]
        except KeyError as exc:
            raise ValueError(
                f"unsupported routed provider_family: {provider_family}"
            ) from exc

def _provider_family_bindings_from_execution_configs(
    ai_api_configs: Mapping[str, object],
) -> dict[str, str]:
    """从本次已加载配置建立 router 所需的完整 entry/provider 映射。"""

    bindings: dict[str, str] = {}
    for config_id, config in ai_api_configs.items():
        if config_id == APPROVED_ENDPOINT_BINDINGS_KEY:
            continue
        provider_family = getattr(config, "provider_family", None)
        entries = getattr(config, "entries", None)
        if (
            not isinstance(provider_family, str)
            or not provider_family
            or not isinstance(entries, Sequence)
            or isinstance(entries, (str, bytes))
        ):
            raise ValueError("routed AI API execution config is invalid")
        for entry in entries:
            entry_id = getattr(entry, "entry_id", None)
            if not isinstance(entry_id, str) or not entry_id:
                raise ValueError("routed AI API entry identity is missing")
            previous = bindings.get(entry_id)
            if previous is not None and previous != provider_family:
                raise ValueError(
                    "routed AI API entry identity maps to multiple provider families"
                )
            bindings[entry_id] = provider_family
    if not bindings:
        raise ValueError("routed AI API execution config inventory is empty")
    return bindings


class _Exp5V3SharedProviderTransport:
    """四个 v3 entry 共用一个 provider-config namespace semaphore。"""

    def __init__(
        self,
        *,
        delegate: object,
        member_id_by_entry_id: Mapping[str, str],
        member_id_by_model: Mapping[str, str] | None = None,
        schedule_binding: Mapping[str, object],
        prior_evidence: Mapping[str, object] | None = None,
    ) -> None:
        normalized_binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
        max_in_flight_global = int(normalized_binding["max_in_flight_global"])
        if type(max_in_flight_global) is not int or max_in_flight_global != 3:
            raise ValueError(
                "Exp5 v3 shared provider limit must be exact integer 3"
            )
        if not member_id_by_entry_id or len(
            set(member_id_by_entry_id.values())
        ) != len(member_id_by_entry_id):
            raise ValueError("Exp5 v3 entry/member namespace mapping is invalid")
        self.tokenshare_underlying_transport = delegate
        self.tokenshare_offline_capturing_transport = bool(
            getattr(
                delegate,
                "tokenshare_offline_capturing_transport",
                False,
            )
        )
        self._delegate = delegate
        self._member_id_by_entry_id = dict(member_id_by_entry_id)
        self._member_id_by_model = dict(member_id_by_model or {})
        self._schedule_binding = normalized_binding
        self._max_in_flight_global = max_in_flight_global
        self._prior_evidence = (
            _validate_exp5_v3_execution_schedule(
                prior_evidence,
                schedule_binding=normalized_binding,
            )
            if prior_evidence is not None
            else None
        )
        self._semaphore = BoundedSemaphore(max_in_flight_global)
        self._lock = Lock()
        self._active = 0
        self._active_by_member: dict[str, int] = {}
        self._observed_peak = 0
        self._provider_calls_made = 0
        self._observed_member_order: list[str] = []
        self._arm_windows: dict[str, dict[str, object]] = {}

    @property
    def new_provider_calls_made(self) -> int:
        with self._lock:
            return self._provider_calls_made

    @property
    def prior_evidence(self) -> dict[str, object] | None:
        if self._prior_evidence is None:
            return None
        return json.loads(json.dumps(self._prior_evidence))

    def tokenshare_transport_for_provider(self, provider_family: str):
        if provider_family == "siliconflow":
            return self
        resolver = getattr(self._delegate, "tokenshare_transport_for_provider", None)
        if callable(resolver):
            return resolver(provider_family)
        return self._delegate

    def post_chat_completion(
        self,
        *,
        api_key: str,
        body_bytes: bytes,
        normalized_absolute_endpoint: str,
        content_type: str,
        timeout_seconds: int,
    ):
        body = json.loads(body_bytes.decode("utf-8"))
        explicit_member = body.get("member_id") if isinstance(body, dict) else None
        member_id = (
            str(explicit_member)
            if explicit_member in self._member_id_by_entry_id.values()
            else self._member_id_by_model.get(str(body.get("model", "")))
            if isinstance(body, dict)
            else None
        )
        delegate = self._delegate
        resolver = getattr(delegate, "tokenshare_transport_for_provider", None)
        if callable(resolver):
            delegate = resolver("siliconflow")
        if member_id is None:
            return delegate.post_chat_completion(
                api_key=api_key,
                body_bytes=body_bytes,
                normalized_absolute_endpoint=normalized_absolute_endpoint,
                content_type=content_type,
                timeout_seconds=timeout_seconds,
            )
        with self._semaphore:
            self._record_call_started(member_id)
            try:
                return delegate.post_chat_completion(
                    api_key=api_key,
                    body_bytes=body_bytes,
                    normalized_absolute_endpoint=normalized_absolute_endpoint,
                    content_type=content_type,
                    timeout_seconds=timeout_seconds,
                )
            finally:
                self._record_call_ended(member_id)

    def execution_evidence(self) -> dict[str, object]:
        with self._lock:
            if self._active != 0 or any(self._active_by_member.values()):
                raise ValueError(
                    "Exp5 v3 execution schedule has provider calls still in flight"
                )
            prior_segments = (
                list(self._prior_evidence["execution_segments"])
                if self._prior_evidence is not None
                else []
            )
            segments = json.loads(json.dumps(prior_segments))
            if self._provider_calls_made:
                windows = [
                    dict(self._arm_windows[member_id])
                    for member_id in self._observed_member_order
                ]
                segment_body: dict[str, object] = {
                    "segment_index": len(segments),
                    "started_at": windows[0]["started_at"],
                    "ended_at": windows[-1]["ended_at"],
                    "observed_member_order": list(self._observed_member_order),
                    "observed_peak": self._observed_peak,
                    "provider_calls_made": self._provider_calls_made,
                    "arm_windows": windows,
                }
                segments.append(
                    {
                        **segment_body,
                        "segment_digest": digest_json(segment_body),
                    }
                )
        return _build_exp5_v3_schedule_evidence(
            schedule_binding=self._schedule_binding,
            execution_segments=segments,
        )

    def _record_call_started(self, member_id: str) -> None:
        recorded_at = _utc_now_string()
        tick = perf_counter_ns()
        with self._lock:
            self._active += 1
            self._provider_calls_made += 1
            self._observed_peak = max(self._observed_peak, self._active)
            member_active = self._active_by_member.get(member_id, 0) + 1
            self._active_by_member[member_id] = member_active
            if (
                not self._observed_member_order
                or self._observed_member_order[-1] != member_id
            ):
                self._observed_member_order.append(member_id)
            window = self._arm_windows.setdefault(
                member_id,
                {
                    "cohort_member_id": member_id,
                    "started_at": recorded_at,
                    "ended_at": recorded_at,
                    "started_tick_ns": tick,
                    "ended_tick_ns": tick,
                    "provider_calls_made": 0,
                    "observed_peak": 0,
                },
            )
            window["provider_calls_made"] = int(
                window["provider_calls_made"]
            ) + 1
            window["observed_peak"] = max(
                int(window["observed_peak"]),
                member_active,
            )

    def _record_call_ended(self, member_id: str) -> None:
        recorded_at = _utc_now_string()
        tick = perf_counter_ns()
        with self._lock:
            self._active -= 1
            self._active_by_member[member_id] -= 1
            window = self._arm_windows[member_id]
            window["ended_at"] = recorded_at
            window["ended_tick_ns"] = max(
                int(window["ended_tick_ns"]),
                tick,
            )


def _normalize_exp5_v3_schedule_binding(
    schedule_binding: Mapping[str, object],
) -> dict[str, object]:
    required = {
        "profile_digest",
        "execution_plan_digest",
        "sequence_plan_digest",
        "provider_namespace",
        "max_in_flight_global",
        "expected_member_order",
        "expected_condition_ids",
    }
    if set(schedule_binding) != required:
        raise ValueError("Exp5 v3 execution schedule binding fields are invalid")
    normalized = json.loads(json.dumps(dict(schedule_binding)))
    for name in (
        "profile_digest",
        "execution_plan_digest",
        "sequence_plan_digest",
        "provider_namespace",
    ):
        if not isinstance(normalized[name], str) or not normalized[name]:
            raise ValueError(f"Exp5 v3 execution schedule {name} is invalid")
    if type(normalized["max_in_flight_global"]) is not int or normalized[
        "max_in_flight_global"
    ] != 3:
        raise ValueError("Exp5 v3 execution schedule provider limit is invalid")
    expected_member_order = normalized["expected_member_order"]
    if (
        not isinstance(expected_member_order, list)
        or len(expected_member_order) != 4
        or any(not isinstance(item, str) or not item for item in expected_member_order)
        or len(set(expected_member_order)) != 4
    ):
        raise ValueError("Exp5 v3 execution schedule member order is invalid")
    expected_condition_ids = normalized["expected_condition_ids"]
    if (
        not isinstance(expected_condition_ids, list)
        or len(expected_condition_ids) != 8
        or any(not isinstance(item, str) or not item for item in expected_condition_ids)
        or len(set(expected_condition_ids)) != 8
    ):
        raise ValueError("Exp5 v3 execution schedule condition binding is invalid")
    return normalized


def _validate_exp5_v3_arm_window(window: object) -> dict[str, object]:
    required = {
        "cohort_member_id",
        "started_at",
        "ended_at",
        "started_tick_ns",
        "ended_tick_ns",
        "provider_calls_made",
        "observed_peak",
    }
    if not isinstance(window, Mapping) or set(window) != required:
        raise ValueError("Exp5 v3 execution schedule arm window fields are invalid")
    normalized = json.loads(json.dumps(dict(window)))
    member_id = normalized["cohort_member_id"]
    if not isinstance(member_id, str) or not member_id:
        raise ValueError("Exp5 v3 execution schedule arm identity is invalid")
    for name in ("started_at", "ended_at"):
        value = normalized[name]
        if not isinstance(value, str) or not value:
            raise ValueError("Exp5 v3 execution schedule UTC window is invalid")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "Exp5 v3 execution schedule UTC window is invalid"
            ) from exc
    if normalized["started_at"] > normalized["ended_at"]:
        raise ValueError("Exp5 v3 execution schedule UTC window is reversed")
    for name in ("started_tick_ns", "ended_tick_ns"):
        if type(normalized[name]) is not int:
            raise ValueError("Exp5 v3 execution schedule ticks must be exact integers")
    if normalized["started_tick_ns"] >= normalized["ended_tick_ns"]:
        raise ValueError("Exp5 v3 execution schedule monotonic window is invalid")
    for name in ("provider_calls_made", "observed_peak"):
        if type(normalized[name]) is not int or normalized[name] < 1:
            raise ValueError("Exp5 v3 execution schedule arm counters are invalid")
    return normalized


def _validate_exp5_v3_execution_segment(
    segment: object,
    *,
    segment_index: int,
) -> dict[str, object]:
    required = {
        "segment_index",
        "started_at",
        "ended_at",
        "observed_member_order",
        "observed_peak",
        "provider_calls_made",
        "arm_windows",
        "segment_digest",
    }
    if not isinstance(segment, Mapping) or set(segment) != required:
        raise ValueError("Exp5 v3 execution schedule segment fields are invalid")
    if type(segment.get("segment_index")) is not int or segment[
        "segment_index"
    ] != segment_index:
        raise ValueError("Exp5 v3 execution schedule segment index is invalid")
    windows_value = segment.get("arm_windows")
    if not isinstance(windows_value, list) or not windows_value:
        raise ValueError("Exp5 v3 execution schedule segment windows are invalid")
    windows = [_validate_exp5_v3_arm_window(item) for item in windows_value]
    member_order = [str(item["cohort_member_id"]) for item in windows]
    provider_calls_made = sum(int(item["provider_calls_made"]) for item in windows)
    observed_peak = max(int(item["observed_peak"]) for item in windows)
    body: dict[str, object] = {
        "segment_index": segment_index,
        "started_at": windows[0]["started_at"],
        "ended_at": windows[-1]["ended_at"],
        "observed_member_order": member_order,
        "observed_peak": observed_peak,
        "provider_calls_made": provider_calls_made,
        "arm_windows": windows,
    }
    if segment.get("segment_digest") != digest_json(body) or json.loads(
        json.dumps(dict(segment))
    ) != {**body, "segment_digest": digest_json(body)}:
        raise ValueError("Exp5 v3 execution schedule segment evidence drift")
    return {**body, "segment_digest": digest_json(body)}


def _build_exp5_v3_schedule_evidence(
    *,
    schedule_binding: Mapping[str, object],
    execution_segments: Sequence[object],
) -> dict[str, object]:
    binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
    segments = [
        _validate_exp5_v3_execution_segment(item, segment_index=index)
        for index, item in enumerate(execution_segments)
    ]
    merged_windows: list[dict[str, object]] = []
    overlap_violation = False
    previous_window: dict[str, object] | None = None
    for segment in segments:
        for raw_window in segment["arm_windows"]:
            window = dict(raw_window)
            if previous_window is not None and int(window["started_tick_ns"]) < int(
                previous_window["ended_tick_ns"]
            ):
                overlap_violation = True
            previous_window = window
            if (
                merged_windows
                and merged_windows[-1]["cohort_member_id"]
                == window["cohort_member_id"]
            ):
                merged = merged_windows[-1]
                merged["ended_at"] = window["ended_at"]
                merged["ended_tick_ns"] = window["ended_tick_ns"]
                merged["provider_calls_made"] = int(
                    merged["provider_calls_made"]
                ) + int(window["provider_calls_made"])
                merged["observed_peak"] = max(
                    int(merged["observed_peak"]),
                    int(window["observed_peak"]),
                )
            else:
                merged_windows.append(window)
    observed_member_order = [
        str(window["cohort_member_id"]) for window in merged_windows
    ]
    expected_member_order = list(binding["expected_member_order"])
    prefix_valid = observed_member_order == expected_member_order[
        : len(observed_member_order)
    ]
    provider_calls_made = sum(
        int(segment["provider_calls_made"]) for segment in segments
    )
    observed_peak = max(
        (int(segment["observed_peak"]) for segment in segments),
        default=0,
    )
    limit_violation = observed_peak > int(binding["max_in_flight_global"])
    sequence_complete = observed_member_order == expected_member_order
    body: dict[str, object] = {
        "schema_version": "tokenshare.paper_exp5_v3_execution_schedule.v2",
        **binding,
        "observed_peak": observed_peak,
        "provider_calls_made": provider_calls_made,
        "observed_member_order": observed_member_order,
        "sequence_complete": sequence_complete,
        "sequence_matches": sequence_complete,
        "prefix_valid": prefix_valid,
        "arm_windows": merged_windows,
        "execution_segments": segments,
        "overlap_violation": overlap_violation,
        "limit_violation": limit_violation,
    }
    return {**body, "evidence_digest": digest_json(body)}


def _validate_exp5_v3_execution_schedule(
    evidence: object,
    *,
    schedule_binding: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(evidence, Mapping):
        raise ValueError("Exp5 v3 execution schedule must be a JSON object")
    expected_schema = "tokenshare.paper_exp5_v3_execution_schedule.v2"
    if evidence.get("schema_version") != expected_schema:
        raise ValueError("Exp5 v3 execution schedule schema is unsupported")
    binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
    for key, expected_value in binding.items():
        if evidence.get(key) != expected_value:
            raise ValueError(f"Exp5 v3 execution schedule binding drift: {key}")
    segments = evidence.get("execution_segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Exp5 v3 execution schedule segments are missing")
    canonical = _build_exp5_v3_schedule_evidence(
        schedule_binding=binding,
        execution_segments=segments,
    )
    if json.loads(json.dumps(dict(evidence))) != canonical:
        raise ValueError("Exp5 v3 execution schedule derived evidence drift")
    if (
        canonical["prefix_valid"] is not True
        or canonical["overlap_violation"] is True
        or canonical["limit_violation"] is True
    ):
        raise ValueError("Exp5 v3 execution schedule is not a valid prefix")
    return canonical


def _utc_now_string() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _paper_output_root(explicit_output_root: str | Path | None) -> Path:
    return resolve_experiment_output_root(explicit_output_root, "paper_v1")


def _configured_paper_output_boundary() -> Path:
    """返回 smoke 必须避开的正式 paper 容器；非法 override 不阻断显式路径。"""

    try:
        return _paper_output_root(None).resolve(strict=False)
    except ValueError:
        return (
            default_data_root() / "outputs" / "experiments" / "paper_v1"
        ).resolve(strict=False)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or run TokenShare paper real-AI experiments.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
    )
    parser.add_argument("--experiments", default="exp1")
    parser.add_argument("--worker-levels", default="10")
    parser.add_argument("--optional-worker-levels", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed-family", default="1")
    parser.add_argument("--real-transport", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--baseline-entry-id", default=None)
    parser.add_argument("--provider-attempt-limit", type=int, default=None)
    parser.add_argument("--token-limit", type=int, default=None)
    parser.add_argument("--cost-limit", type=float, default=None)
    parser.add_argument("--max-total-provider-attempts", type=int, default=None)
    parser.add_argument("--max-total-tokens", type=int, default=None)
    parser.add_argument("--max-cost-estimate", type=float, default=None)
    parser.add_argument("--stop-after-current-task", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--approve-budget-digest", default=None)
    parser.add_argument("--require-budget-approval", action="store_true")
    parser.add_argument("--unlimited-budget", action="store_true")
    parser.add_argument("--smoke-profile", default=None)
    parser.add_argument("--smoke-identity-only", action="store_true")
    parser.add_argument("--expect-smoke-execution-plan-digest", default=None)
    parser.add_argument("--expect-smoke-budget-digest", default=None)
    parser.add_argument("--exp1-pilot-profile", default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--ai-unit-id", default=None)
    parser.add_argument("--condition-id", default=None)
    parser.add_argument("--pilot-output-root", default=None)
    parser.add_argument(
        "--ai-api-config",
        default="benchmarks/paper/exp1_baseline_provider_config.v3.json",
    )
    parser.add_argument("--local-ai-api-config", default=None)
    parser.add_argument("--model-cohort-file", default=None)
    parser.add_argument("--model-entry-map", default=None)
    parser.add_argument("--exp5-smoke-evidence-bundle", default=None)
    parser.add_argument("--provider-config", action="append", default=[])
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    gate_c_transport=None,
    gate_c_ai_api_configs: dict | None = None,
) -> int:
    command_argv = tuple(sys.argv[1:] if argv is None else argv)
    if command_argv and command_argv[0] in PAPER_PIPELINE_COMMANDS:
        return run_paper_pipeline_main(command_argv)
    parser = _build_argument_parser()
    args = parser.parse_args(command_argv)
    args._command_argv = command_argv

    experiment_ids = normalize_experiment_ids(tuple(args.experiments.split(",")))
    try:
        validate_experiment_dependency_order(experiment_ids)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "invalid_experiment_dependency_order",
                    "message": str(error),
                    "experiment_ids": list(experiment_ids),
                    "provider_calls_made": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    if args.output_root is None:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "missing_output_root",
                    "message": (
                        "paper experiment commands require an explicit new "
                        "--output-root"
                    ),
                    "provider_calls_made": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    output_base = _paper_output_root(args.output_root)
    output_root = output_base
    pilot_blocked_output_root: Path | None = None
    unlimited_conflicts = (
        args.require_budget_approval
        or args.approve_budget_digest is not None
        or args.max_total_provider_attempts is not None
        or args.max_total_tokens is not None
        or args.max_cost_estimate is not None
    )
    if args.unlimited_budget and unlimited_conflicts:
        message = (
            "--unlimited-budget is mutually exclusive with budget approval "
            "and all --max-total-* limits"
        )
        if args.smoke_profile is not None:
            try:
                smoke_profile = load_paper_smoke_profile(Path(args.smoke_profile))
            except (OSError, ValueError, json.JSONDecodeError):
                _write_blocked_suite(
                    output_root=output_root,
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_budget_mode",
                    message=message,
                )
            else:
                _write_smoke_blocked_suite(
                    output_root=output_root,
                    profile=smoke_profile,
                    failure_kind="invalid_budget_mode",
                    message=message,
                )
        else:
            _write_blocked_suite(
                output_root=output_root,
                experiment_ids=experiment_ids,
                failure_kind="invalid_budget_mode",
                message=message,
            )
        return 3
    if args.smoke_profile is not None:
        return _run_smoke_cli(
            args=args,
            output_root=output_root,
            transport=gate_c_transport,
            supplied_ai_api_configs=gate_c_ai_api_configs,
        )
    selector_requested = any(
        value is not None
        for value in (args.condition_id, args.case_id, args.ai_unit_id)
    )
    profile_pilot_requested = args.exp1_pilot_profile is not None
    invalid_profile_selector = profile_pilot_requested and (
        (args.case_id is None) != (args.ai_unit_id is None)
        or args.condition_id is not None
    )
    invalid_general_selector = not profile_pilot_requested and selector_requested and (
        args.condition_id is None or args.case_id is None
    )
    if (
        invalid_profile_selector
        or invalid_general_selector
        or (selector_requested and not args.pilot)
        or (selector_requested and args.pilot_output_root is None)
        or (args.pilot_output_root is not None and not args.pilot)
    ):
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_selector",
            message=(
                "profile pilots require paired --case-id/--ai-unit-id; general "
                "Gate C pilots require --condition-id and --case-id with optional "
                "--ai-unit-id. Selectors require --pilot and an independent "
                "--pilot-output-root. --ai-unit-id selects the frozen runtime "
                "diagnostic scope inside the system coordinator"
            ),
        )
        return 3
    pilot_profile = _load_cli_pilot_profile(
        profile_path=args.exp1_pilot_profile,
        output_root=output_base,
        experiment_ids=experiment_ids,
    )
    if args.exp1_pilot_profile is not None and pilot_profile is None:
        return 3
    if pilot_profile is not None:
        if args.pilot:
            output_root = output_base / str(pilot_profile.body["suite_id"])
            pilot_blocked_output_root = output_base / (
                f"{pilot_profile.body['suite_id']}_blocked"
            )
        pilot_cli_error = _pilot_cli_profile_error(
            pilot_profile=pilot_profile,
            experiment_ids=experiment_ids,
            worker_levels=_parse_int_tuple(args.worker_levels),
            optional_worker_levels=_parse_int_tuple(args.optional_worker_levels),
            repeats=args.repeats,
            seed_family=_parse_int_tuple(args.seed_family),
        )
        if pilot_cli_error is not None:
            _write_blocked_suite(
                output_root=pilot_blocked_output_root or output_root,
                experiment_ids=experiment_ids,
                failure_kind="pilot_profile_mismatch",
                message=pilot_cli_error,
                suite_id=f"{pilot_profile.body['suite_id']}_blocked",
            )
            return 3
        if selector_requested:
            try:
                validate_exp1_selector_output_root(
                    output_base=output_base,
                    suite_id=str(pilot_profile.body["suite_id"]),
                    execution_output_root=Path(str(args.pilot_output_root)),
                )
            except ValueError as exc:
                _write_blocked_suite(
                    output_root=pilot_blocked_output_root or output_root,
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message=str(exc),
                    suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                )
                return 3
    if args.pilot and pilot_profile is None and not selector_requested:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_pilot_profile",
            message=(
                "general --pilot requires --condition-id, --case-id, and an "
                "independent --pilot-output-root"
            ),
        )
        return 3
    if args.pilot and args.plan_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_mode",
            message="--pilot cannot be combined with --plan-only",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if pilot_profile is not None and not args.plan_only and not args.pilot:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_mode",
            message="--exp1-pilot-profile execution requires --pilot",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (args.resume or args.replay_only) and args.plan_only:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only cannot be combined with --plan-only",
        )
        return 3
    if args.resume and args.replay_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only are mutually exclusive",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if args.pilot and pilot_profile is not None and args.baseline_entry_id is None:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_baseline_entry_id",
            message="--baseline-entry-id is required for Exp1 pilot execution",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (
        args.pilot
        and pilot_profile is not None
        and args.baseline_entry_id
        != pilot_profile.model_endpoint_identity.selected_entry_id
    ):
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="baseline_entry_mismatch",
            message=(
                "--baseline-entry-id does not match the approved Exp1 pilot entry"
            ),
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if args.replay_only and not args.pilot:
        try:
            execution_result = replay_paper_formal_suite(output_root=output_root)
            write_paper_formal_replay_report(output_root=output_root)
            metrics = recompute_paper_formal_metrics(output_root)
            report_result = generate_paper_formal_report(
                output_root=output_root,
                metrics=metrics,
                secret_values=(),
            )
            FormalEvidenceStore(output_root)._refresh_evidence_manifest()
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "formal_replay_failed",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _formal_execution_exit_code(execution_result, report_result)
    model_endpoint_cohort_preflight, model_cohort = (
        _model_endpoint_cohort_preflight_for_suite(
            experiment_ids=experiment_ids,
            model_cohort_file=args.model_cohort_file,
            model_entry_map=args.model_entry_map,
            provider_config_args=tuple(args.provider_config),
            smoke_evidence_bundle=args.exp5_smoke_evidence_bundle,
        )
    )

    if not args.plan_only and not args.real_transport and not args.replay_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_real_transport",
            message="formal paper runs require --real-transport",
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 1
    catalog_manifest = (
        _load_exp1_pilot_paper_catalogs()
        if pilot_profile is not None
        else _load_default_paper_catalogs()
    )
    lean_3x3_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=catalog_manifest,
    )
    dispatch_plans = ()
    frozen_selection_commitments = None
    if pilot_profile is None:
        planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
        try:
            suite_scale_profile = load_paper_suite_scale_profile(
                DEFAULT_PAPER_SUITE_SCALE_PROFILE
            )
            dispatch_plans = build_gate_c_dispatch_plans(
                catalog_manifest=catalog_manifest,
                lean_3x3_matrix=lean_3x3_matrix,
                experiment_ids=experiment_ids,
                baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
                paper_suite_scale_profile=suite_scale_profile,
                output_root=output_root,
            )
        except ValueError as exc:
            _write_blocked_suite(
                output_root=output_root,
                experiment_ids=experiment_ids,
                failure_kind="gate_c_plan_blocked",
                message=str(exc),
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            )
            return 3
        conditions = tuple(
            condition
            for dispatch_plan in dispatch_plans
            for condition in dispatch_plan.conditions
        )
        frozen_selection_commitments = [
            {
                **selection.to_dict(),
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
            }
            for dispatch_plan in dispatch_plans
            for condition, selection in dispatch_plan.bound_items()
        ]
    else:
        conditions = ()
    del model_cohort
    endpoint_token_ceilings = build_exp5_v3_token_ceiling_mapping(
        model_endpoint_cohort_preflight
    )
    model_policy_preflight = None
    try:
        budget = (
            plan_exp1_pilot(
                catalog_manifest=catalog_manifest,
                pilot_profile=pilot_profile,
                plan_only=args.plan_only,
                approve_budget_digest=args.approve_budget_digest,
                budget_approval_required=args.require_budget_approval,
                budget_mode="unlimited" if args.unlimited_budget else None,
            )
            if pilot_profile is not None
            else plan_paper_suite(
                catalog_manifest=catalog_manifest,
                conditions=conditions,
                max_provider_attempts_per_ai_unit=1,
                token_upper_bound_per_provider_attempt=(
                    FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
                ),
                token_upper_bound_by_endpoint_identity_digest=(
                    endpoint_token_ceilings
                ),
                cost_upper_bound_per_provider_attempt=(
                    FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
                ),
                plan_only=args.plan_only,
                lean_3x3_matrix=lean_3x3_matrix,
                model_policy_preflight=model_policy_preflight,
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
                frozen_selections=frozen_selection_commitments,
                endpoint_identity={
                    "baseline": _baseline_endpoint_binding(
                        load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
                    ),
                    "model_endpoint_cohort_preflight": (
                        model_endpoint_cohort_preflight
                    ),
                },
                request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
                suite_identity={
                    "suite_version": "paper_v1",
                    "execution_scope": "formal_matrix",
                    "experiment_ids": list(experiment_ids),
                    "paper_suite_scale_profile_source_path": (
                        suite_scale_profile.source_path
                    ),
                    "paper_suite_scale_profile_digest": (
                        suite_scale_profile.profile_digest
                    ),
                    "exp5_selection_path": (
                        suite_scale_profile.exp5_source_selection_path
                    ),
                    "exp5_selection_digest": (
                        suite_scale_profile.exp5_source_selection_digest
                    ),
                },
                output_identity={
                    "output_root": output_root.resolve(strict=False).as_posix(),
                    "per_experiment_roots": [
                        (output_root / experiment_id).resolve(
                            strict=False
                        ).as_posix()
                        for experiment_id in experiment_ids
                    ],
                    **_shared_exp1_reference_output_identity(experiment_ids),
                },
                approve_budget_digest=args.approve_budget_digest,
                budget_approval_required=args.require_budget_approval,
                hard_limits={} if args.unlimited_budget else None,
                budget_mode="unlimited" if args.unlimited_budget else None,
            )
        )
    except PaperBudgetApprovalError as exc:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_budget_approval"
            if args.approve_budget_digest is None
            else "budget_digest_mismatch",
            message=str(exc),
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 2

    effective_budget_digest = (
        args.approve_budget_digest or budget.budget_digest
    )

    if args.pilot:
        if pilot_profile is not None:
            if not (args.resume or args.replay_only):
                try:
                    _inject_exp1_pilot_api_key(
                        pilot_profile=pilot_profile,
                        local_config_path=Path(args.ai_api_config),
                    )
                except (OSError, ValueError) as exc:
                    _write_blocked_suite(
                        output_root=pilot_blocked_output_root or output_root,
                        experiment_ids=experiment_ids,
                        failure_kind="missing_real_api_key",
                        message=str(exc),
                        suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                    )
                    return 1
            try:
                execution_result = execute_exp1_pilot(
                    catalog_manifest=catalog_manifest,
                    pilot_profile=pilot_profile,
                    budget=budget,
                    approved_budget_digest=effective_budget_digest,
                    baseline_entry_id=str(args.baseline_entry_id),
                    output_base=output_base,
                    execution_output_root=(
                        Path(args.pilot_output_root)
                        if args.pilot_output_root is not None
                        else None
                    ),
                    real_transport=args.real_transport,
                    provider_attempt_limit=args.provider_attempt_limit,
                    token_limit=args.token_limit,
                    cost_limit=args.cost_limit,
                    resume=args.resume,
                    replay_only=args.replay_only,
                    case_id=args.case_id,
                    ai_unit_id=args.ai_unit_id,
                )
            except ValueError as exc:
                if not selector_requested:
                    raise
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message=str(exc),
                    suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                )
                return 3
        else:
            if len(experiment_ids) != 1:
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message="general Gate C pilot requires exactly one experiment",
                )
                return 3
            experiment_id = experiment_ids[0]
            try:
                validate_gate_c_pilot_output_root(
                    output_base=output_base,
                    experiment_id=experiment_id,
                    execution_output_root=Path(str(args.pilot_output_root)),
                )
                if gate_c_ai_api_configs is not None:
                    execution_configs = dict(gate_c_ai_api_configs)
                elif args.resume or args.replay_only:
                    execution_configs = {}
                elif experiment_id == "exp5_real_ai_model_endpoint_comparison":
                    execution_configs = load_provider_config_map(
                        _parse_provider_config_args(tuple(args.provider_config))
                    )
                else:
                    _inject_exp1_pilot_api_key(
                        pilot_profile=planning_profile,
                        local_config_path=Path(args.ai_api_config),
                    )
                    execution_configs = {
                        planning_profile.model_endpoint_identity.provider_config_id: (
                            planning_profile.source_provider_config
                        )
                    }
                execution_result = execute_gate_c_pilot_case(
                    catalog_manifest=catalog_manifest,
                    lean_3x3_matrix=lean_3x3_matrix,
                    experiment_id=experiment_id,
                    baseline_endpoint_binding=_baseline_endpoint_binding(
                        planning_profile
                    ),
                    model_endpoint_cohort_preflight=(
                        model_endpoint_cohort_preflight
                        if experiment_id
                        == "exp5_real_ai_model_endpoint_comparison"
                        else None
                    ),
                    budget=budget,
                    approved_budget_digest=effective_budget_digest,
                    condition_id=str(args.condition_id),
                    case_id=str(args.case_id),
                    ai_unit_id=args.ai_unit_id,
                    execution_output_root=Path(str(args.pilot_output_root)),
                    ai_api_configs=execution_configs,
                    transport=gate_c_transport,
                    real_transport=args.real_transport,
                    resume=args.resume,
                    replay_only=args.replay_only,
                )
            except (OSError, ValueError) as exc:
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_execution",
                    message=str(exc),
                )
                return 3
        output_root = Path(str(execution_result.to_dict()["output_root"]))
        _write_plan_artifacts(
            output_root=output_root,
            budget=budget.to_dict(),
            pilot_profile=(
                pilot_profile.to_dict() if pilot_profile is not None else None
            ),
            lean_3x3_matrix=lean_3x3_matrix,
        )
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _execution_result_exit_code(execution_result)

    if not args.plan_only:
        if gate_c_ai_api_configs is not None:
            execution_configs = dict(gate_c_ai_api_configs)
        elif args.replay_only:
            execution_configs = {}
        else:
            execution_configs = {}
            if any(
                experiment_id
                != "exp5_real_ai_model_endpoint_comparison"
                for experiment_id in experiment_ids
            ):
                _inject_exp1_pilot_api_key(
                    pilot_profile=planning_profile,
                    local_config_path=Path(args.ai_api_config),
                )
                execution_configs[
                    planning_profile.model_endpoint_identity.provider_config_id
                ] = planning_profile.source_provider_config
            if "exp5_real_ai_model_endpoint_comparison" in experiment_ids:
                execution_configs.update(
                    load_provider_config_map(
                        _parse_provider_config_args(tuple(args.provider_config))
                    )
                )
                execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY] = {
                    "exp5_real_ai_model_endpoint_comparison": (
                        model_endpoint_cohort_preflight
                    )
                }
        hard_limits = {
            key: value
            for key, value in (
                (
                    "max_total_provider_attempts",
                    args.max_total_provider_attempts,
                ),
                ("max_total_tokens", args.max_total_tokens),
                ("max_cost_estimate", args.max_cost_estimate),
                (
                    "stop_after_current_task",
                    True if args.stop_after_current_task else None,
                ),
            )
            if value is not None
        }
        budget_approval = dict(
            budget.quota_preflight.get(
                "budget_approval",
                {
                    "approval_mode": (
                        "digest_approved"
                        if args.require_budget_approval
                        else "user_bypassed"
                    ),
                    "budget_digest": budget.budget_digest,
                },
            )
        )
        budget_approval.setdefault("budget_digest", budget.budget_digest)
        try:
            execution_result = execute_paper_formal_suite(
                dispatch_plans=dispatch_plans,
                catalog_manifest=catalog_manifest,
                budget=budget,
                budget_approval=budget_approval,
                output_root=output_root,
                ai_api_configs=execution_configs,
                transport=gate_c_transport,
                real_transport=args.real_transport,
                hard_limits=hard_limits,
                resume=args.resume,
                replay_only=args.replay_only,
            )
        except PaperInfrastructureBlockedError as error:
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        **error.to_summary(),
                        "message": str(error),
                        "provider_calls_made": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 3
        budget_path = output_root / "run_budget.json"
        if not budget_path.is_file():
            budget_path.write_text(
                json.dumps(
                    budget.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        report_result = None
        suite_manifest_path = output_root / "suite_manifest.json"
        if not suite_manifest_path.is_file():
            _write_suite(output_root, execution_result)
        else:
            suite_manifest = json.loads(
                suite_manifest_path.read_text(encoding="utf-8")
            )
            if (
                suite_manifest.get("formal") is True
                and isinstance(
                    suite_manifest.get("traceability_replay_input_root_ref"),
                    Mapping,
                )
            ):
                write_paper_formal_replay_report(output_root=output_root)
                metrics = recompute_paper_formal_metrics(output_root)
                report_result = generate_paper_formal_report(
                    output_root=output_root,
                    metrics=metrics,
                    secret_values=_configured_secret_values(execution_configs),
                )
                FormalEvidenceStore(output_root)._refresh_evidence_manifest()
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _formal_execution_exit_code(execution_result, report_result)

    _write_plan_artifacts(
        output_root=output_root,
        budget=budget.to_dict(),
        pilot_profile=(
            pilot_profile.to_dict() if pilot_profile is not None else None
        ),
        lean_3x3_matrix=lean_3x3_matrix,
    )
    if dispatch_plans:
        (output_root / "paper_dispatch_plans.json").write_text(
            json.dumps(
                {
                    "schema_version": "tokenshare.paper_dispatch_plan_bundle.v1",
                    "provider_calls_made": 0,
                    "plans": [plan.to_dict() for plan in dispatch_plans],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if model_policy_preflight is not None:
        (output_root / "model_policy_plan.json").write_text(
            json.dumps(
                model_policy_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if model_endpoint_cohort_preflight is not None:
        (output_root / "model_endpoint_cohort_plan.json").write_text(
            json.dumps(
                model_endpoint_cohort_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    has_planned_dispatch = any(
        plan.status == "planned" and bool(plan.conditions)
        for plan in dispatch_plans
    )
    suite_status = (
        PaperStatus.BLOCKED
        if (
            model_endpoint_cohort_preflight is not None
            and model_endpoint_cohort_preflight.get("status") == "blocked"
            and not has_planned_dispatch
        )
        else PaperStatus.PLANNED
    )
    suite = PaperSuiteResult(
        suite_id=(
            str(pilot_profile.body["suite_id"])
            if pilot_profile is not None
            else "paper_v1_plan"
        ),
        status=suite_status,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=budget.planned_conditions,
        run_count=budget.planned_root_runs,
        task_count=budget.planned_root_runs,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref={"path": "audit/paper_eligibility_report.json"},
        budget_ref=budget.to_dict(),
        metrics_refs=[],
        audit_refs=[
            {"path": "lean_3x3_matrix.json"},
            *(
                [{"path": "exp1_pilot_profile.json"}]
                if pilot_profile is not None
                else []
            ),
            *(
                [{"path": "model_policy_plan.json"}]
                if model_policy_preflight is not None
                else []
            ),
            *(
                [{"path": "model_endpoint_cohort_plan.json"}]
                if model_endpoint_cohort_preflight is not None
                else []
            ),
        ],
        error_summary=[],
        model_policy_preflight=model_policy_preflight,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _baseline_endpoint_binding(profile: Exp1PilotProfile) -> dict:
    identity = profile.model_endpoint_identity.to_dict()
    request_controls = dict(EXP1_FORMAL_REQUEST_CONTROLS)
    return {
        **identity,
        "model_entry_id": identity["selected_entry_id"],
        "request_controls": request_controls,
    }


def _is_exp1_exp4_only_smoke(profile: object) -> bool:
    return tuple(profile.experiment_ids) in {
        EXP1_EXP4_ONLY_EXPERIMENT_IDS,
        EXP3_EXP4_ONLY_EXPERIMENT_IDS,
    }


def _explicit_ai_api_config_path(command_argv: Sequence[str]) -> Path | None:
    """返回 CLI 显式给出的 provider config；缺省值不能充当启动授权。"""

    for index, argument in enumerate(command_argv):
        if argument == "--ai-api-config":
            if index + 1 >= len(command_argv):
                return None
            return Path(command_argv[index + 1])
        if argument.startswith("--ai-api-config="):
            return Path(argument.split("=", 1)[1])
    return None


def _validate_exp1_exp4_v3_config_path(args: argparse.Namespace) -> None:
    selected_path = _explicit_ai_api_config_path(args._command_argv)
    if selected_path is None:
        raise ValueError(
            "Exp1-4 smoke execution requires explicit --ai-api-config "
            "benchmarks/paper/exp1_baseline_provider_config.v3.json"
        )
    required_path = EXP1_EXP4_REQUIRED_PROVIDER_CONFIG.resolve(strict=False)
    if selected_path.resolve(strict=False) != required_path:
        raise ValueError(
            "Exp1-4 smoke execution requires the registered v3 provider config path"
        )


def _validate_exp1_exp4_v3_execution_config(config: object) -> None:
    """在 provider dispatch 前冻结 Exp1-4 DeepSeek v3 的完整语义。"""

    if config is None or not hasattr(config, "config_digest"):
        raise ValueError("Exp1-4 v3 baseline provider config is missing")
    if config.config_digest != EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_DIGEST:
        raise ValueError("Exp1-4 v3 baseline provider config digest mismatch")
    if config.provider_family != "deepseek":
        raise ValueError("Exp1-4 v3 baseline provider must be official DeepSeek")
    expected_defaults = {
        "timeout_seconds": 600,
        "max_tokens": 300_000,
        "stream": False,
        "max_provider_attempts": 1,
    }
    if config.defaults != expected_defaults:
        raise ValueError("Exp1-4 v3 baseline request defaults mismatch")
    enabled_entries = [entry for entry in config.entries if entry.enabled]
    if len(config.entries) != 1 or len(enabled_entries) != 1:
        raise ValueError("Exp1-4 v3 baseline must contain one enabled entry")
    entry = enabled_entries[0]
    expected_entry_fields = {
        "entry_id": EXP1_EXP4_REQUIRED_ENTRY_ID,
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-pro",
        "endpoint": "/chat/completions",
        "supports_json_mode": True,
        "supports_streaming": False,
        "request_overrides": {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    }
    actual_entry_fields = {
        field_name: getattr(entry, field_name)
        for field_name in expected_entry_fields
    }
    if actual_entry_fields != expected_entry_fields:
        raise ValueError("Exp1-4 v3 baseline endpoint or reasoning controls mismatch")
    if config.local_concurrency != {"max_in_flight_global": 50}:
        raise ValueError("Exp1-4 v3 baseline provider inflight limit mismatch")


def _exp5_v3_smoke_execution_transport(
    *,
    profile: object,
    execution_plan: object,
    model_endpoint_cohort_preflight: Mapping[str, object] | None,
    transport: object | None,
    ai_api_configs: Mapping[str, object] | None = None,
    prior_evidence: Mapping[str, object] | None = None,
) -> tuple[object | None, _Exp5V3SharedProviderTransport | None]:
    contract = getattr(profile, "exp5_v3_contract", None)
    if not isinstance(contract, Mapping):
        return transport, None
    if (
        not isinstance(model_endpoint_cohort_preflight, Mapping)
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        raise ValueError(
            "Exp5 v3 shared provider limiter requires a planned preflight"
        )
    repeat_member_order = contract.get("repeat_member_order")
    if not isinstance(repeat_member_order, Mapping):
        raise ValueError("Exp5 v3 smoke repeat member order is missing")
    expected_member_order = repeat_member_order.get("0")
    if not isinstance(expected_member_order, list) or len(
        expected_member_order
    ) != 4:
        raise ValueError("Exp5 v3 smoke repeat-0 member order is invalid")
    member_plans = model_endpoint_cohort_preflight.get("member_plans")
    if not isinstance(member_plans, Mapping) or set(member_plans) != set(
        expected_member_order
    ):
        raise ValueError("Exp5 v3 shared provider member inventory drift")
    provider_namespace = str(contract["provider_config_id"])
    member_id_by_entry_id: dict[str, str] = {}
    member_id_by_model: dict[str, str] = {}
    exp5_provider_family_by_entry_id: dict[str, str] = {}
    member_provider_config_ids: set[str] = set()
    for member_id in expected_member_order:
        member_plan = member_plans[member_id]
        member_provider_config_id = (
            member_plan.get("provider_config_id")
            if isinstance(member_plan, Mapping)
            else None
        )
        if (
            not isinstance(member_plan, Mapping)
            or member_plan.get("status") != "planned"
            or member_plan.get("provider_family") != "siliconflow"
            or not isinstance(member_provider_config_id, str)
            or not member_provider_config_id
        ):
            raise ValueError(
                "Exp5 v3 shared provider member plan is not executable"
            )
        member_provider_config_ids.add(member_provider_config_id)
        entry_id = member_plan.get("selected_entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("Exp5 v3 shared provider entry identity is missing")
        if entry_id in member_id_by_entry_id:
            raise ValueError("Exp5 v3 shared provider entry identities must be unique")
        member_id_by_entry_id[entry_id] = str(member_id)
        provider_model_id = member_plan.get("provider_model_id")
        if not isinstance(provider_model_id, str) or not provider_model_id:
            raise ValueError("Exp5 v3 shared provider model identity is missing")
        member_id_by_model[provider_model_id] = str(member_id)
        exp5_provider_family_by_entry_id[entry_id] = str(
            member_plan["provider_family"]
        )
    if len(member_provider_config_ids) != 1:
        raise ValueError("Exp5 v3 member provider config namespace drift")

    if transport is None:
        if not isinstance(ai_api_configs, Mapping):
            raise ValueError(
                "default provider router requires approved execution configs"
            )
        provider_family_by_entry_id = (
            _provider_family_bindings_from_execution_configs(ai_api_configs)
        )
        if any(
            provider_family_by_entry_id.get(entry_id) != provider_family
            for entry_id, provider_family in exp5_provider_family_by_entry_id.items()
        ):
            raise ValueError("Exp5 v3 routed provider binding drift")
        delegate = _ProviderFamilyTransportRouter(
            provider_family_by_entry_id=provider_family_by_entry_id
        )
    else:
        delegate = transport
    schedule_binding = _exp5_v3_execution_schedule_binding(
        profile=profile,
        execution_plan=execution_plan,
    )
    limiter = _Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        member_id_by_model=member_id_by_model,
        schedule_binding=schedule_binding,
        prior_evidence=prior_evidence,
    )
    return limiter, limiter


def _exp5_v3_execution_schedule_binding(
    *,
    profile: object,
    execution_plan: object,
) -> dict[str, object]:
    contract = getattr(profile, "exp5_v3_contract", None)
    if not isinstance(contract, Mapping):
        raise ValueError("Exp5 v3 execution schedule contract is missing")
    repeat_member_order = contract.get("repeat_member_order")
    expected_member_order = (
        repeat_member_order.get("0")
        if isinstance(repeat_member_order, Mapping)
        else None
    )
    if not isinstance(expected_member_order, list):
        raise ValueError("Exp5 v3 execution schedule member order is missing")
    plans = [
        plan
        for plan in execution_plan.dispatch_plans
        if plan.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    ]
    if len(plans) != 1:
        raise ValueError("Exp5 v3 execution schedule dispatch plan is ambiguous")
    conditions = tuple(plans[0].conditions)
    expected_condition_members = [
        str(member_id)
        for member_id in expected_member_order
        for _repeat_index in range(2)
    ]
    actual_condition_members = [
        str(condition.cohort_member_id) for condition in conditions
    ]
    if actual_condition_members != expected_condition_members:
        raise ValueError("Exp5 v3 execution schedule condition order drift")
    return _normalize_exp5_v3_schedule_binding(
        {
            "profile_digest": str(profile.profile_digest),
            "execution_plan_digest": str(execution_plan.execution_plan_digest),
            "sequence_plan_digest": str(contract["sequence_plan_digest"]),
            "provider_namespace": str(contract["provider_config_id"]),
            "max_in_flight_global": contract["max_in_flight_global"],
            "expected_member_order": [
                str(member_id) for member_id in expected_member_order
            ],
            "expected_condition_ids": [
                str(condition.condition_id) for condition in conditions
            ],
        }
    )


def _load_exp5_v3_execution_schedule(
    *,
    output_root: Path,
    schedule_binding: Mapping[str, object],
) -> dict[str, object] | None:
    path = output_root / EXP5_V3_EXECUTION_SCHEDULE_PATH
    if not path.is_file():
        return None
    evidence = json.loads(path.read_text(encoding="utf-8"))
    return _validate_exp5_v3_execution_schedule(
        evidence,
        schedule_binding=schedule_binding,
    )


def _write_exp5_v3_execution_schedule(
    *,
    output_root: Path,
    limiter: _Exp5V3SharedProviderTransport,
) -> dict[str, object]:
    if limiter.new_provider_calls_made == 0:
        prior_evidence = limiter.prior_evidence
        if prior_evidence is None:
            raise ValueError("Exp5 v3 execution schedule has no provider calls")
        return prior_evidence
    evidence = limiter.execution_evidence()
    evidence = _validate_exp5_v3_execution_schedule(
        evidence,
        schedule_binding=limiter._schedule_binding,
    )
    path = output_root / EXP5_V3_EXECUTION_SCHEDULE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    FormalEvidenceStore(output_root)._refresh_evidence_manifest()
    return evidence


def _with_exp2_regression_smoke_lean_plan(
    *,
    dispatch_plans: Sequence[object],
    profile: object,
    catalog_manifest: PaperInputCatalogManifest,
) -> tuple[object, ...]:
    """在明确的非论文 matrix8 smoke 中冻结 Exp1 同题集 planning。"""

    smoke_items = tuple(
        item
        for item in getattr(profile, "items", ())
        if item.experiment_id == "exp2_real_ai_scalability"
    )
    if not smoke_items:
        return tuple(dispatch_plans)
    if not (
        getattr(profile, "formal", None) is False
        and getattr(profile, "pilot_only", None) is True
        and getattr(profile, "regression_only", None) is True
        and getattr(profile, "paper_eligible", None) is False
    ):
        raise ValueError("Exp2 matrix8 planning is restricted to regression smoke")
    matches = tuple(
        plan
        for plan in dispatch_plans
        if plan.experiment_id == "exp2_real_ai_scalability"
    )
    if len(matches) != 1:
        raise ValueError("Exp2 regression smoke requires one canonical Exp2 plan")
    exp2_plan = matches[0]
    if not exp2_plan.conditions or exp2_plan.catalog_execution_view is None:
        raise ValueError("Exp2 regression smoke canonical plan is incomplete")
    catalog_view = restore_catalog_execution_view(
        exp2_plan.catalog_execution_view,
        catalog_manifest=catalog_manifest,
    )
    lean_items = tuple(
        item
        for item in smoke_items
        if item.condition_selector.get("domain") == "lean_proof"
    )
    requests = tuple(
        {
            **dict(item.condition_selector),
            "repeat_id": item.repeat_id,
        }
        for item in lean_items
    )
    derived = build_exp2_regression_smoke_lean_bindings(
        catalog=catalog_view,
        baseline_condition=exp2_plan.conditions[0],
        requests=requests,
    )
    cases_by_id = {
        str(case["case_id"]): case
        for case in catalog_manifest.factorization_cases
    }
    factor_replacements: dict[
        str, tuple[object, object]
    ] = {}
    for item in smoke_items:
        if item.condition_selector.get("domain") != "factorization":
            continue
        matches = tuple(
            condition
            for condition in exp2_plan.conditions
            if condition.domain == "factorization"
            and condition.worker_count == item.condition_selector.get("worker_count")
            and condition.repeat_id == item.repeat_id
        )
        if len(matches) != 1:
            raise ValueError("Exp2 regression smoke Factor condition is ambiguous")
        case = cases_by_id.get(item.case_id)
        if case is None:
            raise ValueError("Exp2 regression smoke Factor case is missing")
        factor_replacements[matches[0].condition_id] = (
            build_exp2_regression_smoke_factor_binding(
                formal_condition=matches[0],
                case_id=item.case_id,
                catalog_version=catalog_manifest.catalog_version,
            )
        )
    replacement_conditions = {
        condition_id: pair[0]
        for condition_id, pair in factor_replacements.items()
    }
    replacement_bindings = {
        condition_id: pair[1]
        for condition_id, pair in factor_replacements.items()
    }
    augmented = replace(
        exp2_plan,
        conditions=tuple(
            replacement_conditions.get(condition.condition_id, condition)
            for condition in exp2_plan.conditions
        ) + tuple(item[0] for item in derived),
        condition_selection_bindings=(
            tuple(
                replacement_bindings.get(binding.condition_id, binding)
                for binding in exp2_plan.condition_selection_bindings
            )
            + tuple(item[1] for item in derived)
        ),
        paper_eligible_possible=False,
    )
    return tuple(
        augmented if plan is exp2_plan else plan for plan in dispatch_plans
    )


def _with_exp3_regression_smoke_lean_plan(
    *,
    dispatch_plans: Sequence[object],
    profile: object,
    catalog_manifest: PaperInputCatalogManifest,
) -> tuple[object, ...]:
    """为 Exp3 matrix8 冻结 Exp1 同题集的单题 fault/recovery 绑定。"""

    seam_items = tuple(
        item
        for item in getattr(profile, "items", ())
        if item.experiment_id == "exp3_real_ai_fault_recovery"
    )
    if not seam_items:
        return tuple(dispatch_plans)
    if not (
        getattr(profile, "formal", None) is False
        and getattr(profile, "pilot_only", None) is True
        and getattr(profile, "regression_only", None) is True
        and getattr(profile, "paper_eligible", None) is False
    ):
        raise ValueError("Exp3 matrix8 planning is restricted to regression smoke")
    plans = tuple(
        plan
        for plan in dispatch_plans
        if plan.experiment_id == "exp3_real_ai_fault_recovery"
    )
    if len(plans) != 1:
        raise ValueError("Exp3 regression smoke requires one canonical Exp3 plan")
    exp3_plan = plans[0]
    if exp3_plan.catalog_execution_view is None:
        raise ValueError("Exp3 regression smoke canonical plan is incomplete")
    catalog_view = restore_catalog_execution_view(
        exp3_plan.catalog_execution_view,
        catalog_manifest=catalog_manifest,
    )
    if catalog_view.view_kind != EXP3_VIEW_KIND:
        raise ValueError("Exp3 regression smoke catalog view kind drift")
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    replacements: dict[str, tuple[object, object]] = {}
    for item in seam_items:
        selector = item.condition_selector
        matches = tuple(
            condition
            for condition in exp3_plan.conditions
            if all(
                getattr(condition, field_name, None) == expected
                for field_name, expected in selector.items()
                if field_name
                not in {"matrix_kind", "dead_worker_count", "kill_progress_percent"}
            )
            and condition.repeat_id == item.repeat_id
            and (
                selector.get("matrix_kind") != "worker_death"
                or (
                    f"__dead{selector.get('dead_worker_count')}__"
                    f"p{selector.get('kill_progress_percent')}__"
                )
                in condition.condition_id
            )
        )
        if len(matches) != 1:
            raise ValueError(
                "Exp3 regression smoke condition is ambiguous: "
                f"{item.item_id} matched "
                f"{tuple(condition.condition_id for condition in matches)!r}"
            )
        case = cases_by_id.get(item.case_id)
        if case is None:
            raise ValueError("Exp3 regression smoke case is missing")
        replacements[matches[0].condition_id] = build_exp3_regression_smoke_binding(
            formal_condition=matches[0],
            case_id=item.case_id,
            expected_ai_unit_count=estimated_ai_units_for_case(case),
            catalog_version=catalog_manifest.catalog_version,
        )
    view_body = json.loads(json.dumps(catalog_view.view_body))
    ai_units_by_case_id = dict(view_body.get("ai_units_by_case_id", {}))
    for item in seam_items:
        ai_units_by_case_id[item.case_id] = list(
            _smoke_case_planned_ai_unit_ids(cases_by_id[item.case_id])
        )
    view_body["ai_units_by_case_id"] = ai_units_by_case_id
    augmented_view = build_prepared_mapping_execution_view(
        view_kind=EXP3_VIEW_KIND,
        catalog_manifest_digest=catalog_manifest.catalog_digest,
        view_body=view_body,
    )
    augmented = replace(
        exp3_plan,
        conditions=tuple(
            replacements.get(current.condition_id, (current, None))[0]
            for current in exp3_plan.conditions
        ),
        condition_selection_bindings=tuple(
            replacements.get(current.condition_id, (None, current))[1]
            for current in exp3_plan.condition_selection_bindings
        ),
        catalog_execution_view=augmented_view.to_dict(),
        paper_eligible_possible=False,
    )
    return tuple(
        augmented if plan is exp3_plan else plan for plan in dispatch_plans
    )


def _with_matrix8_unified_factor_seed_execution_plan(
    *,
    execution_plan: object,
    profile: object,
) -> object:
    """仅在 Exp1--4 matrix8 clone 中按 root 冻结共享 Factor split seed。"""

    if not (
        getattr(profile, "formal", None) is False
        and getattr(profile, "pilot_only", None) is True
        and getattr(profile, "regression_only", None) is True
        and getattr(profile, "paper_eligible", None) is False
        and getattr(profile, "expected_root_runs", None) == 8
    ):
        raise ValueError("matrix8 unified seed is restricted to regression smoke")
    experiment_ids = tuple(getattr(execution_plan, "experiment_ids", ()))
    if len(experiment_ids) != 1 or experiment_ids[0] not in EXP1_EXP4_ONLY_EXPERIMENT_IDS:
        raise ValueError("matrix8 unified seed requires one Exp1--4 experiment")
    profile_items = {
        item.item_id: item for item in tuple(getattr(profile, "items", ()))
    }
    resolved_items = tuple(getattr(execution_plan, "items", ()))
    factor_items_by_condition: dict[str, list[object]] = {}
    for resolved in resolved_items:
        source = profile_items.get(resolved.item_id)
        if source is None or source.case_id != resolved.case_id:
            raise ValueError("matrix8 unified seed profile/execution item drift")
        if source.condition_selector.get("domain") != "factorization":
            continue
        factor_items_by_condition.setdefault(resolved.condition_id, []).append(resolved)
    dispatch_plans = tuple(getattr(execution_plan, "dispatch_plans", ()))
    if len(dispatch_plans) != 1 or dispatch_plans[0].paper_eligible_possible:
        raise ValueError("matrix8 unified seed dispatch classification drift")
    dispatch_plan = dispatch_plans[0]
    replacement_by_item_id: dict[str, object] = {}
    conditions: list[object] = []
    bindings: list[object] = []
    for condition, selection in dispatch_plan.bound_items():
        factor_items = tuple(
            factor_items_by_condition.get(condition.condition_id, ())
        )
        if not factor_items:
            conditions.append(condition)
            bindings.append(
                FrozenConditionSelectionBinding.from_condition(
                    condition,
                    selection,
                )
            )
            continue
        for item in factor_items:
            condition_id = condition.condition_id
            if len(factor_items) > 1:
                suffix = digest_json(
                    {
                        "schema_version": "tokenshare.matrix8_factor_condition_clone.v1",
                        "condition_id": condition.condition_id,
                        "case_id": item.case_id,
                    }
                ).removeprefix("sha256:")[:12]
                condition_id = f"{condition.condition_id}__matrix8_{suffix}"
            cloned = replace(
                condition,
                condition_id=condition_id,
                seed=_matrix8_unified_factor_seed(item.case_id),
            )
            conditions.append(cloned)
            bindings.append(
                FrozenConditionSelectionBinding.from_condition(cloned, selection)
            )
            replacement_by_item_id[item.item_id] = cloned
    augmented_dispatch = replace(
        dispatch_plan,
        conditions=tuple(conditions),
        condition_selection_bindings=tuple(bindings),
    )
    augmented_items = tuple(
        replace(
            item,
            condition_id=replacement_by_item_id[item.item_id].condition_id,
            condition_digest=replacement_by_item_id[item.item_id].condition_digest,
        )
        if item.item_id in replacement_by_item_id
        else item
        for item in resolved_items
    )
    root_case_filter: dict[str, tuple[str, ...]] = {}
    for item in augmented_items:
        root_case_filter[item.condition_id] = (
            *root_case_filter.get(item.condition_id, ()),
            item.case_id,
        )
    return replace(
        execution_plan,
        items=augmented_items,
        dispatch_plans=(augmented_dispatch,),
        root_case_filter=root_case_filter,
    )


def _matrix8_unified_factor_seed(case_id: str) -> int:
    digest = digest_json(
        {
            "schema_version": "tokenshare.matrix8_unified_factor_seed.v1",
            "case_id": case_id,
        }
    )
    return int(digest.removeprefix("sha256:")[:8], 16)


def build_results_first_matrix8_planning_context(
    *,
    output_root: str | Path,
) -> Matrix8SmokePlanningContext:
    """复算四份 canonical matrix8 smoke plan；不读取 secret、不调用 provider。"""

    root = Path(output_root).resolve(strict=False)
    catalog = _load_default_paper_catalogs()
    lean_matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
    scale_profile = load_paper_suite_scale_profile(
        DEFAULT_PAPER_SUITE_SCALE_PROFILE
    )
    profiles: list[object] = []
    execution_plans: list[object] = []
    for number, profile_path in enumerate(MATRIX8_SMOKE_PROFILE_PATHS, start=1):
        profile = load_paper_smoke_profile(profile_path)
        dispatch = build_gate_c_dispatch_plans(
            catalog_manifest=catalog,
            lean_3x3_matrix=lean_matrix,
            experiment_ids=profile.experiment_ids,
            baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
            model_endpoint_cohort_preflight=None,
            paper_suite_scale_profile=scale_profile,
            output_root=root / f"canonical-exp{number}",
        )
        dispatch = _with_exp2_regression_smoke_lean_plan(
            dispatch_plans=dispatch,
            profile=profile,
            catalog_manifest=catalog,
        )
        dispatch = _with_exp3_regression_smoke_lean_plan(
            dispatch_plans=dispatch,
            profile=profile,
            catalog_manifest=catalog,
        )
        execution = resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=dispatch,
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.catalog_version,
            catalog_digest=catalog.catalog_digest,
            output_root=root / f"exp{number}",
        )
        execution = _with_matrix8_unified_factor_seed_execution_plan(
            execution_plan=execution,
            profile=profile,
        )
        profiles.append(profile)
        execution_plans.append(execution)
    return Matrix8SmokePlanningContext(
        catalog_manifest=catalog,
        profiles=tuple(profiles),
        execution_plans=tuple(execution_plans),
        ai_api_config=planning_profile.source_provider_config,
    )


def _build_results_first_matrix8_acquisition_plan(
    *, planning_root: str | Path
):
    from tokenshare.experiments.paper_response_bank import (
        build_matrix8_unified_acquisition_plan,
    )

    planning_root = Path(planning_root).resolve(strict=False)
    context = build_results_first_matrix8_planning_context(
        output_root=planning_root
    )
    entry = next(
        item
        for item in context.ai_api_config.entries
        if item.entry_id == EXP1_EXP4_REQUIRED_ENTRY_ID and item.enabled
    )
    pricing = entry.pricing
    return build_matrix8_unified_acquisition_plan(
        execution_plans=context.execution_plans,
        catalog_manifest=context.catalog_manifest,
        ai_api_config=context.ai_api_config,
        entry_id=EXP1_EXP4_REQUIRED_ENTRY_ID,
        planning_artifact_root=planning_root / "prepared",
        requested_at="2026-08-09T00:00:00Z",
        token_upper_bound=FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT,
        cost_upper_bound=Decimal(
            str(FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT)
        ),
        frozen_pricing=FrozenPricing(
            currency=str(pricing["currency"]),
            input_per_million_tokens=Decimal(
                str(pricing["uncached_input_per_million_tokens"])
            ),
            output_per_million_tokens=Decimal(
                str(pricing["output_per_million_tokens"])
            ),
        ),
    )


def _results_first_matrix8_authorized_plan_digest(plan: object) -> str:
    return digest_json(
        {
            "schema_version": "tokenshare.results_first_matrix8_acquisition_plan.v1",
            "combined_profile_digest": plan.combined_profile_digest,
            "inventory_digest": plan.semantic_inventory_plan.inventory_digest,
            "condition_candidate_count": plan.condition_candidate_count,
        }
    )


def create_results_first_matrix8_acquisition_bundle(
    *,
    pipeline_profile_digest: str,
    bundle_root: str | Path,
):
    """从四份 matrix8 profile 写入 fresh 166-entry acquisition bundle。"""

    from tokenshare.experiments.paper_response_bank import (
        create_acquisition_plan_bundle,
    )

    root = Path(bundle_root).resolve()
    planning_root = root.with_name(f"{root.name}.planning_artifacts")
    if planning_root.exists():
        raise FileExistsError(
            f"matrix8 planning artifact root already exists: {planning_root}"
        )
    plan = _build_results_first_matrix8_acquisition_plan(
        planning_root=planning_root
    )
    bundle = create_acquisition_plan_bundle(
        root,
        authorized_plan_digest=_results_first_matrix8_authorized_plan_digest(plan),
        profile_digest=pipeline_profile_digest,
        semantic_inventory_plan=plan.semantic_inventory_plan,
        acquisition_requests=plan.acquisition_requests,
    )
    return bundle


def validate_results_first_matrix8_acquisition_bundle(
    *,
    bundle: object,
    bundle_root: str | Path,
):
    """重建 official matrix8 plan，拒绝仅内部自洽的替代 bundle。"""

    from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

    if type(bundle) is not AcquisitionPlanBundle:
        raise ValueError("results-first acquisition requires a v1 plan bundle")
    bundle.validate()
    if not Path(bundle_root).resolve().is_dir():
        raise ValueError("results-first acquisition bundle root is missing")
    with tempfile.TemporaryDirectory(
        prefix="tokenshare-matrix8-official-validation-"
    ) as raw:
        expected = _build_results_first_matrix8_acquisition_plan(
            planning_root=Path(raw)
        )
    if len(expected.acquisition_requests) != 166:
        raise ValueError("official matrix8 acquisition plan must contain 166 entries")
    expected_plan_digest = _results_first_matrix8_authorized_plan_digest(expected)
    if bundle.authorized_plan_digest != expected_plan_digest:
        raise ValueError("results-first bundle official plan digest mismatch")
    if (
        bundle.semantic_inventory_plan != expected.semantic_inventory_plan
        or bundle.inventory_rows != expected.semantic_inventory_plan.rows
        or bundle.inventory_digest
        != expected.semantic_inventory_plan.inventory_digest
    ):
        raise ValueError("results-first bundle official inventory mismatch")
    if bundle.acquisition_requests != expected.acquisition_requests:
        raise ValueError("results-first bundle official acquisition requests mismatch")
    if len(bundle.inventory_rows) != 166:
        raise ValueError("results-first bundle must contain 166 inventory entries")
    expected_provider_digests = {
        row.provider_config_digest for row in expected.semantic_inventory_plan.rows
    }
    actual_provider_digests = {
        row.provider_config_digest for row in bundle.inventory_rows
    }
    if (
        expected_provider_digests != actual_provider_digests
        or bundle.provider_config_digest not in expected_provider_digests
        or any(
            request.provider_family != "deepseek"
            or request.prepared_request.entry_id != EXP1_EXP4_REQUIRED_ENTRY_ID
            or request.prepared_request.configured_model != "deepseek-v4-pro"
            for request in bundle.acquisition_requests
        )
    ):
        raise ValueError("results-first bundle official provider identity mismatch")
    return bundle


class _ResultsFirstNoProviderTransport:
    def send(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("results-first trace attempted a current provider call")


def build_results_first_matrix8_trace_batches(
    *,
    output_root: str | Path,
    trace_context: object,
) -> tuple[dict[str, object], ...]:
    """构造 Exp1--4 四份 8-root trace smoke；不执行 provider。"""

    root = Path(output_root).resolve(strict=False)
    context = build_results_first_matrix8_planning_context(output_root=root)
    lean_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=context.catalog_manifest
    )
    planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
    scale_profile = load_paper_suite_scale_profile(
        DEFAULT_PAPER_SUITE_SCALE_PROFILE
    )
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            context.catalog_manifest.factorization_cases
            + context.catalog_manifest.lean_cases
            + context.catalog_manifest.lean_lemma_graph_cases
        )
    }
    execution_configs = {
        planning_profile.model_endpoint_identity.provider_config_id: (
            context.ai_api_config
        )
    }
    batches: list[dict[str, object]] = []
    for number, (profile, execution_plan) in enumerate(
        zip(context.profiles, context.execution_plans),
        start=1,
    ):
        if (
            profile.expected_root_runs != 8
            or len(execution_plan.items) != 8
            or execution_plan.experiment_ids != profile.experiment_ids
        ):
            raise ValueError("results-first trace requires exact matrix8 plans")
        expected_ai_units_by_case = {
            item.case_id: estimated_ai_units_for_case(cases_by_id[item.case_id])
            for item in execution_plan.items
        }
        frozen_selections = execution_plan.budget_selection_commitments(
            expected_ai_units_by_case=expected_ai_units_by_case
        )
        budget = plan_paper_suite(
            catalog_manifest=context.catalog_manifest,
            conditions=tuple(
                condition
                for plan in execution_plan.dispatch_plans
                for condition in plan.conditions
            ),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=(
                FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            cost_upper_bound_per_provider_attempt=(
                FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            plan_only=True,
            lean_3x3_matrix=lean_matrix,
            model_endpoint_cohort_preflight=None,
            frozen_selections=frozen_selections,
            endpoint_identity={
                "baseline": _baseline_endpoint_binding(planning_profile),
                "model_endpoint_cohort_preflight": None,
            },
            request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
            suite_identity={
                "suite_version": "paper_matrix8_results_first_v1",
                "execution_scope": "smoke_suite",
                "profile_digest": profile.profile_digest,
                "execution_plan_digest": execution_plan.execution_plan_digest,
                "experiment_ids": list(profile.experiment_ids),
                "paper_suite_scale_profile_source_path": scale_profile.source_path,
                "paper_suite_scale_profile_digest": scale_profile.profile_digest,
                "exp5_selection_path": scale_profile.exp5_source_selection_path,
                "exp5_selection_digest": scale_profile.exp5_source_selection_digest,
            },
            output_identity={
                "output_root": Path(execution_plan.output_root).as_posix(),
                "formal_output_root_allowed": False,
                **_shared_exp1_reference_output_identity(
                    profile.experiment_ids,
                    baseline_policy=profile.baseline_policy,
                ),
            },
            budget_approval_required=False,
        )
        batches.append(
            {
                "profile": profile,
                "execution_plan": execution_plan,
                "catalog_manifest": context.catalog_manifest,
                "budget": budget,
                "ai_api_configs": execution_configs,
                "transport": _ResultsFirstNoProviderTransport(),
                "real_transport": False,
                "hard_limits": {
                    "max_total_provider_attempts": budget.max_provider_attempts,
                    "max_total_tokens": budget.token_upper_bound,
                    "max_cost_estimate": budget.cost_upper_bound,
                },
                "resume": False,
                "secret_values": (),
                "launch_manifest": {
                    "schema_version": (
                        "tokenshare.results_first_matrix8_trace_launch.v1"
                    ),
                    "authorization_kind": "user_authorized_smoke_facility",
                    "experiment_number": number,
                    "execution_plan_digest": (
                        execution_plan.execution_plan_digest
                    ),
                    "real_transport": False,
                },
                "recovery_manifest": None,
                "trace_context": trace_context,
            }
        )
    return tuple(batches)


def _smoke_case_planned_ai_unit_ids(case: Mapping[str, object]) -> tuple[str, ...]:
    schema_version = case.get("schema_version")
    if schema_version == "tokenshare.paper_factorization_case.v1":
        count = estimated_ai_units_for_case(dict(case))
        return tuple(f"range_{index}" for index in range(count))
    if schema_version == "tokenshare.paper_lean_case.v1":
        count = estimated_ai_units_for_case(dict(case))
        return tuple(f"child_{index}" for index in range(count))
    if schema_version == "tokenshare.paper_lean_lemma_graph_case.v1":
        graph = case.get("lemma_graph")
        nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
        if not isinstance(nodes, list):
            raise ValueError("Exp3 regression smoke Lean graph is invalid")
        ids = tuple(
            str(node.get("node_id"))
            for node in nodes
            if isinstance(node, Mapping) and node.get("node_id")
        )
        if len(ids) != estimated_ai_units_for_case(dict(case)):
            raise ValueError("Exp3 regression smoke Lean unit count drift")
        return ids
    raise ValueError("Exp3 regression smoke case schema is unsupported")


def _run_smoke_cli(
    *,
    args: argparse.Namespace,
    output_root: Path,
    transport: object,
    supplied_ai_api_configs: dict | None,
) -> int:
    """独立 smoke CLI；正式矩阵参数不参与 smoke selector。"""

    formal_output_root = _configured_paper_output_boundary()
    resolved_output_root = output_root.resolve(strict=False)
    if (
        resolved_output_root == formal_output_root
        or formal_output_root in resolved_output_root.parents
        or resolved_output_root in formal_output_root.parents
    ):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "invalid_smoke_output_root",
                    "message": (
                        "smoke output root must be isolated from formal paper output"
                    ),
                    "provider_calls_made": 0,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    try:
        profile = load_paper_smoke_profile(Path(args.smoke_profile))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=(),
            failure_kind="invalid_smoke_profile",
            message=str(exc),
            suite_id="paper_smoke_blocked",
        )
        return 3
    if args.pilot or args.exp1_pilot_profile is not None or any(
        value is not None
        for value in (args.condition_id, args.case_id, args.ai_unit_id)
    ):
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="invalid_smoke_mode",
            message="--smoke-profile cannot be combined with pilot selectors",
        )
        return 3
    if args.replay_only:
        try:
            result = replay_paper_smoke_suite(
                output_root=output_root,
                expected_profile_digest=profile.profile_digest,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_replay_failed",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    experiment_ids = profile.experiment_ids
    if not (args.plan_only or args.smoke_identity_only) and not args.real_transport:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="missing_real_transport",
            message="paper smoke execution requires --real-transport",
        )
        return 1
    planning_profile: Exp1PilotProfile | None = None
    if not args.plan_only and _is_exp1_exp4_only_smoke(profile):
        try:
            _validate_exp1_exp4_v3_config_path(args)
            planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
            _validate_exp1_exp4_v3_execution_config(
                planning_profile.source_provider_config
            )
            if supplied_ai_api_configs is not None:
                _validate_exp1_exp4_v3_execution_config(
                    supplied_ai_api_configs.get(
                        EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _write_smoke_blocked_suite(
                output_root=output_root,
                profile=profile,
                failure_kind="exp1_exp4_v3_config_blocked",
                message=str(exc),
            )
            return 3
    requires_exp5_cohort = (
        "exp5_real_ai_model_endpoint_comparison" in experiment_ids
    )
    if requires_exp5_cohort and args.local_ai_api_config is not None:
        try:
            _inject_exp5_v3_api_key(
                local_config_path=Path(args.local_ai_api_config),
                provider_config_args=tuple(args.provider_config),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _write_smoke_blocked_suite(
                output_root=output_root,
                profile=profile,
                failure_kind="exp5_local_api_config_blocked",
                message=str(exc),
            )
            return 3
    model_endpoint_cohort_preflight, _model_cohort = (
        _model_endpoint_cohort_preflight_for_suite(
            experiment_ids=experiment_ids,
            model_cohort_file=args.model_cohort_file,
            model_entry_map=args.model_entry_map,
            provider_config_args=tuple(args.provider_config),
            require_smoke_evidence=False,
        )
    )
    if requires_exp5_cohort and (
        model_endpoint_cohort_preflight is None
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="incomplete_model_cohort",
            message=str(
                (model_endpoint_cohort_preflight or {}).get(
                    "message", "Experiment 5 cohort preflight is incomplete"
                )
            ),
            blocked_members=_smoke_exp5_blocked_members(
                profile=profile,
                preflight=model_endpoint_cohort_preflight or {},
            ),
        )
        return 3
    try:
        catalog_manifest = _load_default_paper_catalogs()
        lean_3x3_matrix = build_lean_3x3_matrix_plan(
            catalog_manifest=catalog_manifest
        )
        if planning_profile is None:
            planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
        suite_scale_profile = load_paper_suite_scale_profile(
            DEFAULT_PAPER_SUITE_SCALE_PROFILE
        )
        canonical_plans = build_gate_c_dispatch_plans(
            catalog_manifest=catalog_manifest,
            lean_3x3_matrix=lean_3x3_matrix,
            experiment_ids=experiment_ids,
            baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            paper_suite_scale_profile=suite_scale_profile,
            output_root=output_root,
        )
        canonical_plans = _with_exp2_regression_smoke_lean_plan(
            dispatch_plans=canonical_plans,
            profile=profile,
            catalog_manifest=catalog_manifest,
        )
        canonical_plans = _with_exp3_regression_smoke_lean_plan(
            dispatch_plans=canonical_plans,
            profile=profile,
            catalog_manifest=catalog_manifest,
        )
        execution_plan = resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=canonical_plans,
            catalog_id=catalog_manifest.catalog_id,
            catalog_version=catalog_manifest.catalog_version,
            catalog_digest=catalog_manifest.catalog_digest,
            output_root=output_root,
        )
        if (
            profile.expected_root_runs == 8
            and len(profile.experiment_ids) == 1
            and profile.experiment_ids[0] in EXP1_EXP4_ONLY_EXPERIMENT_IDS
        ):
            execution_plan = _with_matrix8_unified_factor_seed_execution_plan(
                execution_plan=execution_plan,
                profile=profile,
            )
    except (OSError, ValueError) as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_plan_blocked",
            message=str(exc),
        )
        return 3
    if args.resume:
        try:
            execution_plan = _restore_smoke_resume_dispatch_views(
                execution_plan=execution_plan,
                catalog_manifest=catalog_manifest,
                output_root=output_root,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_resume_dispatch_blocked",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    expected_ai_units_by_case = {
        item.case_id: estimated_ai_units_for_case(cases_by_id[item.case_id])
        for item in execution_plan.items
    }
    frozen_selections = execution_plan.budget_selection_commitments(
        expected_ai_units_by_case=expected_ai_units_by_case
    )
    endpoint_token_ceilings = build_exp5_v3_token_ceiling_mapping(
        model_endpoint_cohort_preflight
    )
    hard_limits = {
        key: value
        for key, value in (
            ("max_total_provider_attempts", args.max_total_provider_attempts),
            ("max_total_tokens", args.max_total_tokens),
            ("max_cost_estimate", args.max_cost_estimate),
            (
                "stop_after_current_task",
                True if args.stop_after_current_task else None,
            ),
        )
        if value is not None
    }
    try:
        budget = plan_paper_suite(
            catalog_manifest=catalog_manifest,
            conditions=tuple(
                condition
                for plan in execution_plan.dispatch_plans
                for condition in plan.conditions
            ),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=(
                FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            token_upper_bound_by_endpoint_identity_digest=(
                endpoint_token_ceilings
            ),
            cost_upper_bound_per_provider_attempt=(
                FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            plan_only=args.plan_only,
            lean_3x3_matrix=lean_3x3_matrix,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            frozen_selections=frozen_selections,
            endpoint_identity={
                "baseline": _baseline_endpoint_binding(planning_profile),
                "model_endpoint_cohort_preflight": model_endpoint_cohort_preflight,
            },
            request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
            hard_limits={} if args.unlimited_budget else hard_limits,
            suite_identity={
                "suite_version": "paper_smoke_v1",
                "execution_scope": "smoke_suite",
                "profile_digest": profile.profile_digest,
                "execution_plan_digest": execution_plan.execution_plan_digest,
                "experiment_ids": list(experiment_ids),
                "paper_suite_scale_profile_source_path": (
                    suite_scale_profile.source_path
                ),
                "paper_suite_scale_profile_digest": (
                    suite_scale_profile.profile_digest
                ),
                "exp5_selection_path": (
                    suite_scale_profile.exp5_source_selection_path
                ),
                "exp5_selection_digest": (
                    suite_scale_profile.exp5_source_selection_digest
                ),
            },
            output_identity={
                "output_root": output_root.resolve(strict=False).as_posix(),
                "formal_output_root_allowed": False,
                **_shared_exp1_reference_output_identity(
                    experiment_ids,
                    baseline_policy=profile.baseline_policy,
                ),
            },
            approve_budget_digest=args.approve_budget_digest,
            budget_approval_required=args.require_budget_approval,
            budget_mode="unlimited" if args.unlimited_budget else None,
        )
    except (PaperBudgetApprovalError, ValueError) as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_budget_blocked",
            message=str(exc),
        )
        return 2
    if args.smoke_identity_only:
        identity = _build_smoke_prelaunch_identity(
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=catalog_manifest,
            budget=budget,
            planning_profile=planning_profile,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        )
        print(json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.resume:
        try:
            budget = _restore_smoke_resume_budget(
                budget=budget,
                output_root=output_root,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_resume_budget_blocked",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
    try:
        _validate_expected_smoke_run_instance_identity(
            args=args,
            execution_plan=execution_plan,
            budget=budget,
        )
    except ValueError as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_run_instance_identity_blocked",
            message=str(exc),
        )
        return 3
    if args.plan_only:
        _write_smoke_plan_only(
            output_root=output_root,
            profile=profile,
            execution_plan=execution_plan,
            budget=budget.to_dict(),
            lean_3x3_matrix=lean_3x3_matrix,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        )
        return 0
    try:
        if supplied_ai_api_configs is not None:
            execution_configs = dict(supplied_ai_api_configs)
        else:
            if any(
                experiment_id != "exp5_real_ai_model_endpoint_comparison"
                for experiment_id in experiment_ids
            ):
                _inject_exp1_pilot_api_key(
                    pilot_profile=planning_profile,
                    local_config_path=Path(args.ai_api_config),
                )
            execution_configs = {
                planning_profile.model_endpoint_identity.provider_config_id: (
                    planning_profile.source_provider_config
                )
            }
            execution_configs.update(
                load_provider_config_map(
                    _parse_provider_config_args(tuple(args.provider_config))
                )
            )
        if model_endpoint_cohort_preflight is not None:
            execution_configs.setdefault(APPROVED_ENDPOINT_BINDINGS_KEY, {})
            execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY][
                "exp5_real_ai_model_endpoint_comparison"
            ] = model_endpoint_cohort_preflight
        if _is_exp1_exp4_only_smoke(profile):
            _validate_exp1_exp4_v3_execution_config(
                execution_configs.get(EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID)
            )
        current_launch_manifest = _build_smoke_launch_manifest(
            args=args,
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=catalog_manifest,
            budget=budget,
            planning_profile=planning_profile,
            execution_configs=execution_configs,
        )
        launch_manifest, recovery_manifest = _resolve_smoke_invocation_manifests(
            resume=args.resume,
            output_root=output_root,
            current_manifest=current_launch_manifest,
        )
        exp5_v3_prior_evidence = None
        if isinstance(getattr(profile, "exp5_v3_contract", None), Mapping):
            exp5_v3_schedule_binding = _exp5_v3_execution_schedule_binding(
                profile=profile,
                execution_plan=execution_plan,
            )
            if args.resume:
                exp5_v3_prior_evidence = _load_exp5_v3_execution_schedule(
                    output_root=output_root,
                    schedule_binding=exp5_v3_schedule_binding,
                )
        execution_transport, exp5_v3_limiter = (
            _exp5_v3_smoke_execution_transport(
                profile=profile,
                execution_plan=execution_plan,
                model_endpoint_cohort_preflight=(
                    model_endpoint_cohort_preflight
                ),
                transport=transport,
                ai_api_configs=execution_configs,
                prior_evidence=exp5_v3_prior_evidence,
            )
        )
        exp5_v3_execution_evidence = None
        try:
            if profile.experiment_ids == (
                "exp5_real_ai_model_endpoint_comparison",
            ):
                smoke_authority = build_paper_smoke_service_authority(
                    scope="exp5_capability_smoke",
                    authorized_plan_digest=execution_plan.execution_plan_digest,
                    profile=profile,
                    execution_plan=execution_plan,
                    catalog_manifest=catalog_manifest,
                    budget=budget,
                    ai_api_configs=execution_configs,
                    transport=execution_transport,
                    hard_limits=hard_limits,
                    resume=args.resume,
                    launch_manifest=launch_manifest,
                    recovery_manifest=recovery_manifest,
                )
                smoke_kwargs = dict(smoke_authority.keyword_arguments)
                smoke_kwargs["real_transport"] = args.real_transport
                smoke_kwargs["secret_values"] = _configured_secret_values(
                    execution_configs
                )
                result = execute_paper_smoke_suite(**smoke_kwargs)
            else:
                result = execute_paper_smoke_suite(
                    profile=profile,
                    execution_plan=execution_plan,
                    catalog_manifest=catalog_manifest,
                    budget=budget,
                    ai_api_configs=execution_configs,
                    transport=execution_transport,
                    real_transport=args.real_transport,
                    hard_limits=hard_limits,
                    resume=args.resume,
                    secret_values=_configured_secret_values(execution_configs),
                    launch_manifest=launch_manifest,
                    recovery_manifest=recovery_manifest,
                )
        finally:
            if exp5_v3_limiter is not None:
                if exp5_v3_limiter.new_provider_calls_made:
                    exp5_v3_execution_evidence = _write_exp5_v3_execution_schedule(
                        output_root=output_root,
                        limiter=exp5_v3_limiter,
                    )
                else:
                    exp5_v3_execution_evidence = exp5_v3_limiter.prior_evidence
        result_completed = result.to_dict()["status"] == PaperStatus.COMPLETED.value
        if exp5_v3_limiter is not None and result_completed and (
            exp5_v3_execution_evidence is None
            or exp5_v3_execution_evidence["sequence_complete"] is not True
        ):
            raise ValueError(
                "Exp5 v3 execution schedule evidence failed closed"
            )
        _write_completed_exp5_smoke_evidence(
            profile=profile,
            output_root=output_root,
            result=result,
            entry_map_path=(
                Path(args.model_entry_map)
                if args.model_entry_map is not None
                else None
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        error_payload = {
            "status": "failed",
            "failure_kind": "smoke_execution_failed",
            "message": str(exc),
        }
        if not (output_root / "suite_manifest.json").exists():
            try:
                _write_smoke_blocked_suite(
                    output_root=output_root,
                    profile=profile,
                    failure_kind="smoke_execution_failed",
                    message=str(exc),
                )
            except (OSError, RuntimeError, ValueError) as persistence_exc:
                error_payload["evidence_persistence_error"] = str(
                    persistence_exc
                )
        print(
            json.dumps(
                error_payload,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return _execution_result_exit_code(result)


def _execution_result_exit_code(result: object) -> int:
    """把持久化 suite 终态传播给 CLI/监督进程。"""

    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        return 3
    status = to_dict().get("status")
    if status in {
        PaperStatus.COMPLETED.value,
        PaperStatus.COMPLETED_WITH_FAILURES.value,
    }:
        return 0
    return 3


def _formal_execution_exit_code(
    execution_result: object,
    report_result: object | None,
) -> int:
    """正式 suite 声称可写论文时，renderer 失败必须传播到监督进程。"""

    execution_exit_code = _execution_result_exit_code(execution_result)
    if execution_exit_code != 0:
        return execution_exit_code
    to_dict = getattr(execution_result, "to_dict", None)
    if not callable(to_dict) or to_dict().get("paper_eligible") is not True:
        return execution_exit_code
    if (
        getattr(report_result, "paper_eligible", None) is not True
        or getattr(report_result, "formal_paper_table_generated", None) is not True
    ):
        return 3
    return execution_exit_code


def _write_smoke_blocked_suite(
    *,
    output_root: Path,
    profile: object,
    failure_kind: str,
    message: str,
    blocked_members: tuple[dict, ...] = (),
) -> None:
    profile_body = profile.to_dict()
    body = {
        "schema_version": "tokenshare.paper_smoke_suite.v1",
        "suite_id": profile.suite_id,
        "status": "blocked",
        "experiment_ids": list(profile.experiment_ids),
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        "provider_attempt_count": 0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "error_summary": [
            {"failure_kind": failure_kind, "message": message}
        ],
        "blocked_members": [dict(member) for member in blocked_members],
    }
    documents = {
        "smoke_profile.json": profile_body,
        "suite_manifest.json": body,
    }
    if blocked_members:
        documents["audit/smoke_endpoint_preflight.json"] = {
            "schema_version": "tokenshare.paper_smoke_endpoint_preflight.v1",
            "suite_id": profile.suite_id,
            "status": "blocked",
            "provider_calls_made": 0,
            "members": [dict(member) for member in blocked_members],
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        }
    _write_smoke_documents_fail_closed(
        output_root=output_root,
        documents=documents,
    )


def _smoke_exp5_blocked_members(*, profile: object, preflight: dict) -> tuple[dict, ...]:
    """把 atomic cohort preflight 的阻塞显式投影到每个 smoke endpoint。"""

    member_plans = preflight.get("member_plans")
    if not isinstance(member_plans, dict):
        member_plans = {}
    member_ids = sorted(
        {
            str(item.condition_selector["cohort_member_id"])
            for item in profile.items
            if item.experiment_id == "exp5_real_ai_model_endpoint_comparison"
            and "cohort_member_id" in item.condition_selector
        }
    )
    records = []
    for member_id in member_ids:
        member_plan = member_plans.get(member_id)
        reasons = (
            member_plan.get("blocked_reasons")
            if isinstance(member_plan, dict)
            else None
        )
        if not isinstance(reasons, list) or not reasons:
            reasons = ["incomplete_model_cohort"]
        records.append(
            {
                "cohort_member_id": member_id,
                "status": "blocked",
                "blocked_reasons": list(dict.fromkeys(str(reason) for reason in reasons)),
                "provider_attempt_count": 0,
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "provider_actual_billing": None,
                "provider_actual_billing_available": False,
                "paper_eligible": False,
            }
        )
    return tuple(records)


def _write_smoke_plan_only(
    *,
    output_root: Path,
    profile: object,
    execution_plan: object,
    budget: dict,
    lean_3x3_matrix: dict,
    model_endpoint_cohort_preflight: dict | None,
) -> None:
    documents = {
        "smoke_profile.json": profile.to_dict(),
        "smoke_execution_plan.json": execution_plan.to_dict(),
        "run_budget.json": budget,
        "lean_3x3_matrix.json": lean_3x3_matrix,
        "suite_manifest.json": {
            "schema_version": "tokenshare.paper_smoke_suite.v1",
            "suite_id": profile.suite_id,
            "status": "planned",
            "experiment_ids": list(profile.experiment_ids),
            "condition_count": sum(
                len(plan.conditions) for plan in execution_plan.dispatch_plans
            ),
            "run_count": execution_plan.direct_root_run_count,
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
            "provider_attempt_count": 0,
            "total_tokens": 0,
            "total_cost_estimate": 0.0,
        },
    }
    if model_endpoint_cohort_preflight is not None:
        documents["model_endpoint_cohort_plan.json"] = (
            model_endpoint_cohort_preflight
        )
    _write_smoke_documents_fail_closed(
        output_root=output_root,
        documents=documents,
    )
    print(
        json.dumps(
            documents["suite_manifest.json"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _write_smoke_documents_fail_closed(
    *,
    output_root: Path,
    documents: dict[str, dict],
) -> None:
    """先校验全部已有 identity，再写缺失文件，避免半写入覆盖。"""

    output_root.mkdir(parents=True, exist_ok=True)
    for name, body in documents.items():
        path = output_root / name
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != body:
                raise ValueError(f"smoke output identity mismatch: {name}")
    for name, body in documents.items():
        path = output_root / name
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


@lru_cache(maxsize=1)
def _load_default_paper_catalogs() -> PaperInputCatalogManifest:
    return load_paper_catalogs(
        factorization_path=DEFAULT_FACTOR_CATALOG,
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG
            if DEFAULT_LEAN_LEMMA_GRAPH_CATALOG.exists()
            else None
        ),
    )


def _load_exp1_pilot_paper_catalogs() -> PaperInputCatalogManifest:
    return load_paper_catalogs(
        factorization_path=DEFAULT_EXP1_PILOT_FACTOR_CATALOG,
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG
            if DEFAULT_LEAN_LEMMA_GRAPH_CATALOG.exists()
            else None
        ),
    )


def _inject_exp1_pilot_api_key(
    *,
    pilot_profile: Exp1PilotProfile,
    local_config_path: Path,
) -> None:
    """只把匹配 provider/model 的本地 secret 注入批准 entry 的 env。"""

    selected_entry_id = pilot_profile.model_endpoint_identity.selected_entry_id
    approved_entries = [
        entry
        for entry in pilot_profile.source_provider_config.entries
        if entry.entry_id == selected_entry_id and entry.enabled
    ]
    if len(approved_entries) != 1:
        raise ValueError("approved Exp1 pilot entry is missing or disabled")
    approved_entry = approved_entries[0]
    if os.environ.get(approved_entry.api_key_env):
        return
    if not local_config_path.is_file():
        raise ValueError(
            f"Exp1 pilot local AI API config is missing: {local_config_path}"
        )

    local_config = load_local_ai_api_config(local_config_path)
    candidates = sorted(
        (
            entry
            for entry in local_config.entries
            if entry.enabled
            and local_config.provider_family
            == pilot_profile.model_endpoint_identity.provider_family
            and entry.model
            == pilot_profile.model_endpoint_identity.provider_model_id
            and entry.base_url == approved_entry.base_url
        ),
        key=lambda entry: entry.entry_id,
    )
    if not candidates:
        raise ValueError(
            "Exp1 pilot local config has no enabled key for the approved provider/model"
        )
    os.environ[approved_entry.api_key_env] = candidates[0].resolve_api_key()


def _inject_exp5_v3_api_key(
    *,
    local_config_path: Path,
    provider_config_args: tuple[str, ...],
) -> None:
    """把 gitignored 本地 SiliconFlow secret 注入 v3 共享 env。"""

    provider_configs = load_provider_config_map(
        _parse_provider_config_args(provider_config_args)
    )
    target_config = provider_configs.get("siliconflow")
    if target_config is None:
        raise ValueError("Exp5 v3 SiliconFlow provider config is missing")
    target_entries = tuple(
        entry for entry in target_config.entries if entry.enabled
    )
    target_envs = {entry.api_key_env for entry in target_entries}
    if not target_entries or target_envs != {"SILICONFLOW_API_KEY"}:
        raise ValueError("Exp5 v3 SiliconFlow API key env binding drift")
    if os.environ.get("SILICONFLOW_API_KEY"):
        return
    if not local_config_path.is_file():
        raise ValueError(
            f"Exp5 v3 local AI API config is missing: {local_config_path}"
        )

    local_config = load_local_ai_api_config(local_config_path)
    if local_config.provider_family != target_config.provider_family:
        raise ValueError("Exp5 v3 local provider family does not match")
    target_base_urls = {entry.base_url for entry in target_entries}
    candidate_secrets = {
        secret
        for entry in local_config.entries
        if entry.enabled and entry.base_url in target_base_urls
        for secret in (os.environ.get(entry.api_key_env, ""),)
        if secret
    }
    if len(candidate_secrets) != 1:
        raise ValueError(
            "Exp5 v3 local config must resolve exactly one SiliconFlow secret"
        )
    os.environ["SILICONFLOW_API_KEY"] = next(iter(candidate_secrets))


def _configured_secret_values(configs: dict) -> tuple[str, ...]:
    """向报告 secret scan 提供本次实际配置引用的环境变量值。"""

    values: list[str] = []
    for config in configs.values():
        entries = getattr(config, "entries", ())
        for entry in entries:
            api_key_env = getattr(entry, "api_key_env", None)
            value = os.environ.get(api_key_env) if isinstance(api_key_env, str) else None
            if value:
                values.append(value)
    return tuple(sorted(set(values)))


def _build_smoke_launch_manifest(
    *,
    args: argparse.Namespace,
    profile: object,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    budget: object,
    planning_profile: Exp1PilotProfile,
    execution_configs: dict,
) -> dict:
    """冻结首次 provider 调用前的 smoke 授权与执行身份。"""

    endpoint_identity = planning_profile.model_endpoint_identity
    provider_config_id = endpoint_identity.provider_config_id
    config = execution_configs.get(provider_config_id)
    if config is None or not hasattr(config, "config_digest"):
        raise ValueError("smoke baseline provider config is missing")
    if config.config_digest != planning_profile.source_provider_config.config_digest:
        raise ValueError("smoke baseline provider config digest mismatch")
    entries = [
        entry
        for entry in config.entries
        if entry.enabled
        and entry.entry_id == endpoint_identity.selected_entry_id
    ]
    if len(entries) != 1:
        raise ValueError("smoke baseline provider entry identity mismatch")
    entry = entries[0]
    request_limits = _baseline_endpoint_binding(planning_profile)[
        "request_controls"
    ]
    direct_by_experiment = {
        experiment_id: sum(
            item.experiment_id == experiment_id for item in profile.items
        )
        for experiment_id in profile.experiment_ids
    }
    scope = (
        "exp1_exp4_only_smoke"
        if tuple(profile.experiment_ids) == EXP1_EXP4_ONLY_EXPERIMENT_IDS
        else "paper_smoke"
    )
    selection_inventory = _smoke_selection_inventory(execution_plan)
    identity_seed = {
        "suite_id": profile.suite_id,
        "profile_digest": profile.profile_digest,
        "execution_plan_digest": execution_plan.execution_plan_digest,
        "catalog_digest": catalog_manifest.catalog_digest,
        "budget_digest": budget.budget_digest,
        "output_root": Path(execution_plan.output_root)
        .resolve(strict=False)
        .as_posix(),
    }
    run_identity_digest = digest_json(identity_seed)
    run_identity_suffix = run_identity_digest.removeprefix("sha256:")[:16]
    launch_recorded_at = datetime.now(timezone.utc)
    body = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "recorded_at": launch_recorded_at.isoformat().replace("+00:00", "Z"),
        "recorded_at_local": launch_recorded_at.astimezone().isoformat(),
        "authorization_scope": scope,
        "execution_identity": {
            "suite_id": profile.suite_id,
            "run_id": f"smoke_run_{run_identity_suffix}",
            "generation_id": f"smoke_generation_{run_identity_suffix}",
            "checkpoint_identities_at_launch": [],
            "identity_digest": run_identity_digest,
        },
        "command": {
            "executable": sys.executable,
            "module": "tokenshare.experiments.run_paper_experiments",
            "argv": list(args._command_argv),
        },
        "suite_id": profile.suite_id,
        "experiment_ids": list(profile.experiment_ids),
        "profile_path": Path(args.smoke_profile).as_posix(),
        "profile_digest": profile.profile_digest,
        "execution_plan_digest": execution_plan.execution_plan_digest,
        "catalog": {
            "catalog_id": catalog_manifest.catalog_id,
            "catalog_version": catalog_manifest.catalog_version,
            "catalog_digest": catalog_manifest.catalog_digest,
        },
        "budget": {
            "budget_mode": budget.budget_mode,
            "budget_digest": budget.budget_digest,
            "direct_root_runs": execution_plan.direct_root_run_count,
            "actual_scheduled_root_runs": budget.planned_root_runs,
            "planned_ai_units": budget.planned_ai_units,
            "max_provider_attempts": budget.max_provider_attempts,
        },
        "direct_root_runs_by_experiment": direct_by_experiment,
        "model_endpoint": {
            "provider_config_id": provider_config_id,
            "provider_config_path": Path(args.ai_api_config).as_posix(),
            "source_provider_config_digest": config.config_digest,
            "selected_entry_id": entry.entry_id,
            "provider_family": endpoint_identity.provider_family,
            "provider_model_id": endpoint_identity.provider_model_id,
            "reasoning_profile_id": endpoint_identity.reasoning_profile_id,
            "base_url": entry.base_url,
            "endpoint": entry.endpoint,
            "provider_inflight_limit": config.local_concurrency[
                "max_in_flight_global"
            ],
        },
        "request_limits": dict(request_limits),
        "worker_counts": sorted(
            {
                int(item.condition_selector["worker_count"])
                for item in profile.items
                if "worker_count" in item.condition_selector
            }
        ),
        "repeat_ids": sorted({int(item.repeat_id) for item in profile.items}),
        "output_root": Path(execution_plan.output_root)
        .resolve(strict=False)
        .as_posix(),
        "selection_bundle_digest": digest_json(selection_inventory),
        "selection_inventory": selection_inventory,
        "source_control": _git_source_snapshot(),
        "explicit_exclusions": [
            "exp5",
            "pilot",
            "formal_experiments",
            "full_experiment_matrix",
            "full_lean",
            "lean_audit",
            "force_all_lean_audit",
        ],
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
    }
    return {**body, "launch_manifest_digest": digest_json(body)}


def _smoke_selection_inventory(execution_plan: object) -> list[dict]:
    """返回不含 output root 的跨 run 稳定 smoke selection inventory。"""

    return [
        {
            "item_id": item.item_id,
            "experiment_id": item.experiment_id,
            "condition_id": item.condition_id,
            "condition_digest": item.condition_digest,
            "selection_id": item.selection_id,
            "selection_digest": item.selection_digest,
            "case_id": item.case_id,
            "repeat_id": item.repeat_id,
        }
        for item in execution_plan.items
    ]


def _shared_exp1_reference_output_identity(
    experiment_ids: Sequence[str],
    *,
    baseline_policy: str = "shared_exp1_reference",
) -> dict[str, object]:
    """只允许正式的 Exp1→Exp3 shared-reference evidence 流向。"""

    try:
        validate_experiment_dependency_order(experiment_ids)
    except ValueError:
        dependency_order_valid = False
    else:
        dependency_order_valid = True
    allowed = (
        "exp1_real_ai_feasibility" in experiment_ids
        and "exp3_real_ai_fault_recovery" in experiment_ids
        and dependency_order_valid
        and baseline_policy != "omitted_for_smoke_regression"
    )
    return {
        "cross_experiment_evidence_allowed": allowed,
        "cross_experiment_evidence_policy": {
            "policy_id": "exp1_to_exp3_shared_reference_v1",
            "allowed": allowed,
            "dependency_order_valid": dependency_order_valid,
            "source_experiment_id": (
                "exp1_real_ai_feasibility" if allowed else None
            ),
            "target_experiment_id": (
                "exp3_real_ai_fault_recovery" if allowed else None
            ),
            "purpose": "shared_exp1_reference" if allowed else None,
        },
    }


def _build_smoke_prelaunch_identity(
    *,
    profile: object,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    budget: object,
    planning_profile: Exp1PilotProfile,
    model_endpoint_cohort_preflight: Mapping[str, object] | None = None,
) -> dict:
    """分离跨 run 预注册语义与绑定 output root 的运行实例 identity。"""

    experiment_ids = tuple(profile.experiment_ids)
    includes_exp5 = "exp5_real_ai_model_endpoint_comparison" in experiment_ids
    includes_baseline_experiment = any(
        experiment_id != "exp5_real_ai_model_endpoint_comparison"
        for experiment_id in experiment_ids
    )
    endpoint_semantics: dict[str, object] = {}
    if includes_baseline_experiment:
        config = planning_profile.source_provider_config
        _validate_exp1_exp4_v3_execution_config(config)
        endpoint_identity = planning_profile.model_endpoint_identity
        entries = [
            entry
            for entry in config.entries
            if entry.enabled
            and entry.entry_id == endpoint_identity.selected_entry_id
        ]
        if len(entries) != 1:
            raise ValueError("smoke baseline provider entry identity mismatch")
        entry = entries[0]
        endpoint_semantics.update(
            {
                "provider_config_source_digest": config.config_digest,
                "model_endpoint": {
                    "provider_config_id": endpoint_identity.provider_config_id,
                    "selected_entry_id": entry.entry_id,
                    "provider_family": endpoint_identity.provider_family,
                    "provider_model_id": endpoint_identity.provider_model_id,
                    "reasoning_profile_id": endpoint_identity.reasoning_profile_id,
                    "base_url": entry.base_url,
                    "endpoint": entry.endpoint,
                    "provider_inflight_limit": config.local_concurrency[
                        "max_in_flight_global"
                    ],
                },
                "request_limits": dict(
                    _baseline_endpoint_binding(planning_profile)[
                        "request_controls"
                    ]
                ),
            }
        )
    if includes_exp5:
        preflight = model_endpoint_cohort_preflight
        if not isinstance(preflight, Mapping) or preflight.get("status") != "planned":
            raise ValueError("smoke Exp5 cohort identity is not planned")
        expected_member_ids = preflight.get("expected_member_ids")
        member_plans = preflight.get("member_plans")
        if not isinstance(expected_member_ids, list) or not isinstance(
            member_plans, Mapping
        ):
            raise ValueError("smoke Exp5 cohort identity is incomplete")
        member_endpoints: list[dict[str, object]] = []
        for member_id in expected_member_ids:
            plan = member_plans.get(member_id)
            if not isinstance(member_id, str) or not isinstance(plan, Mapping):
                raise ValueError("smoke Exp5 member identity is incomplete")
            if plan.get("status") != "planned":
                raise ValueError("smoke Exp5 member identity is not planned")
            endpoint_identity = plan.get("endpoint_identity")
            request_controls = plan.get("request_controls")
            if not isinstance(endpoint_identity, Mapping) or not isinstance(
                request_controls, Mapping
            ):
                raise ValueError("smoke Exp5 endpoint identity is incomplete")
            member_endpoints.append(
                {
                    "cohort_member_id": member_id,
                    "provider_config_id": plan.get("provider_config_id"),
                    "selected_entry_id": plan.get("selected_entry_id"),
                    "provider_family": plan.get("provider_family"),
                    "provider_model_id": plan.get("provider_model_id"),
                    "reasoning_profile_id": plan.get("reasoning_profile_id"),
                    "effective_reasoning_controls": endpoint_identity.get(
                        "effective_reasoning_controls"
                    ),
                    "source_provider_config_digest": plan.get(
                        "source_provider_config_digest"
                    ),
                    "model_endpoint_identity_digest": plan.get(
                        "model_endpoint_identity_digest"
                    ),
                    "request_controls": dict(request_controls),
                    "request_controls_digest": plan.get(
                        "request_controls_digest"
                    ),
                }
            )
        endpoint_semantics["model_endpoint_cohort"] = {
            "cohort_id": preflight.get("cohort_id"),
            "model_cohort_digest": preflight.get("model_cohort_digest"),
            "request_controls_snapshot": preflight.get(
                "request_controls_snapshot"
            ),
            "request_controls_snapshot_digest": preflight.get(
                "request_controls_snapshot_digest"
            ),
            "member_endpoints": member_endpoints,
        }
    selection_inventory = _smoke_selection_inventory(execution_plan)
    direct_root_runs = execution_plan.direct_root_run_count
    actual_scheduled_root_runs = budget.planned_root_runs
    supporting_baselines = actual_scheduled_root_runs - direct_root_runs
    if supporting_baselines < 0:
        raise ValueError("smoke supporting baseline count is invalid")
    commitments = budget.quota_preflight.get("budget_commitments", {})
    experiment_budget_identity = (
        commitments.get("experiment_budget_identity", {})
        if isinstance(commitments, Mapping)
        else {}
    )
    supporting_root_map = experiment_budget_identity.get(
        "supporting_baseline_root_runs_by_experiment",
        {},
    )
    supporting_ai_unit_map = experiment_budget_identity.get(
        "supporting_baseline_ai_units_by_experiment",
        {},
    )
    if not isinstance(supporting_root_map, Mapping) or not isinstance(
        supporting_ai_unit_map,
        Mapping,
    ):
        raise ValueError("smoke supporting baseline budget identity is invalid")
    supporting_baseline_root_runs = sum(
        int(value) for value in supporting_root_map.values()
    )
    supporting_baseline_ai_units = sum(
        int(value) for value in supporting_ai_unit_map.values()
    )
    if supporting_baseline_root_runs != supporting_baselines:
        raise ValueError("smoke supporting baseline root identity mismatch")
    baseline_omitted = (
        profile.baseline_policy == "omitted_for_smoke_regression"
    )
    shared_reference_identity = _shared_exp1_reference_output_identity(
        profile.experiment_ids,
        baseline_policy=profile.baseline_policy,
    )
    return {
        "schema_version": "tokenshare.paper_smoke_prelaunch_identity.v1",
        "preregistered_semantics": {
            "suite_id": profile.suite_id,
            "profile_digest": profile.profile_digest,
            "catalog_digest": catalog_manifest.catalog_digest,
            "selection_bundle_digest": digest_json(selection_inventory),
            "experiment_ids": list(profile.experiment_ids),
            "direct_root_runs": direct_root_runs,
            "supporting_worker_death_baselines": supporting_baselines,
            "supporting_baseline_root_runs": supporting_baseline_root_runs,
            "supporting_baseline_ai_units": supporting_baseline_ai_units,
            "supporting_provider_attempt_upper_bound": (
                supporting_baseline_ai_units
                * int(
                    experiment_budget_identity.get(
                        "provider_attempt_multiplier",
                        1,
                    )
                )
            ),
            "actual_scheduled_root_runs": actual_scheduled_root_runs,
            "planned_first_attempt_ai_units": budget.planned_ai_units,
            "budget_provider_attempt_upper_bound": budget.max_provider_attempts,
            "provider_retry_limit": 0,
            "worker_counts": sorted(
                {
                    int(item.condition_selector["worker_count"])
                    for item in profile.items
                    if "worker_count" in item.condition_selector
                }
            ),
            "repeat_ids": sorted({int(item.repeat_id) for item in profile.items}),
            "baseline_policy": profile.baseline_policy,
            "baseline": None if baseline_omitted else "required_by_formal_plan",
            "baseline_comparison_eligible": not baseline_omitted,
            "baseline_unavailable_reason": (
                "smoke_baseline_not_requested" if baseline_omitted else None
            ),
            **shared_reference_identity,
            **endpoint_semantics,
            "paper_eligible": False,
        },
        "run_instance_identity": {
            "output_root": Path(execution_plan.output_root)
            .resolve(strict=False)
            .as_posix(),
            "execution_plan_digest": execution_plan.execution_plan_digest,
            "budget_digest": budget.budget_digest,
        },
        "identity_policy": {
            "cross_run_stable": "preregistered_semantics",
            "per_output_root": [
                "run_instance_identity.output_root",
                "run_instance_identity.execution_plan_digest",
                "run_instance_identity.budget_digest",
            ],
            "same_output_root_identity_drift": "fail_closed",
        },
    }


def _validate_expected_smoke_run_instance_identity(
    *,
    args: argparse.Namespace,
    execution_plan: object,
    budget: object,
) -> None:
    """若 launcher 冻结了本 root identity，则在 secret/provider 前精确复核。"""

    expected_plan = args.expect_smoke_execution_plan_digest
    expected_budget = args.expect_smoke_budget_digest
    if (expected_plan is None) != (expected_budget is None):
        raise ValueError("smoke run-instance digest expectations must be paired")
    if expected_plan is None:
        return
    if execution_plan.execution_plan_digest != expected_plan:
        raise ValueError("same output root smoke execution-plan identity drift")
    if budget.budget_digest != expected_budget:
        raise ValueError("same output root smoke budget identity drift")


def _git_source_snapshot() -> dict:
    """记录 commit 与不含 diff 内容/secret 的 commit-less 工作树摘要。"""

    def run_git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise ValueError(
                f"unable to persist smoke Git identity: git {' '.join(arguments)}"
            )
        return result.stdout.strip()

    commit = run_git("rev-parse", "HEAD")
    status_lines = tuple(
        line for line in run_git("status", "--short").splitlines() if line
    )
    diff_stat = tuple(
        line for line in run_git("diff", "--stat", "HEAD").splitlines() if line
    )
    staged_stat = tuple(
        line for line in run_git("diff", "--cached", "--stat").splitlines() if line
    )
    return {
        "git_commit": commit,
        "commit_less_diff": {
            "dirty": bool(status_lines),
            "status_line_count": len(status_lines),
            "status_digest": digest_json({"status_lines": list(status_lines)}),
            "status_lines": list(status_lines),
            "worktree_diff_stat": list(diff_stat),
            "staged_diff_stat": list(staged_stat),
        },
    }


def _resolve_smoke_invocation_manifests(
    *,
    resume: bool,
    output_root: Path,
    current_manifest: dict,
) -> tuple[dict, dict | None]:
    """恢复时复用初始授权身份，并追加本次恢复调用与 checkpoint 血缘。"""

    _validate_smoke_manifest_digest(current_manifest)
    if not resume:
        return dict(current_manifest), None
    launch_path = output_root / "smoke_launch_manifest.json"
    if not launch_path.is_file():
        raise ValueError("smoke resume requires persisted launch manifest")
    existing = json.loads(launch_path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("persisted smoke launch manifest must be an object")
    _validate_smoke_manifest_digest(existing)
    volatile_fields = {
        "recorded_at",
        "recorded_at_local",
        "command",
        "launch_manifest_digest",
    }
    stable_existing = {
        key: value for key, value in existing.items() if key not in volatile_fields
    }
    stable_current = {
        key: value
        for key, value in current_manifest.items()
        if key not in volatile_fields
    }
    if stable_existing != stable_current:
        raise ValueError("smoke resume authorization identity drift")

    checkpoints = []
    checkpoint_root = output_root / "experiments"
    if checkpoint_root.is_dir():
        for path in sorted(checkpoint_root.rglob("CURRENT.json")):
            pointer = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(pointer, dict):
                raise ValueError("smoke resume checkpoint pointer must be an object")
            generation_id = pointer.get("generation_id")
            generation_digest = pointer.get("generation_manifest_digest")
            if (
                not isinstance(generation_id, str)
                or not generation_id
                or not isinstance(generation_digest, str)
                or not generation_digest.startswith("sha256:")
            ):
                raise ValueError("smoke resume checkpoint identity is invalid")
            checkpoints.append(
                {
                    "checkpoint_path": path.relative_to(output_root).as_posix(),
                    "generation_id": generation_id,
                    "generation_manifest_digest": generation_digest,
                }
            )
    if not checkpoints:
        raise ValueError("smoke resume requires a committed checkpoint")

    source_dir = Path(__file__).resolve().parent
    source_snapshot = {
        name: "sha256:" + sha256((source_dir / name).read_bytes()).hexdigest()
        for name in (
            "lean_paper_adapter.py",
            "paper_formal_runner.py",
            "paper_smoke.py",
            "run_paper_experiments.py",
        )
    }
    recovery_base = {
        "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
        "recorded_at": current_manifest["recorded_at"],
        "recovery_reason": "facility_code_repair",
        "parent_launch_manifest_digest": existing["launch_manifest_digest"],
        "resume_invocation_digest": current_manifest["launch_manifest_digest"],
        "command": current_manifest["command"],
        "output_root": current_manifest["output_root"],
        "profile_digest": current_manifest["profile_digest"],
        "execution_plan_digest": current_manifest["execution_plan_digest"],
        "catalog_digest": current_manifest["catalog"]["catalog_digest"],
        "budget_digest": current_manifest["budget"]["budget_digest"],
        "source_checkpoints": checkpoints,
        "facility_source_snapshot": source_snapshot,
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
    }
    provisional_digest = digest_json(recovery_base)
    recovery_id = "smoke_recovery_" + provisional_digest.removeprefix(
        "sha256:"
    )[:16]
    recovery_body = {**recovery_base, "recovery_id": recovery_id}
    recovery = {
        **recovery_body,
        "recovery_manifest_digest": digest_json(recovery_body),
    }
    return existing, recovery


def _restore_smoke_resume_dispatch_views(
    *,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    output_root: Path,
) -> object:
    """从初始 suite identity 恢复非确定性 readiness execution view。"""

    suite_path = output_root / "suite_manifest.json"
    if not suite_path.is_file():
        raise ValueError("smoke resume requires persisted suite manifest")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    frozen_identity = suite.get("suite_identity")
    frozen_dispatch = (
        frozen_identity.get("dispatch")
        if isinstance(frozen_identity, dict)
        else None
    )
    dispatch_body = (
        frozen_dispatch.get("body")
        if isinstance(frozen_dispatch, dict)
        else None
    )
    stored_plans = (
        dispatch_body.get("plans")
        if isinstance(dispatch_body, dict)
        else None
    )
    if not isinstance(stored_plans, list):
        raise ValueError("smoke resume frozen dispatch plans are missing")
    stored_by_experiment = {
        str(body.get("experiment_id")): body
        for body in stored_plans
        if isinstance(body, dict) and body.get("experiment_id")
    }
    current_plans = tuple(execution_plan.dispatch_plans)
    if set(stored_by_experiment) != {
        plan.experiment_id for plan in current_plans
    }:
        raise ValueError("smoke resume frozen dispatch experiment coverage drift")

    restored_plans = []
    for plan in current_plans:
        stored = stored_by_experiment[plan.experiment_id]
        current = plan.to_dict()
        stored_without_view = {
            key: value
            for key, value in stored.items()
            if key != "catalog_execution_view"
        }
        current_without_view = {
            key: value
            for key, value in current.items()
            if key != "catalog_execution_view"
        }
        if stored_without_view != current_without_view:
            raise ValueError(
                "smoke resume dispatch identity drift outside catalog execution view"
            )
        frozen_view = stored.get("catalog_execution_view")
        if frozen_view is None:
            if current.get("catalog_execution_view") is not None:
                raise ValueError("smoke resume catalog execution view coverage drift")
            restored_plans.append(plan)
            continue
        if not isinstance(frozen_view, dict):
            raise ValueError("smoke resume frozen catalog execution view is invalid")
        restore_catalog_execution_view(
            frozen_view,
            catalog_manifest=catalog_manifest,
        )
        restored_plans.append(
            replace(plan, catalog_execution_view=dict(frozen_view))
        )
    return replace(execution_plan, dispatch_plans=tuple(restored_plans))


def _restore_smoke_resume_budget(
    *,
    budget: PaperBudgetResult,
    output_root: Path,
) -> PaperBudgetResult:
    """恢复初始 budget 中会随 readiness 预检重建的 golden evidence 哈希。"""

    suite_path = output_root / "suite_manifest.json"
    if not suite_path.is_file():
        raise ValueError("smoke resume requires persisted suite manifest")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    frozen_identity = suite.get("suite_identity")
    frozen_budget = (
        frozen_identity.get("budget")
        if isinstance(frozen_identity, dict)
        else None
    )
    stored_body = (
        frozen_budget.get("body")
        if isinstance(frozen_budget, dict)
        else None
    )
    if not isinstance(stored_body, dict):
        raise ValueError("smoke resume frozen budget body is missing")

    current_body = budget.to_dict()
    stored_comparable = _without_lean_golden_evidence_content_hashes(stored_body)
    current_comparable = _without_lean_golden_evidence_content_hashes(current_body)
    if stored_comparable != current_comparable:
        raise ValueError(
            "smoke resume budget identity drift outside Lean golden evidence hashes"
        )
    stored_quota = stored_body.get("quota_preflight")
    if not isinstance(stored_quota, dict):
        raise ValueError("smoke resume frozen budget quota preflight is invalid")
    restored = replace(
        budget,
        quota_preflight=json.loads(
            json.dumps(stored_quota, ensure_ascii=False, sort_keys=True)
        ),
    )
    if restored.to_dict() != stored_body:
        raise ValueError("smoke resume frozen budget restoration is incomplete")
    return restored


def _without_lean_golden_evidence_content_hashes(body: dict) -> dict:
    comparable = json.loads(json.dumps(body, ensure_ascii=False, sort_keys=True))
    quota = comparable.get("quota_preflight")
    matrix = quota.get("lean_3x3_matrix") if isinstance(quota, dict) else None
    cells = matrix.get("cells") if isinstance(matrix, dict) else None
    if not isinstance(cells, list):
        raise ValueError("smoke resume budget Lean matrix is missing")
    for cell in cells:
        golden_by_case = (
            cell.get("golden_evidence_by_case_id")
            if isinstance(cell, dict)
            else None
        )
        if not isinstance(golden_by_case, dict):
            raise ValueError("smoke resume Lean golden evidence map is invalid")
        for evidence in golden_by_case.values():
            _remove_content_hashes(evidence)
    return comparable


def _remove_content_hashes(value: object) -> None:
    if isinstance(value, dict):
        value.pop("content_hash", None)
        for child in value.values():
            _remove_content_hashes(child)
    elif isinstance(value, list):
        for child in value:
            _remove_content_hashes(child)


def _validate_smoke_manifest_digest(manifest: dict) -> None:
    claimed = manifest.get("launch_manifest_digest")
    body = {
        key: value
        for key, value in manifest.items()
        if key != "launch_manifest_digest"
    }
    if not isinstance(claimed, str) or claimed != digest_json(body):
        raise ValueError("smoke launch manifest digest mismatch")


def _load_cli_pilot_profile(
    *,
    profile_path: str | None,
    output_root: Path,
    experiment_ids: tuple[str, ...],
) -> Exp1PilotProfile | None:
    if profile_path is None:
        return None
    try:
        return load_exp1_pilot_profile(Path(profile_path))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_profile",
            message=str(exc),
            suite_id="paper_exp1_minimal_pilot_invalid_profile",
        )
        return None


def _pilot_cli_profile_error(
    *,
    pilot_profile: Exp1PilotProfile,
    experiment_ids: tuple[str, ...],
    worker_levels: tuple[int, ...],
    optional_worker_levels: tuple[int, ...],
    repeats: int,
    seed_family: tuple[int, ...],
) -> str | None:
    body = pilot_profile.body
    if experiment_ids != (EXP1_PILOT_EXPERIMENT_ID,):
        return "Exp1 pilot profile can only plan Experiment 1"
    if worker_levels != (int(body["worker_count"]),):
        return "CLI worker levels do not match the frozen pilot profile"
    if optional_worker_levels:
        return "Exp1 minimal pilot does not allow optional worker levels"
    if repeats != int(body["repeat_count"]):
        return "CLI repeats do not match the frozen pilot profile"
    if seed_family != tuple(int(seed) for seed in body["seed_family"]):
        return "CLI seed family does not match the frozen pilot profile"
    return None


def _write_blocked_suite(
    *,
    output_root: Path,
    experiment_ids: tuple[str, ...],
    failure_kind: str,
    message: str,
    suite_id: str = "paper_v1_blocked",
    model_endpoint_cohort_preflight: Mapping[str, object] | None = None,
) -> None:
    suite = PaperSuiteResult(
        suite_id=suite_id,
        status=PaperStatus.BLOCKED,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=0,
        run_count=0,
        task_count=0,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=[],
        audit_refs=[],
        error_summary=[{"failure_kind": failure_kind, "message": message}],
        model_endpoint_cohort_preflight=(
            dict(model_endpoint_cohort_preflight)
            if model_endpoint_cohort_preflight is not None
            else None
        ),
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


def _write_suite(output_root: Path, suite: PaperSuiteResult) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "suite_manifest.json").write_text(
        json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_plan_artifacts(
    *,
    output_root: Path,
    budget: dict,
    pilot_profile: dict | None,
    lean_3x3_matrix: dict,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "run_budget.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if pilot_profile is not None:
        (output_root / "exp1_pilot_profile.json").write_text(
            json.dumps(
                pilot_profile,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    (output_root / "lean_3x3_matrix.json").write_text(
        json.dumps(
            lean_3x3_matrix,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _parse_provider_config_args(values: tuple[str, ...]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--provider-config must use provider=path")
        provider_config_id, path = value.split("=", 1)
        provider_config_id = provider_config_id.strip()
        path = path.strip()
        if not provider_config_id or not path:
            raise ValueError("--provider-config must use provider=path")
        result[provider_config_id] = Path(path)
    return result


def _model_endpoint_cohort_preflight_for_suite(
    *,
    experiment_ids: tuple[str, ...],
    model_cohort_file: str | None,
    model_entry_map: str | None,
    provider_config_args: tuple[str, ...],
    require_smoke_evidence: bool = True,
    smoke_evidence_bundle: str | None = None,
) -> tuple[dict | None, dict | None]:
    if "exp5_real_ai_model_endpoint_comparison" not in experiment_ids:
        return None, None
    if model_cohort_file is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-cohort-file is required for Experiment 5",
            ),
            None,
        )
    try:
        cohort = load_model_endpoint_cohort(Path(model_cohort_file))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return _blocked_model_endpoint_cohort_preflight(message=str(exc)), None
    if model_entry_map is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-entry-map is required for Experiment 5",
                cohort=cohort,
            ),
            cohort,
        )
    try:
        entry_map = load_model_entry_map(Path(model_entry_map))
        provider_configs = load_provider_config_map(
            _parse_provider_config_args(provider_config_args)
        )
        smoke_bundle = (
            load_exp5_smoke_evidence_bundle(Path(smoke_evidence_bundle))
            if require_smoke_evidence and smoke_evidence_bundle is not None
            else None
        )
        preflight = build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=provider_configs,
            require_smoke_evidence=require_smoke_evidence,
            smoke_evidence_bundle=smoke_bundle,
        )
        return preflight, cohort
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        reasons = (
            [exc.reason]
            if isinstance(exc, Exp5SmokeEvidenceError)
            else ["incomplete_model_cohort"]
        )
        return (
            _blocked_model_endpoint_cohort_preflight(
                message=str(exc),
                cohort=cohort,
                ineligibility_reasons=reasons,
            ),
            cohort,
        )


def _blocked_model_endpoint_cohort_preflight(
    *,
    message: str,
    cohort: dict | None = None,
    ineligibility_reasons: Sequence[str] = ("incomplete_model_cohort",),
) -> dict:
    members = cohort.get("members") if isinstance(cohort, dict) else None
    cohort_member_ids = (
        [
            str(member["cohort_member_id"])
            for member in members
            if isinstance(member, Mapping)
            and isinstance(member.get("cohort_member_id"), str)
            and member["cohort_member_id"]
        ]
        if isinstance(members, Sequence)
        and not isinstance(members, (str, bytes))
        else []
    )
    expected_member_ids = (
        cohort_member_ids
        if cohort_member_ids
        else list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    )
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "blocked",
        "paper_eligible_possible": False,
        "blocked_reason": "incomplete_model_cohort",
        "ineligibility_reasons": list(dict.fromkeys(ineligibility_reasons)),
        "message": message,
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": cohort.get("cohort_id") if isinstance(cohort, dict) else None,
        "model_cohort_digest": (
            cohort.get("model_cohort_digest") if isinstance(cohort, dict) else None
        ),
        "expected_member_ids": expected_member_ids,
        "missing_members": [],
        "missing_provider_configs": [],
        "missing_entry_ids": [],
        "ineligible_members": [],
        "member_plans": {},
    }


def _write_completed_exp5_smoke_evidence(
    *,
    profile: object,
    output_root: Path,
    result: object,
    entry_map_path: Path | None,
) -> dict | None:
    """仅为成功的独立 Exp5 bootstrap smoke 派生 formal 门禁证据。"""

    if getattr(profile, "suite_id", None) != EXP5_SMOKE_SUITE_ID:
        return None
    to_dict = getattr(result, "to_dict", None)
    terminal_statuses = {
        PaperStatus.COMPLETED.value,
        PaperStatus.COMPLETED_WITH_FAILURES.value,
    }
    if not callable(to_dict) or to_dict().get("status") not in terminal_statuses:
        return None
    if entry_map_path is None:
        raise ValueError("completed Exp5 smoke requires --model-entry-map")
    bundle = write_exp5_smoke_evidence_bundle(
        source_suite_root=output_root,
        entry_map=load_model_entry_map(entry_map_path),
        output_path=output_root / "audit" / "exp5_endpoint_smoke_evidence.json",
    )
    FormalEvidenceStore(output_root)._refresh_evidence_manifest()
    return bundle

if __name__ == "__main__":
    raise SystemExit(main())
