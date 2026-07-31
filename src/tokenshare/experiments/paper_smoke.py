"""独立 paper smoke profile 与 canonical condition 解析。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import digest_json


PAPER_SMOKE_PROFILE_SCHEMA_VERSION = "tokenshare.paper_smoke_profile.v1"
PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION = "tokenshare.paper_smoke_profile.v2"
PAPER_SMOKE_EXP5_V3_CONTRACT_SCHEMA_VERSION = (
    "tokenshare.paper_smoke_exp5_contract.v1"
)
PAPER_SMOKE_EXECUTION_PLAN_SCHEMA_VERSION = (
    "tokenshare.paper_smoke_execution_plan.v1"
)
SMOKE_INELIGIBILITY_REASONS = ("smoke_suite", "pilot_only")
SMOKE_BASELINE_POLICIES = frozenset(
    {
        "required_by_formal_plan",
        "omitted_for_smoke_regression",
    }
)
_WORKER_DEATH_PATTERN = re.compile(r"__dead(?P<dead>\d+)__p(?P<progress>\d+)__")
_ALLOWED_SELECTOR_FIELDS = frozenset(
    {
        "domain",
        "difficulty",
        "paper_difficulty",
        "topic_family",
        "worker_count",
        "fault_type",
        "fault_rate",
        "ablation_mode",
        "model_entry_id",
        "cohort_member_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "dead_worker_count",
        "kill_progress_percent",
        "matrix_kind",
    }
)
_EXP5_V3_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
_EXP5_V3_MEMBER_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_14b_siliconflow",
    "minimax_m2_5_siliconflow",
    "deepseek_v3_pro_siliconflow",
)
_EXP5_V3_REPEAT_MEMBER_ORDER = {
    "0": list(_EXP5_V3_MEMBER_IDS),
    "1": [
        "qwen3_14b_siliconflow",
        "deepseek_v3_pro_siliconflow",
        "glm_5_2_siliconflow",
        "minimax_m2_5_siliconflow",
    ],
    "2": [
        "minimax_m2_5_siliconflow",
        "glm_5_2_siliconflow",
        "deepseek_v3_pro_siliconflow",
        "qwen3_14b_siliconflow",
    ],
}
_EXP5_V3_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "cohort_id",
        "cohort_digest",
        "provider_config_id",
        "provider_config_digest",
        "selection_id",
        "selection_digest",
        "sequence_plan_digest",
        "shared_provider_family",
        "max_in_flight_global",
        "model_arms_sequential",
        "repeat_member_order",
    }
)
_V3_PROFILE_FIELDS = frozenset(
    {
        "schema_version",
        "suite_id",
        "profile_version",
        "catalog",
        "experiment_ids",
        "expected_root_runs",
        "output_mode",
        "baseline_policy",
        "formal",
        "pilot_only",
        "regression_only",
        "paper_eligible",
        "ineligibility_reasons",
        "items",
        "exp5_v3_contract",
        "profile_digest",
    }
)
_V3_CATALOG_FIELDS = frozenset(
    {
        "catalog_id",
        "catalog_version",
        "catalog_digest",
    }
)
_V3_ITEM_FIELDS = frozenset(
    {
        "item_id",
        "experiment_id",
        "case_id",
        "repeat_id",
        "condition_selector",
    }
)


@dataclass(frozen=True, kw_only=True)
class PaperSmokeItem:
    item_id: str
    experiment_id: str
    case_id: str
    repeat_id: int
    condition_selector: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "experiment_id": self.experiment_id,
            "case_id": self.case_id,
            "repeat_id": self.repeat_id,
            "condition_selector": dict(self.condition_selector),
        }


@dataclass(frozen=True, kw_only=True)
class PaperSmokeProfile:
    suite_id: str
    profile_version: str
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    experiment_ids: tuple[str, ...]
    expected_root_runs: int
    output_mode: str
    items: tuple[PaperSmokeItem, ...]
    ineligibility_reasons: tuple[str, ...]
    baseline_policy: str = "required_by_formal_plan"
    schema_version: str = PAPER_SMOKE_PROFILE_SCHEMA_VERSION
    exp5_v3_contract: Mapping[str, Any] | None = None
    formal: bool = False
    pilot_only: bool = True
    regression_only: bool = True
    paper_eligible: bool = False

    @property
    def profile_digest(self) -> str:
        return digest_json(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "profile_digest": self.profile_digest}

    def _body(self) -> dict[str, Any]:
        body = {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "profile_version": self.profile_version,
            "catalog": {
                "catalog_id": self.catalog_id,
                "catalog_version": self.catalog_version,
                "catalog_digest": self.catalog_digest,
            },
            "experiment_ids": list(self.experiment_ids),
            "expected_root_runs": self.expected_root_runs,
            "output_mode": self.output_mode,
            "baseline_policy": self.baseline_policy,
            "formal": self.formal,
            "pilot_only": self.pilot_only,
            "regression_only": self.regression_only,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "items": [item.to_dict() for item in self.items],
        }
        if self.exp5_v3_contract is not None:
            body["exp5_v3_contract"] = dict(self.exp5_v3_contract)
        return body


@dataclass(frozen=True, kw_only=True)
class ResolvedPaperSmokeItem:
    item_id: str
    experiment_id: str
    case_id: str
    repeat_id: int
    condition_selector: Mapping[str, Any]
    condition_id: str
    condition_digest: str
    selection_id: str
    selection_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "experiment_id": self.experiment_id,
            "case_id": self.case_id,
            "repeat_id": self.repeat_id,
            "condition_selector": dict(self.condition_selector),
            "condition_id": self.condition_id,
            "condition_digest": self.condition_digest,
            "selection_id": self.selection_id,
            "selection_digest": self.selection_digest,
        }


@dataclass(frozen=True, kw_only=True)
class PaperSmokeExecutionPlan:
    suite_id: str
    profile_digest: str
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    output_root: str
    experiment_ids: tuple[str, ...]
    items: tuple[ResolvedPaperSmokeItem, ...]
    dispatch_plans: tuple[PaperExperimentDispatchPlan, ...]
    root_case_filter: Mapping[str, tuple[str, ...]]
    baseline_policy: str = "required_by_formal_plan"
    schema_version: str = PAPER_SMOKE_EXECUTION_PLAN_SCHEMA_VERSION

    @property
    def direct_root_run_count(self) -> int:
        return len(self.items)

    @property
    def execution_plan_digest(self) -> str:
        return digest_json(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "execution_plan_digest": self.execution_plan_digest}

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "profile_digest": self.profile_digest,
            "catalog": {
                "catalog_id": self.catalog_id,
                "catalog_version": self.catalog_version,
                "catalog_digest": self.catalog_digest,
            },
            "output_root": self.output_root,
            "experiment_ids": list(self.experiment_ids),
            "direct_root_run_count": self.direct_root_run_count,
            "baseline_policy": self.baseline_policy,
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "ineligibility_reasons": list(SMOKE_INELIGIBILITY_REASONS),
            "root_case_filter": {
                condition_id: list(case_ids)
                for condition_id, case_ids in sorted(self.root_case_filter.items())
            },
            "items": [item.to_dict() for item in self.items],
        }

    def budget_selection_commitments(
        self,
        *,
        expected_ai_units_by_case: Mapping[str, int],
    ) -> list[dict[str, Any]]:
        """把 canonical selection identity 与 smoke root filter 一起冻结进预算。"""

        resolved_by_condition = {item.condition_id: item for item in self.items}
        commitments: list[dict[str, Any]] = []
        for plan in self.dispatch_plans:
            for condition, selection in plan.bound_items():
                resolved = resolved_by_condition[condition.condition_id]
                commitments.append(
                    {
                        **selection.to_dict(),
                        "condition_id": condition.condition_id,
                        "condition_digest": condition.condition_digest,
                        "canonical_ordered_case_ids": list(
                            selection.ordered_case_ids
                        ),
                        "ordered_case_ids": [resolved.case_id],
                        "expected_ai_unit_count": int(
                            expected_ai_units_by_case[resolved.case_id]
                        ),
                        "smoke_root_filter": True,
                    }
                )
        return commitments


def load_paper_smoke_profile(path: str | Path) -> PaperSmokeProfile:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("paper smoke profile must be a JSON object")
    schema_version = body.get("schema_version")
    if schema_version not in {
        PAPER_SMOKE_PROFILE_SCHEMA_VERSION,
        PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported paper smoke profile schema")
    exp5_v3_contract: Mapping[str, Any] | None = None
    declared_v3_profile_digest: str | None = None
    if schema_version == PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION:
        if set(body).difference(_V3_PROFILE_FIELDS):
            raise ValueError("paper smoke v3 profile contains unsupported fields")
        if body.get("profile_version") not in {"v3", "v4"}:
            raise ValueError(
                "paper smoke profile v2 schema requires profile_version=v3 or v4"
            )
        declared_v3_profile_digest = _required_digest(body, "profile_digest")
        exp5_v3_contract = _load_exp5_v3_contract(
            body,
            profile_version=str(body["profile_version"]),
        )
    if body.get("formal") is not False:
        raise ValueError("paper smoke profile requires formal=false")
    if body.get("pilot_only") is not True:
        raise ValueError("paper smoke profile requires pilot_only=true")
    if body.get("regression_only") is not True:
        raise ValueError("paper smoke profile requires regression_only=true")
    if body.get("paper_eligible") is not False:
        raise ValueError("paper smoke profile requires paper_eligible=false")
    catalog = _required_mapping(body, "catalog")
    if (
        schema_version == PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION
        and set(catalog) != _V3_CATALOG_FIELDS
    ):
        raise ValueError("paper smoke v3 catalog fields do not match schema")
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("paper smoke profile requires non-empty items")
    items = tuple(
        _load_item(
            item,
            strict_v3=schema_version == PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION,
        )
        for item in raw_items
    )
    expected_root_runs = _required_non_negative_int(body, "expected_root_runs")
    if expected_root_runs != len(items):
        raise ValueError("paper smoke expected_root_runs does not match items")
    experiment_ids = _required_string_tuple(body, "experiment_ids")
    if tuple(dict.fromkeys(item.experiment_id for item in items)) != experiment_ids:
        raise ValueError("paper smoke experiment_ids do not match item order")
    item_ids = tuple(item.item_id for item in items)
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("paper smoke item_id values must be unique")
    reasons = _required_string_tuple(body, "ineligibility_reasons")
    if not set(SMOKE_INELIGIBILITY_REASONS).issubset(reasons):
        raise ValueError("paper smoke ineligibility reasons are incomplete")
    baseline_policy = body.get("baseline_policy", "required_by_formal_plan")
    if baseline_policy not in SMOKE_BASELINE_POLICIES:
        raise ValueError("unsupported paper smoke baseline_policy")
    if (
        baseline_policy == "omitted_for_smoke_regression"
        and "smoke_baseline_not_requested" not in reasons
    ):
        raise ValueError("smoke baseline omission reason is missing")
    profile = PaperSmokeProfile(
        suite_id=_required_string(body, "suite_id"),
        profile_version=_required_string(body, "profile_version"),
        catalog_id=_required_string(catalog, "catalog_id"),
        catalog_version=_required_string(catalog, "catalog_version"),
        catalog_digest=_required_digest(catalog, "catalog_digest"),
        experiment_ids=experiment_ids,
        expected_root_runs=expected_root_runs,
        output_mode=_required_string(body, "output_mode"),
        items=items,
        ineligibility_reasons=reasons,
        baseline_policy=str(baseline_policy),
        schema_version=str(schema_version),
        exp5_v3_contract=exp5_v3_contract,
    )
    if schema_version == PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION:
        _validate_exp5_v3_items(profile)
    declared_digest = (
        declared_v3_profile_digest
        if schema_version == PAPER_SMOKE_PROFILE_V3_SCHEMA_VERSION
        else body.get("profile_digest")
    )
    if declared_digest is not None and declared_digest != profile.profile_digest:
        raise ValueError("paper smoke profile digest mismatch")
    return profile


def _load_exp5_v3_contract(
    body: Mapping[str, Any],
    *,
    profile_version: str,
) -> Mapping[str, Any]:
    contract = _required_mapping(body, "exp5_v3_contract")
    if set(contract) != _EXP5_V3_CONTRACT_FIELDS:
        raise ValueError("paper smoke Exp5 v3 contract fields do not match schema")
    if (
        contract.get("schema_version")
        != PAPER_SMOKE_EXP5_V3_CONTRACT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported paper smoke Exp5 v3 contract schema")
    if contract.get("cohort_id") != "tokenshare.paper.model_endpoint_cohort.v3":
        raise ValueError("paper smoke Exp5 v3 cohort_id mismatch")
    if contract.get("provider_config_id") != "executor_ai_api_exp5_siliconflow_v3":
        raise ValueError("paper smoke Exp5 v3 provider_config_id mismatch")
    expected_selection_id = (
        "tokenshare.paper.exp5.parent_quarter.v4"
        if profile_version == "v4"
        else "tokenshare.paper.exp5.hard_half.v3"
    )
    if contract.get("selection_id") != expected_selection_id:
        raise ValueError("paper smoke Exp5 v3 selection_id mismatch")
    for field_name in (
        "cohort_digest",
        "provider_config_digest",
        "selection_digest",
        "sequence_plan_digest",
    ):
        _required_digest(contract, field_name)
    if contract.get("shared_provider_family") != "siliconflow":
        raise ValueError("paper smoke Exp5 v3 requires shared SiliconFlow provider")
    max_in_flight_global = contract.get("max_in_flight_global")
    if type(max_in_flight_global) is not int or max_in_flight_global != 3:
        raise ValueError(
            "paper smoke Exp5 v3 requires max_in_flight_global exact integer 3"
        )
    if contract.get("model_arms_sequential") is not True:
        raise ValueError("paper smoke Exp5 v3 requires sequential model arms")
    if contract.get("repeat_member_order") != _EXP5_V3_REPEAT_MEMBER_ORDER:
        raise ValueError("paper smoke Exp5 v3 repeat_member_order mismatch")
    canonical_digests = _canonical_exp5_v3_contract_digests(
        profile_version=profile_version
    )
    for field_name, expected_digest in canonical_digests.items():
        if contract.get(field_name) != expected_digest:
            raise ValueError(
                f"paper smoke Exp5 v3 canonical {field_name} mismatch"
            )
    return {
        field_name: (
            {
                repeat_id: list(member_ids)
                for repeat_id, member_ids in _EXP5_V3_REPEAT_MEMBER_ORDER.items()
            }
            if field_name == "repeat_member_order"
            else contract[field_name]
        )
        for field_name in contract
    }


def _canonical_exp5_v3_contract_digests(
    *,
    profile_version: str,
) -> Mapping[str, str]:
    """从当前 canonical artifacts 和常量重建 v3 smoke identity。"""

    from tokenshare.executors.ai_api_config import load_ai_api_config
    from tokenshare.experiments.paper_exp5_model_comparison import (
        EXP5_V3_SEQUENCE_PLAN_DIGEST,
        load_exp5_v3_selection,
        load_exp5_v4_selection,
    )
    from tokenshare.experiments.paper_model_policy import (
        load_model_endpoint_cohort,
    )

    repository_root = Path(__file__).resolve().parents[3]
    cohort = load_model_endpoint_cohort(
        repository_root / "benchmarks/paper/model_comparison_cohort.v3.json"
    )
    provider_config_body = json.loads(
        (
            repository_root
            / "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
        ).read_text(encoding="utf-8")
    )
    provider_config = load_ai_api_config(provider_config_body)
    selection = (
        load_exp5_v4_selection()
        if profile_version == "v4"
        else load_exp5_v3_selection()
    )
    return {
        "cohort_digest": str(cohort["model_cohort_digest"]),
        "provider_config_digest": provider_config.config_digest,
        "selection_digest": str(selection["selection_digest"]),
        "sequence_plan_digest": EXP5_V3_SEQUENCE_PLAN_DIGEST,
    }


def _validate_exp5_v3_items(profile: PaperSmokeProfile) -> None:
    if profile.expected_root_runs not in {8, 29} or len(profile.items) not in {
        8,
        29,
    }:
        raise ValueError(
            "paper smoke Exp5 v3 profile requires either 8 Exp5-only roots "
            "or 29 composite roots"
        )
    exp5_items = tuple(
        item
        for item in profile.items
        if item.experiment_id == _EXP5_V3_EXPERIMENT_ID
    )
    if len(exp5_items) != 8:
        raise ValueError("paper smoke Exp5 v3 profile requires 8 Exp5 roots")
    if profile.expected_root_runs == 8 and profile.experiment_ids != (
        _EXP5_V3_EXPERIMENT_ID,
    ):
        raise ValueError(
            "paper smoke Exp5-only v3 profile requires only Experiment 5"
        )
    expected_members = tuple(
        member_id for member_id in _EXP5_V3_MEMBER_IDS for _ in range(2)
    )
    if tuple(
        item.condition_selector.get("cohort_member_id") for item in exp5_items
    ) != expected_members:
        raise ValueError("paper smoke Exp5 v3 member order mismatch")
    for offset, item in enumerate(exp5_items):
        member_id = _EXP5_V3_MEMBER_IDS[offset // 2]
        if item.repeat_id != 0:
            raise ValueError("paper smoke Exp5 v3 roots require repeat_id=0")
        worker_count = item.condition_selector.get("worker_count")
        if type(worker_count) is not int or worker_count != 3:
            raise ValueError(
                "paper smoke Exp5 v3 selector requires worker_count exact integer 3"
            )
        if offset % 2 == 0:
            expected_case_id = "factor_v2_hard_001"
            expected_selector = {
                "domain": "factorization",
                "difficulty": "hard",
                "worker_count": 3,
                "cohort_member_id": member_id,
            }
        else:
            expected_case_id = "lean_v2_hard_frontier_pure_logic_checker_06"
            expected_selector = {
                "domain": "lean_proof",
                "difficulty": "hard",
                "paper_difficulty": "hard_frontier",
                "topic_family": "pure_logic",
                "worker_count": 3,
                "cohort_member_id": member_id,
            }
        if (
            item.case_id != expected_case_id
            or dict(item.condition_selector) != expected_selector
        ):
            raise ValueError("paper smoke Exp5 v3 root selector mismatch")


def resolve_paper_smoke_execution_plan(
    *,
    profile: PaperSmokeProfile,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_id: str,
    catalog_version: str,
    catalog_digest: str,
    output_root: str | Path,
) -> PaperSmokeExecutionPlan:
    """用语义 selector 从正式 canonical plan 精确解析 smoke roots。"""

    if (
        profile.catalog_id != catalog_id
        or profile.catalog_version != catalog_version
        or profile.catalog_digest != catalog_digest
    ):
        raise ValueError("paper smoke profile catalog identity mismatch")
    plans_by_experiment = {plan.experiment_id: plan for plan in dispatch_plans}
    if len(plans_by_experiment) != len(tuple(dispatch_plans)):
        raise ValueError("duplicate canonical dispatch plan")
    resolved: list[ResolvedPaperSmokeItem] = []
    selected_by_experiment: dict[
        str, list[tuple[Any, Any]]
    ] = {experiment_id: [] for experiment_id in profile.experiment_ids}
    root_case_filter: dict[str, tuple[str, ...]] = {}
    for item in profile.items:
        plan = plans_by_experiment.get(item.experiment_id)
        if plan is None or plan.status != "planned":
            raise ValueError(
                f"smoke item has no executable canonical plan: {item.item_id}"
            )
        matches = tuple(
            (condition, selection)
            for condition, selection in plan.bound_items()
            if condition.repeat_id == item.repeat_id
            and item.case_id in selection.ordered_case_ids
            and _condition_matches(condition, item.condition_selector)
        )
        if len(matches) != 1:
            raise ValueError(
                "paper smoke item must resolve to exactly one canonical condition: "
                f"{item.item_id} matched {len(matches)}"
            )
        condition, selection = matches[0]
        if condition.condition_id in root_case_filter:
            raise ValueError("paper smoke conditions must be unique")
        root_case_filter[condition.condition_id] = (item.case_id,)
        selected_by_experiment[item.experiment_id].append((condition, selection))
        resolved.append(
            ResolvedPaperSmokeItem(
                item_id=item.item_id,
                experiment_id=item.experiment_id,
                case_id=item.case_id,
                repeat_id=item.repeat_id,
                condition_selector=dict(item.condition_selector),
                condition_id=condition.condition_id,
                condition_digest=condition.condition_digest,
                selection_id=selection.selection_id,
                selection_digest=selection.selection_digest,
            )
        )
    output = Path(output_root).resolve(strict=False)
    smoke_plans = tuple(
        PaperExperimentDispatchPlan(
            experiment_id=experiment_id,
            output_root=(output / experiment_id).as_posix(),
            conditions=tuple(condition for condition, _ in selected_by_experiment[experiment_id]),
            condition_selection_bindings=tuple(
                FrozenConditionSelectionBinding.from_condition(
                    condition, selection
                )
                for condition, selection in selected_by_experiment[experiment_id]
            ),
            paper_eligible_possible=False,
            catalog_execution_view=plans_by_experiment[
                experiment_id
            ].catalog_execution_view,
        )
        for experiment_id in profile.experiment_ids
    )
    execution = PaperSmokeExecutionPlan(
        suite_id=profile.suite_id,
        profile_digest=profile.profile_digest,
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        catalog_digest=catalog_digest,
        output_root=output.as_posix(),
        experiment_ids=profile.experiment_ids,
        items=tuple(resolved),
        dispatch_plans=smoke_plans,
        root_case_filter=root_case_filter,
        baseline_policy=profile.baseline_policy,
    )
    if execution.direct_root_run_count != profile.expected_root_runs:
        raise ValueError("paper smoke resolved root count drift")
    return execution


def smoke_execution_classification(
    *,
    baseline_policy: str = "required_by_formal_plan",
) -> dict[str, Any]:
    if baseline_policy not in SMOKE_BASELINE_POLICIES:
        raise ValueError("unsupported smoke baseline policy")
    reasons = list(SMOKE_INELIGIBILITY_REASONS)
    if baseline_policy == "omitted_for_smoke_regression":
        reasons.append("smoke_baseline_not_requested")
    return {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
        "ineligibility_reasons": reasons,
        "baseline_policy": baseline_policy,
    }


def execute_paper_smoke_suite(
    *,
    profile: PaperSmokeProfile,
    execution_plan: PaperSmokeExecutionPlan,
    catalog_manifest: Any,
    budget: Any,
    ai_api_configs: Mapping[str, Any],
    transport: Any,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    resume: bool = False,
    secret_values: Sequence[str] = (),
    launch_manifest: Mapping[str, Any],
    recovery_manifest: Mapping[str, Any] | None = None,
) -> Any:
    """复用 formal production callback 执行缩减后的 canonical roots。"""

    if execution_plan.profile_digest != profile.profile_digest:
        raise ValueError("smoke execution plan/profile identity mismatch")
    if execution_plan.baseline_policy != profile.baseline_policy:
        raise ValueError("smoke execution plan baseline policy mismatch")
    budget_approval = budget.quota_preflight.get("budget_approval")
    if not isinstance(budget_approval, Mapping):
        raise ValueError("smoke budget approval record is missing")
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
    from tokenshare.experiments.paper_formal_runner import execute_paper_formal_suite
    from tokenshare.experiments.paper_smoke_report import generate_paper_smoke_report

    recovery_documents = {}
    if recovery_manifest is not None:
        recovery_id = recovery_manifest.get("recovery_id")
        if not isinstance(recovery_id, str) or not recovery_id:
            raise ValueError("smoke recovery manifest recovery_id is required")
        recovery_documents[
            f"smoke_recoveries/{recovery_id}.json"
        ] = dict(recovery_manifest)

    result = execute_paper_formal_suite(
        dispatch_plans=execution_plan.dispatch_plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        budget_approval=budget_approval,
        output_root=execution_plan.output_root,
        ai_api_configs=ai_api_configs,
        transport=transport,
        real_transport=real_transport,
        hard_limits=hard_limits,
        resume=resume,
        root_case_filter=execution_plan.root_case_filter,
        execution_classification=smoke_execution_classification(
            baseline_policy=profile.baseline_policy,
        ),
        suite_id=profile.suite_id,
        pre_execution_documents={
            "smoke_profile.json": profile.to_dict(),
            "smoke_execution_plan.json": execution_plan.to_dict(),
            "smoke_launch_manifest.json": dict(launch_manifest),
        },
        recovery_documents=recovery_documents,
    )
    generate_paper_smoke_report(
        output_root=execution_plan.output_root,
        secret_values=secret_values,
    )
    FormalEvidenceStore(execution_plan.output_root)._refresh_evidence_manifest()
    return result


def replay_paper_smoke_suite(
    *,
    output_root: str | Path,
    expected_profile_digest: str | None = None,
) -> Any:
    """只读既有 evidence 重放 smoke；绝不接收或调用 provider。"""

    root = Path(output_root).resolve(strict=False)
    persisted_profile = json.loads(
        (root / "smoke_profile.json").read_text(encoding="utf-8")
    )
    if not isinstance(persisted_profile, Mapping):
        raise ValueError("persisted smoke profile is invalid")
    if (
        expected_profile_digest is not None
        and persisted_profile.get("profile_digest") != expected_profile_digest
    ):
        raise ValueError("replay smoke profile identity mismatch")
    suite = json.loads((root / "suite_manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(suite, Mapping)
        or suite.get("formal") is not False
        or suite.get("pilot_only") is not True
        or suite.get("regression_only") is not True
        or suite.get("paper_eligible") is not False
        or suite.get("execution_scope") != "smoke_suite"
    ):
        raise ValueError("replay input is not smoke evidence")
    from tokenshare.experiments.paper_formal_runner import replay_paper_formal_suite

    return replay_paper_formal_suite(output_root=root)


def _load_item(value: Any, *, strict_v3: bool = False) -> PaperSmokeItem:
    if not isinstance(value, Mapping):
        raise ValueError("paper smoke items must be JSON objects")
    if strict_v3 and set(value) != _V3_ITEM_FIELDS:
        raise ValueError("paper smoke v3 item fields do not match schema")
    selector = _required_mapping(value, "condition_selector")
    if not selector:
        raise ValueError("paper smoke condition selector must not be empty")
    unknown = set(selector).difference(_ALLOWED_SELECTOR_FIELDS)
    if unknown or "condition_id" in selector:
        raise ValueError("paper smoke condition selector contains unsupported fields")
    repeat_id = _required_non_negative_int(value, "repeat_id")
    return PaperSmokeItem(
        item_id=_required_string(value, "item_id"),
        experiment_id=_required_string(value, "experiment_id"),
        case_id=_required_string(value, "case_id"),
        repeat_id=repeat_id,
        condition_selector=dict(selector),
    )


def _condition_matches(condition: Any, selector: Mapping[str, Any]) -> bool:
    semantics = {
        field_name: getattr(condition, field_name, None)
        for field_name in _ALLOWED_SELECTOR_FIELDS
        if field_name
        not in {"dead_worker_count", "kill_progress_percent", "matrix_kind"}
    }
    death = _WORKER_DEATH_PATTERN.search(str(condition.condition_id))
    semantics["dead_worker_count"] = int(death.group("dead")) if death else None
    semantics["kill_progress_percent"] = (
        int(death.group("progress")) if death else None
    )
    semantics["matrix_kind"] = (
        "worker_death"
        if str(condition.fault_type) == "worker_death"
        else "rate_fault"
        if condition.experiment_id == "exp3_real_ai_fault_recovery"
        else None
    )
    return all(semantics.get(field_name) == expected for field_name, expected in selector.items())


def _required_mapping(body: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    value = body.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"paper smoke {field_name} must be an object")
    return value


def _required_string(body: Mapping[str, Any], field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"paper smoke {field_name} must be a non-empty string")
    return value


def _required_string_tuple(
    body: Mapping[str, Any], field_name: str
) -> tuple[str, ...]:
    value = body.get(field_name)
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"paper smoke {field_name} must be non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"paper smoke {field_name} contains duplicates")
    return tuple(value)


def _required_non_negative_int(body: Mapping[str, Any], field_name: str) -> int:
    value = body.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"paper smoke {field_name} must be a non-negative integer")
    return value


def _required_digest(body: Mapping[str, Any], field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", value
    ):
        raise ValueError(f"paper smoke {field_name} must be a sha256 digest")
    return value
