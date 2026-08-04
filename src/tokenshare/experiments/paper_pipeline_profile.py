from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


PIPELINE_PROFILE_SCHEMA_VERSION = "tokenshare.epd027_pipeline_profile.v1"
PIPELINE_PROFILE_ID = "epd027_response_bank_paper_pipeline.v1"
PIPELINE_PROFILE_VERSION = 1
PROMPT_ADMISSION_PROFILE_ID = "tokenshare.prompt_admission.utf8_byte_upper.v1"
PROMPT_ADMISSION_PROFILE_VERSION = 1
PROMPT_ADMISSION_ALGORITHM = (
    "estimated_prompt_tokens=utf8_byte_length(canonical_body_bytes)"
    "+8*message_count+16"
)
PROMPT_ADMISSION_PROFILE_DIGEST = (
    "sha256:e693ef1c9dbf50aff36aaae1b14f7029c5954c6708a6570182e69a7051685d49"
)
TRACE_DELAY_POLICY = "logical_source_latency_1x"

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EPD027_PIPELINE_PROFILE_PATH = (
    _REPOSITORY_ROOT / "benchmarks" / "paper" / "epd027_pipeline_profile.v1.json"
)

_EXP2_WORKERS = (1, 3, 7, 10, 30, 50)
_EXP2_CASES = (
    ("early", "factor_v2_hard_034", 0),
    ("middle", "factor_v2_hard_122", 9),
    ("late", "factor_v2_hard_063", 1),
    ("no_factor", "factor_v2_hard_161", 44),
)


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineCondition:
    condition_id: str
    experiment_id: str
    evidence_class: str
    worker_count: int
    case_position: str
    case_id: str
    paper_difficulty: str
    active_selection_index: int
    repeat_id: int
    split_profile_id: str
    planned_first_ai_units: int
    provider_calls_upper: int
    max_concurrent_roots: int
    condition_digest: str


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineCase:
    case_id: str
    check_kind: str
    worker_count: int
    repeat_id: int
    max_retries: int
    planned_first_ai_units: int
    provider_calls_upper: int
    fault_rate_percent: int | None = None
    kill_progress_percent: int | None = None
    dead_worker_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "case_id": self.case_id,
            "check_kind": self.check_kind,
        }
        if self.fault_rate_percent is not None:
            body["fault_rate_percent"] = self.fault_rate_percent
        if self.kill_progress_percent is not None:
            body["kill_progress_percent"] = self.kill_progress_percent
        if self.dead_worker_count is not None:
            body["dead_worker_count"] = self.dead_worker_count
        body.update(
            {
                "worker_count": self.worker_count,
                "repeat_id": self.repeat_id,
                "max_retries": self.max_retries,
                "planned_first_ai_units": self.planned_first_ai_units,
                "provider_calls_upper": self.provider_calls_upper,
            }
        )
        return body


_EXPECTED_EXP3_CASES = (
    Exp3OnlineCase(
        case_id="factor_v2_easy_109",
        check_kind="false_positive",
        fault_rate_percent=100,
        worker_count=10,
        repeat_id=0,
        max_retries=2,
        planned_first_ai_units=2,
        provider_calls_upper=6,
    ),
    Exp3OnlineCase(
        case_id="factor_v2_easy_148",
        check_kind="worker_death",
        kill_progress_percent=50,
        dead_worker_count=1,
        worker_count=10,
        repeat_id=0,
        max_retries=2,
        planned_first_ai_units=2,
        provider_calls_upper=6,
    ),
)


@dataclass(frozen=True, kw_only=True)
class PipelineAuthorities:
    implementation_plan_path: Path
    implementation_plan_content_digest: str
    factorization_catalog_path: Path
    factorization_catalog_content_digest: str
    paper_suite_scale_profile_path: Path
    paper_suite_scale_profile_content_digest: str
    paper_suite_scale_profile_digest: str
    lean_catalog_path: Path
    lean_catalog_content_digest: str
    lean_readiness_path: Path
    lean_readiness_content_digest: str
    provider_config_path: Path
    provider_config_content_digest: str


@dataclass(frozen=True, kw_only=True)
class PromptAdmissionProfile:
    profile_id: str
    version: int
    algorithm: str
    canonical_body_schema: str
    includes: tuple[str, ...]
    max_prompt_tokens: int
    profile_digest: str


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineProfile:
    workers: tuple[int, ...]
    repeat_id: int
    planned_first_ai_units_per_root: int
    provider_calls_upper: int
    max_concurrent_roots: int
    conditions: tuple[Exp2OnlineCondition, ...]


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineProfile:
    provider_calls_upper: int
    cases: tuple[Exp3OnlineCase, ...]


@dataclass(frozen=True, kw_only=True)
class FactorCapabilityProfile:
    case_id: str
    split_policy: str
    stable_planned_ai_unit_id: str
    planned_first_ai_units: int
    initial_control: str
    initial_attempt_must_reject: bool
    max_retries: int
    replacement_calls_exact: int
    attempts_after_replacement_exact: int

    @property
    def max_attempts(self) -> int:
        return 1 + self.max_retries


@dataclass(frozen=True, kw_only=True)
class LeanCapabilityProfile:
    case_id: str
    selected_proof_node_id: str
    stable_planned_ai_unit_id: str
    planned_first_ai_units: int
    initial_control: str
    initial_attempt_must_reject: bool
    max_retries: int
    replacement_calls_exact: int
    attempts_after_replacement_exact: int

    @property
    def max_attempts(self) -> int:
        return 1 + self.max_retries


@dataclass(frozen=True, kw_only=True)
class CapabilityOnlineProfile:
    classification: str
    paper_eligible: bool
    factor: FactorCapabilityProfile
    lean: LeanCapabilityProfile
    root_count: int
    provider_calls_exact: int

    @property
    def selected_ai_units_per_root(self) -> int:
        return self.factor.planned_first_ai_units

    @property
    def max_attempts_per_selected_unit(self) -> int:
        return self.factor.max_attempts


@dataclass(frozen=True, kw_only=True)
class OnlineChecksProfile:
    max_concurrent_roots: int
    exp2: Exp2OnlineProfile
    exp3: Exp3OnlineProfile
    capability: CapabilityOnlineProfile


@dataclass(frozen=True, kw_only=True)
class MetricControls:
    exp2_429_or_timeout_union_fraction_max: Decimal
    exp2_union_denominator: str
    exp2_union_identity: str
    exp2_online_trace_same_case_intersection_min: int
    severe_speedup_ratio_min: Decimal
    severe_speedup_ratio_max: Decimal
    severe_opposed_trend_high: Decimal
    severe_opposed_trend_low: Decimal


@dataclass(frozen=True, kw_only=True)
class PaidAuthorization:
    allow_provider_calls_flag: bool
    required_receipt_schema: str
    offline_approval_authorizes_provider_write: bool
    provider_scopes: tuple[str, ...]
    output_mode_values: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class PaperPipelineBudget:
    capability_calls_exact: int
    exp2_calls_upper: int
    exp3_calls_upper: int
    online_checks_calls_upper: int
    planned_calls_upper: int
    ambiguous_reserve_calls: int
    calls_hard_limit: int
    prompt_tokens_per_call: int
    completion_tokens_per_call: int
    tokens_per_call: int
    tokens_hard_limit: int
    cny_per_call_reservation: Decimal
    cny_reservation_hard_limit: Decimal
    cny_absolute_hard_stop: Decimal
    max_in_flight_acquisition_capability: int
    max_in_flight_exp2_online: int
    unlimited_budget_provider_writes_allowed: bool
    prompt_admission_profile_digest: str
    budget_digest: str


@dataclass(frozen=True, kw_only=True)
class OfflineImplementationApproval:
    schema_version: str
    approval_kind: str
    approved_plan_digest: str
    approved_profile_digest: str


@dataclass(frozen=True, kw_only=True)
class PaperPipelineProfile:
    schema_version: str
    profile_id: str
    profile_version: int
    profile_digest: str
    authorities: PipelineAuthorities
    prompt_admission: PromptAdmissionProfile
    trace_delay_policy: str
    online_checks: OnlineChecksProfile
    metric_controls: MetricControls
    paid_authorization: PaidAuthorization
    budget: PaperPipelineBudget
    offline_approval: OfflineImplementationApproval

    @property
    def budget_digest(self) -> str:
        return self.budget.budget_digest

    @property
    def prompt_admission_profile_id(self) -> str:
        return self.prompt_admission.profile_id

    @property
    def prompt_admission_profile_version(self) -> int:
        return self.prompt_admission.version

    @property
    def prompt_admission_algorithm(self) -> str:
        return self.prompt_admission.algorithm

    @property
    def prompt_admission_profile_digest(self) -> str:
        return self.prompt_admission.profile_digest

    @property
    def prompt_admission_max_tokens(self) -> int:
        return self.prompt_admission.max_prompt_tokens

    @property
    def max_concurrent_roots(self) -> int:
        return self.online_checks.max_concurrent_roots

    @property
    def exp2(self) -> Exp2OnlineProfile:
        return self.online_checks.exp2

    @property
    def exp2_conditions(self) -> tuple[Exp2OnlineCondition, ...]:
        return self.exp2.conditions

    @property
    def exp3(self) -> Exp3OnlineProfile:
        return self.online_checks.exp3

    @property
    def exp3_cases(self) -> tuple[Exp3OnlineCase, ...]:
        return self.exp3.cases

    @property
    def capability(self) -> CapabilityOnlineProfile:
        return self.online_checks.capability

    @property
    def capability_factor_case_id(self) -> str:
        return self.capability.factor.case_id

    @property
    def capability_factor_stable_planned_ai_unit_id(self) -> str:
        return self.capability.factor.stable_planned_ai_unit_id

    @property
    def capability_factor_planned_first_ai_units(self) -> int:
        return self.capability.factor.planned_first_ai_units

    @property
    def capability_factor_max_retries(self) -> int:
        return self.capability.factor.max_retries

    @property
    def capability_factor_max_attempts(self) -> int:
        return self.capability.factor.max_attempts

    @property
    def capability_factor_replacement_calls_exact(self) -> int:
        return self.capability.factor.replacement_calls_exact

    @property
    def capability_lean_case_id(self) -> str:
        return self.capability.lean.case_id

    @property
    def capability_lean_selected_proof_node_id(self) -> str:
        return self.capability.lean.selected_proof_node_id

    @property
    def capability_lean_stable_planned_ai_unit_id(self) -> str:
        return self.capability.lean.stable_planned_ai_unit_id

    @property
    def capability_lean_planned_first_ai_units(self) -> int:
        return self.capability.lean.planned_first_ai_units

    @property
    def capability_lean_max_retries(self) -> int:
        return self.capability.lean.max_retries

    @property
    def capability_lean_max_attempts(self) -> int:
        return self.capability.lean.max_attempts

    @property
    def capability_lean_replacement_calls_exact(self) -> int:
        return self.capability.lean.replacement_calls_exact

    @property
    def capability_calls_exact(self) -> int:
        return self.budget.capability_calls_exact

    @property
    def exp2_calls_upper(self) -> int:
        return self.budget.exp2_calls_upper

    @property
    def exp3_calls_upper(self) -> int:
        return self.budget.exp3_calls_upper

    @property
    def online_checks_calls_upper(self) -> int:
        return self.budget.online_checks_calls_upper

    @property
    def planned_calls_upper(self) -> int:
        return self.budget.planned_calls_upper

    @property
    def ambiguous_reserve_calls(self) -> int:
        return self.budget.ambiguous_reserve_calls

    @property
    def calls_hard_limit(self) -> int:
        return self.budget.calls_hard_limit

    @property
    def tokens_per_call(self) -> int:
        return self.budget.tokens_per_call

    @property
    def tokens_hard_limit(self) -> int:
        return self.budget.tokens_hard_limit

    @property
    def cny_per_call_reservation(self) -> Decimal:
        return self.budget.cny_per_call_reservation

    @property
    def cny_reservation_hard_limit(self) -> Decimal:
        return self.budget.cny_reservation_hard_limit

    @property
    def cny_absolute_hard_stop(self) -> Decimal:
        return self.budget.cny_absolute_hard_stop

    def estimate_prompt_tokens(
        self, canonical_body_bytes: bytes, *, message_count: int
    ) -> int:
        if not isinstance(canonical_body_bytes, bytes):
            raise TypeError("canonical_body_bytes must be bytes")
        if isinstance(message_count, bool) or not isinstance(message_count, int):
            raise TypeError("message_count must be an integer")
        if message_count < 0:
            raise ValueError("message_count must be non-negative")
        return len(canonical_body_bytes) + 8 * message_count + 16


def load_paper_pipeline_profile(
    path: str | Path = DEFAULT_EPD027_PIPELINE_PROFILE_PATH,
) -> PaperPipelineProfile:
    profile_path = Path(path)
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to load EPD-027 pipeline profile") from exc
    body = _mapping(value, "pipeline profile")
    _require_exact_keys(
        body,
        {
            "schema_version",
            "profile_id",
            "profile_version",
            "authorities",
            "prompt_admission",
            "trace_delay_policy",
            "online_checks",
            "metric_controls",
            "paid_authorization",
            "budget",
            "offline_approval",
            "profile_digest",
        },
        "EPD-027 pipeline profile",
    )

    if body.get("schema_version") != PIPELINE_PROFILE_SCHEMA_VERSION:
        raise ValueError("EPD-027 pipeline profile schema drift")
    if body.get("profile_id") != PIPELINE_PROFILE_ID:
        raise ValueError("EPD-027 pipeline profile id drift")
    profile_version = _integer(body, "profile_version")
    if profile_version != PIPELINE_PROFILE_VERSION:
        raise ValueError("EPD-027 pipeline profile version drift")

    budget = _mapping(body.get("budget"), "budget")
    expected_budget_digest = compute_budget_digest(body)
    if budget.get("budget_digest") != expected_budget_digest:
        raise ValueError("EPD-027 pipeline budget digest drift")

    expected_profile_digest = compute_profile_digest(body)
    if body.get("profile_digest") != expected_profile_digest:
        raise ValueError("EPD-027 pipeline profile digest drift")

    prompt = _load_prompt_admission(
        _mapping(body.get("prompt_admission"), "prompt_admission")
    )
    authorities = _load_authorities(
        _mapping(body.get("authorities"), "authorities")
    )
    _validate_provider_config(authorities)
    online = _mapping(body.get("online_checks"), "online_checks")
    _require_exact_keys(
        online,
        {"max_concurrent_roots", "exp2", "exp3", "capability"},
        "online_checks",
    )
    max_concurrent_roots = _integer(online, "max_concurrent_roots")
    exp2 = _load_exp2_profile(online, max_concurrent_roots)
    exp3 = _load_exp3_profile(online)
    capability = _load_capability_profile(online)
    _validate_catalog_bindings(
        authorities=authorities,
        exp2_conditions=exp2.conditions,
        exp3_cases=exp3.cases,
        capability=capability,
    )
    _validate_lean_readiness(
        authorities=authorities,
        capability=capability,
    )
    metric_controls = _load_metric_controls(
        _mapping(body.get("metric_controls"), "metric_controls")
    )
    paid_authorization = _load_paid_authorization(
        _mapping(body.get("paid_authorization"), "paid_authorization")
    )
    parsed_budget = _load_budget(
        budget,
        exp2=exp2,
        exp3=exp3,
        capability=capability,
        expected_budget_digest=expected_budget_digest,
    )
    approval = _load_offline_approval(body, expected_profile_digest)
    if body.get("trace_delay_policy") != TRACE_DELAY_POLICY:
        raise ValueError("EPD-027 trace delay policy drift")

    return PaperPipelineProfile(
        schema_version=PIPELINE_PROFILE_SCHEMA_VERSION,
        profile_id=PIPELINE_PROFILE_ID,
        profile_version=profile_version,
        profile_digest=expected_profile_digest,
        authorities=authorities,
        prompt_admission=prompt,
        trace_delay_policy=TRACE_DELAY_POLICY,
        online_checks=OnlineChecksProfile(
            max_concurrent_roots=max_concurrent_roots,
            exp2=exp2,
            exp3=exp3,
            capability=capability,
        ),
        metric_controls=metric_controls,
        paid_authorization=paid_authorization,
        budget=parsed_budget,
        offline_approval=approval,
    )


def compute_budget_digest(value: Mapping[str, Any]) -> str:
    body = _mapping(value, "pipeline profile")
    budget = dict(_mapping(body.get("budget"), "budget"))
    budget.pop("budget_digest", None)
    prompt = _mapping(body.get("prompt_admission"), "prompt_admission")
    budget["prompt_admission_profile_digest"] = _prompt_admission_digest(
        prompt
    )
    budget["prompt_admission_control_digest"] = _digest_json(dict(prompt))
    capability = _mapping(
        _mapping(body.get("online_checks"), "online_checks").get("capability"),
        "online_checks.capability",
    )
    budget["capability_control_digest"] = _digest_json(dict(capability))
    return _digest_json(budget)


def compute_profile_digest(value: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(_mapping(value, "pipeline profile")))
    body.pop("profile_digest", None)
    prompt = dict(_mapping(body.get("prompt_admission"), "prompt_admission"))
    body["prompt_admission"] = prompt
    budget = dict(_mapping(body.get("budget"), "budget"))
    budget["prompt_admission_profile_digest"] = prompt["profile_digest"]
    budget["budget_digest"] = compute_budget_digest(body)
    body["budget"] = budget
    approval = dict(_mapping(body.get("offline_approval"), "offline_approval"))
    approval.pop("approved_profile_digest", None)
    body["offline_approval"] = approval
    return _digest_json(body)


def reject_offline_implementation_approval(value: Any) -> None:
    if isinstance(value, OfflineImplementationApproval) or (
        isinstance(value, Mapping)
        and value.get("approval_kind") == "offline_implementation"
    ):
        raise ValueError("offline plan approval is not a paid execution receipt")
    raise ValueError("a validated paid execution receipt is required")


def _load_prompt_admission(
    prompt: Mapping[str, Any],
) -> PromptAdmissionProfile:
    _require_exact_keys(
        prompt,
        {
            "profile_id",
            "version",
            "algorithm",
            "canonical_body_schema",
            "includes",
            "max_prompt_tokens",
            "profile_digest",
        },
        "prompt_admission",
    )
    version = _integer(prompt, "version")
    max_prompt_tokens = _integer(prompt, "max_prompt_tokens")
    includes = _string_tuple(prompt.get("includes"), "prompt_admission.includes")
    if prompt.get("profile_id") != PROMPT_ADMISSION_PROFILE_ID:
        raise ValueError("prompt admission profile id drift")
    if version != PROMPT_ADMISSION_PROFILE_VERSION:
        raise ValueError("prompt admission profile version drift")
    if prompt.get("algorithm") != PROMPT_ADMISSION_ALGORITHM:
        raise ValueError("prompt admission algorithm drift")
    if prompt.get("canonical_body_schema") != "canonical_json_utf8_v1":
        raise ValueError("prompt admission canonical body schema drift")
    if includes != (
        "system",
        "messages",
        "canonical_body_overhead",
        "unicode_utf8_byte_upper",
    ):
        raise ValueError("prompt admission includes drift")
    if max_prompt_tokens != 32_768:
        raise ValueError("prompt admission maximum drift")
    if _prompt_admission_digest(prompt) != PROMPT_ADMISSION_PROFILE_DIGEST:
        raise ValueError("prompt admission canonical digest drift")
    if prompt.get("profile_digest") != PROMPT_ADMISSION_PROFILE_DIGEST:
        raise ValueError("prompt admission stored digest drift")
    return PromptAdmissionProfile(
        profile_id=PROMPT_ADMISSION_PROFILE_ID,
        version=version,
        algorithm=PROMPT_ADMISSION_ALGORITHM,
        canonical_body_schema="canonical_json_utf8_v1",
        includes=includes,
        max_prompt_tokens=max_prompt_tokens,
        profile_digest=PROMPT_ADMISSION_PROFILE_DIGEST,
    )


def _prompt_admission_digest(prompt: Mapping[str, Any]) -> str:
    canonical = {
        "algorithm": prompt.get("algorithm"),
        "canonical_body_schema": prompt.get("canonical_body_schema"),
        "includes": prompt.get("includes"),
        "profile_id": prompt.get("profile_id"),
        "version": prompt.get("version"),
    }
    return _digest_json(canonical)


def _load_offline_approval(
    body: Mapping[str, Any], profile_digest: str
) -> OfflineImplementationApproval:
    value = _mapping(body.get("offline_approval"), "offline_approval")
    authorities = _mapping(body.get("authorities"), "authorities")
    _require_exact_keys(
        value,
        {
            "schema_version",
            "approval_kind",
            "approved_plan_digest",
            "approved_profile_digest",
        },
        "offline_approval",
    )
    if (
        value.get("schema_version")
        != "tokenshare.offline_implementation_approval.v1"
        or value.get("approval_kind") != "offline_implementation"
        or value.get("approved_plan_digest")
        != authorities.get("implementation_plan_content_digest")
        or value.get("approved_profile_digest") != profile_digest
    ):
        raise ValueError("offline implementation approval drift")
    return OfflineImplementationApproval(
        schema_version=str(value["schema_version"]),
        approval_kind=str(value["approval_kind"]),
        approved_plan_digest=str(value["approved_plan_digest"]),
        approved_profile_digest=profile_digest,
    )


def _load_authorities(authorities: Mapping[str, Any]) -> PipelineAuthorities:
    _require_exact_keys(
        authorities,
        {
            "implementation_plan_path",
            "implementation_plan_content_digest",
            "factorization_catalog_path",
            "factorization_catalog_content_digest",
            "paper_suite_scale_profile_path",
            "paper_suite_scale_profile_content_digest",
            "paper_suite_scale_profile_digest",
            "lean_catalog_path",
            "lean_catalog_content_digest",
            "lean_readiness_path",
            "lean_readiness_content_digest",
            "provider_config_path",
            "provider_config_content_digest",
        },
        "authorities",
    )
    exact_raw_pairs = (
        ("implementation_plan_path", "implementation_plan_content_digest"),
        ("factorization_catalog_path", "factorization_catalog_content_digest"),
        ("lean_catalog_path", "lean_catalog_content_digest"),
        ("provider_config_path", "provider_config_content_digest"),
    )
    semantic_pairs = (
        (
            "paper_suite_scale_profile_path",
            "paper_suite_scale_profile_content_digest",
        ),
        ("lean_readiness_path", "lean_readiness_content_digest"),
    )
    paths: dict[str, Path] = {}
    for path_field, digest_field in exact_raw_pairs + semantic_pairs:
        path = _repository_path(authorities.get(path_field), path_field)
        paths[path_field] = path
        try:
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(f"unable to read {path_field}") from exc
        declared = authorities.get(digest_field)
        if not isinstance(declared, str) or not declared.startswith("sha256:"):
            raise ValueError(f"{digest_field} must be a sha256 digest")
        if (path_field, digest_field) in exact_raw_pairs and declared != actual:
            raise ValueError(f"{digest_field} drift")
        # scale/readiness 的旧 content_digest 是 profile 内被签入的历史审计字段，
        # 不伪称 current raw。当前内容分别由 parsed profile digest 与下游
        # catalog/selection/matrix commitments 校验。
    parsed_scale_digest = authorities.get("paper_suite_scale_profile_digest")
    if not isinstance(parsed_scale_digest, str) or not parsed_scale_digest:
        raise ValueError("paper_suite_scale_profile_digest must be a string")
    return PipelineAuthorities(
        implementation_plan_path=paths["implementation_plan_path"],
        implementation_plan_content_digest=str(
            authorities["implementation_plan_content_digest"]
        ),
        factorization_catalog_path=paths["factorization_catalog_path"],
        factorization_catalog_content_digest=str(
            authorities["factorization_catalog_content_digest"]
        ),
        paper_suite_scale_profile_path=paths["paper_suite_scale_profile_path"],
        paper_suite_scale_profile_content_digest=str(
            authorities["paper_suite_scale_profile_content_digest"]
        ),
        paper_suite_scale_profile_digest=parsed_scale_digest,
        lean_catalog_path=paths["lean_catalog_path"],
        lean_catalog_content_digest=str(authorities["lean_catalog_content_digest"]),
        lean_readiness_path=paths["lean_readiness_path"],
        lean_readiness_content_digest=str(
            authorities["lean_readiness_content_digest"]
        ),
        provider_config_path=paths["provider_config_path"],
        provider_config_content_digest=str(
            authorities["provider_config_content_digest"]
        ),
    )


def _validate_provider_config(authorities: PipelineAuthorities) -> None:
    config = _load_json_object(
        authorities.provider_config_path, "DeepSeek v3 provider config"
    )
    entries = config.get("entries")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("DeepSeek v3 provider entry drift")
    entry = _mapping(entries[0], "DeepSeek v3 provider entry")
    if (
        config.get("schema_version") != "phase7.ai_api_executor_config.v1"
        or config.get("provider_family") != "deepseek"
        or config.get("executor_id") != "executor_ai_api_exp1_deepseek_v4_pro"
        or _mapping(config.get("defaults"), "provider defaults")
        != {
            "timeout_seconds": 600,
            "max_tokens": 300000,
            "stream": False,
            "max_provider_attempts": 1,
        }
        or entry.get("entry_id") != "deepseek_v4_pro_exp1_baseline"
        or entry.get("enabled") is not True
        or entry.get("base_url") != "https://api.deepseek.com"
        or entry.get("endpoint") != "/chat/completions"
        or entry.get("api_key_env") != "DEEPSEEK_API_KEY"
        or entry.get("model") != "deepseek-v4-pro"
        or entry.get("request_overrides")
        != {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
        or _mapping(config.get("local_concurrency"), "local_concurrency").get(
            "max_in_flight_global"
        )
        != 50
    ):
        raise ValueError("DeepSeek v3 provider config drift")


def _load_exp2_profile(
    online: Mapping[str, Any], max_concurrent_roots: int
) -> Exp2OnlineProfile:
    if max_concurrent_roots != 1:
        raise ValueError("EPD-027 max_concurrent_roots drift")
    exp2 = _mapping(online.get("exp2"), "online_checks.exp2")
    _require_exact_keys(
        exp2,
        {
            "workers",
            "repeat_id",
            "planned_first_ai_units_per_root",
            "provider_calls_upper",
            "conditions",
        },
        "online_checks.exp2",
    )
    worker_values = exp2.get("workers")
    if not isinstance(worker_values, list):
        raise ValueError("workers must be an array")
    workers = tuple(
        _integer_value(value, f"workers[{index}]")
        for index, value in enumerate(worker_values)
    )
    repeat_id = _integer(exp2, "repeat_id")
    planned_first_ai_units_per_root = _integer(
        exp2, "planned_first_ai_units_per_root"
    )
    provider_calls_upper = _integer(exp2, "provider_calls_upper")
    if (
        workers != _EXP2_WORKERS
        or repeat_id != 0
        or planned_first_ai_units_per_root != 20
        or provider_calls_upper != 480
    ):
        raise ValueError("EPD-027 Experiment 2 profile drift")
    records = exp2.get("conditions")
    if not isinstance(records, list) or len(records) != 24:
        raise ValueError("EPD-027 Experiment 2 condition count drift")
    result: list[Exp2OnlineCondition] = []
    for index, (worker, case) in enumerate(
        (
            (worker, case)
            for worker in _EXP2_WORKERS
            for case in _EXP2_CASES
        )
    ):
        position, case_id, selection_index = case
        expected = {
            "condition_id": f"epd027_exp2_online_w{worker}_{position}_r0",
            "experiment_id": "exp2_online_concurrency_check",
            "evidence_class": "online_real_provider",
            "worker_count": worker,
            "case_position": position,
            "case_id": case_id,
            "paper_difficulty": "hard",
            "active_selection_index": selection_index,
            "repeat_id": 0,
            "split_profile_id": "factorization.exp2_contiguous_20way.v1",
            "planned_first_ai_units": 20,
            "provider_calls_upper": 20,
            "max_concurrent_roots": 1,
        }
        record = _mapping(records[index], f"exp2.conditions[{index}]")
        _require_exact_keys(
            record,
            set(expected) | {"condition_digest"},
            f"exp2.conditions[{index}]",
        )
        for field in (
            "worker_count",
            "active_selection_index",
            "repeat_id",
            "planned_first_ai_units",
            "provider_calls_upper",
            "max_concurrent_roots",
        ):
            _integer(record, field)
        body = dict(record)
        digest = body.pop("condition_digest", None)
        if body != expected:
            raise ValueError("EPD-027 Experiment 2 condition sequence drift")
        if digest != _digest_json(expected):
            raise ValueError("EPD-027 Experiment 2 condition digest drift")
        result.append(
            Exp2OnlineCondition(
                condition_id=expected["condition_id"],
                experiment_id=expected["experiment_id"],
                evidence_class=expected["evidence_class"],
                worker_count=worker,
                case_position=position,
                case_id=case_id,
                paper_difficulty=expected["paper_difficulty"],
                active_selection_index=selection_index,
                repeat_id=0,
                split_profile_id=expected["split_profile_id"],
                planned_first_ai_units=20,
                provider_calls_upper=20,
                max_concurrent_roots=1,
                condition_digest=str(digest),
            )
        )
    return Exp2OnlineProfile(
        workers=workers,
        repeat_id=repeat_id,
        planned_first_ai_units_per_root=planned_first_ai_units_per_root,
        provider_calls_upper=provider_calls_upper,
        max_concurrent_roots=max_concurrent_roots,
        conditions=tuple(result),
    )


def _load_exp3_profile(online: Mapping[str, Any]) -> Exp3OnlineProfile:
    exp3 = _mapping(online.get("exp3"), "online_checks.exp3")
    _require_exact_keys(
        exp3, {"provider_calls_upper", "cases"}, "online_checks.exp3"
    )
    provider_calls_upper = _integer(exp3, "provider_calls_upper")
    if provider_calls_upper != 12:
        raise ValueError("EPD-027 Experiment 3 call upper drift")
    records = exp3.get("cases")
    if not isinstance(records, list) or len(records) != len(_EXPECTED_EXP3_CASES):
        raise ValueError("EPD-027 Experiment 3 case drift")
    parsed: list[Exp3OnlineCase] = []
    for index, expected in enumerate(_EXPECTED_EXP3_CASES):
        record = _mapping(records[index], f"exp3.cases[{index}]")
        expected_body = expected.to_dict()
        _require_exact_keys(record, set(expected_body), f"exp3.cases[{index}]")
        numeric_fields = (
            "worker_count",
            "repeat_id",
            "max_retries",
            "planned_first_ai_units",
            "provider_calls_upper",
        )
        for field in numeric_fields:
            _integer(record, field)
        if "fault_rate_percent" in expected_body:
            _integer(record, "fault_rate_percent")
        if "kill_progress_percent" in expected_body:
            _integer(record, "kill_progress_percent")
        if "dead_worker_count" in expected_body:
            _integer(record, "dead_worker_count")
        parsed_case = Exp3OnlineCase(
            case_id=str(record["case_id"]),
            check_kind=str(record["check_kind"]),
            worker_count=_integer(record, "worker_count"),
            repeat_id=_integer(record, "repeat_id"),
            max_retries=_integer(record, "max_retries"),
            planned_first_ai_units=_integer(record, "planned_first_ai_units"),
            provider_calls_upper=_integer(record, "provider_calls_upper"),
            fault_rate_percent=(
                _integer(record, "fault_rate_percent")
                if "fault_rate_percent" in record
                else None
            ),
            kill_progress_percent=(
                _integer(record, "kill_progress_percent")
                if "kill_progress_percent" in record
                else None
            ),
            dead_worker_count=(
                _integer(record, "dead_worker_count")
                if "dead_worker_count" in record
                else None
            ),
        )
        if parsed_case != expected:
            raise ValueError("EPD-027 Experiment 3 case drift")
        parsed.append(parsed_case)
    return Exp3OnlineProfile(
        provider_calls_upper=provider_calls_upper,
        cases=tuple(parsed),
    )


def _load_capability_profile(
    online: Mapping[str, Any],
) -> CapabilityOnlineProfile:
    capability = _mapping(online.get("capability"), "online_checks.capability")
    _require_exact_keys(
        capability,
        {
            "classification",
            "paper_eligible",
            "factor",
            "lean",
            "root_count",
            "provider_calls_exact",
        },
        "online_checks.capability",
    )
    factor = _mapping(capability.get("factor"), "online_checks.capability.factor")
    lean = _mapping(capability.get("lean"), "online_checks.capability.lean")
    common_keys = {
        "case_id",
        "stable_planned_ai_unit_id",
        "planned_first_ai_units",
        "initial_control",
        "initial_attempt_must_reject",
        "max_retries",
        "replacement_calls_exact",
        "attempts_after_replacement_exact",
    }
    _require_exact_keys(
        factor,
        common_keys | {"split_policy"},
        "online_checks.capability.factor",
    )
    _require_exact_keys(
        lean,
        common_keys | {"selected_proof_node_id"},
        "online_checks.capability.lean",
    )
    factor_profile = FactorCapabilityProfile(
        case_id=str(factor.get("case_id")),
        split_policy=str(factor.get("split_policy")),
        stable_planned_ai_unit_id=str(factor.get("stable_planned_ai_unit_id")),
        planned_first_ai_units=_integer(factor, "planned_first_ai_units"),
        initial_control=str(factor.get("initial_control")),
        initial_attempt_must_reject=_boolean(
            factor, "initial_attempt_must_reject"
        ),
        max_retries=_integer(factor, "max_retries"),
        replacement_calls_exact=_integer(factor, "replacement_calls_exact"),
        attempts_after_replacement_exact=_integer(
            factor, "attempts_after_replacement_exact"
        ),
    )
    lean_profile = LeanCapabilityProfile(
        case_id=str(lean.get("case_id")),
        selected_proof_node_id=str(lean.get("selected_proof_node_id")),
        stable_planned_ai_unit_id=str(lean.get("stable_planned_ai_unit_id")),
        planned_first_ai_units=_integer(lean, "planned_first_ai_units"),
        initial_control=str(lean.get("initial_control")),
        initial_attempt_must_reject=_boolean(lean, "initial_attempt_must_reject"),
        max_retries=_integer(lean, "max_retries"),
        replacement_calls_exact=_integer(lean, "replacement_calls_exact"),
        attempts_after_replacement_exact=_integer(
            lean, "attempts_after_replacement_exact"
        ),
    )
    profile = CapabilityOnlineProfile(
        classification=str(capability.get("classification")),
        paper_eligible=_boolean(capability, "paper_eligible"),
        factor=factor_profile,
        lean=lean_profile,
        root_count=_integer(capability, "root_count"),
        provider_calls_exact=_integer(capability, "provider_calls_exact"),
    )
    derived_calls = (
        profile.root_count
        * profile.selected_ai_units_per_root
        * profile.max_attempts_per_selected_unit
    )
    if (
        profile.classification != "new_real_capability_smoke"
        or profile.paper_eligible is not False
        or factor_profile.case_id != "factor_v2_easy_109"
        or factor_profile.split_policy
        != "deterministic_one_range_capability_split"
        or factor_profile.stable_planned_ai_unit_id != "range_0"
        or factor_profile.planned_first_ai_units != 1
        or factor_profile.initial_control != "forced_verification_rejection"
        or factor_profile.initial_attempt_must_reject is not True
        or factor_profile.max_retries != 1
        or factor_profile.replacement_calls_exact
        != factor_profile.planned_first_ai_units * factor_profile.max_retries
        or factor_profile.attempts_after_replacement_exact != 0
        or lean_profile.case_id
        != "lean_v2_simple_pure_logic_direct_prop_01"
        or lean_profile.selected_proof_node_id != "pure_simple_leaf_01"
        or lean_profile.stable_planned_ai_unit_id != "pure_simple_leaf_01"
        or lean_profile.planned_first_ai_units != 1
        or lean_profile.initial_control != "controlled_checker_rejection"
        or lean_profile.initial_attempt_must_reject is not True
        or lean_profile.max_retries != 1
        or lean_profile.replacement_calls_exact
        != lean_profile.planned_first_ai_units * lean_profile.max_retries
        or lean_profile.attempts_after_replacement_exact != 0
        or profile.root_count != 2
        or factor_profile.planned_first_ai_units
        != lean_profile.planned_first_ai_units
        or factor_profile.max_attempts != lean_profile.max_attempts
        or profile.provider_calls_exact != derived_calls
        or profile.provider_calls_exact != 4
    ):
        raise ValueError("EPD-027 capability profile drift")
    return profile


def _validate_catalog_bindings(
    *,
    authorities: PipelineAuthorities,
    exp2_conditions: tuple[Exp2OnlineCondition, ...],
    exp3_cases: tuple[Exp3OnlineCase, ...],
    capability: CapabilityOnlineProfile,
) -> None:
    rows = _load_jsonl_objects(
        authorities.factorization_catalog_path, "Factorization catalog"
    )
    by_id = {row.get("case_id"): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Factorization catalog case identity drift")

    from tokenshare.experiments.paper_suite_scale import (
        load_paper_suite_scale_profile,
    )

    scale = load_paper_suite_scale_profile(
        authorities.paper_suite_scale_profile_path
    )
    if scale.profile_digest != authorities.paper_suite_scale_profile_digest:
        raise ValueError("paper suite scale parsed digest drift")
    candidates = {
        difficulty: tuple(
            row for row in rows if row.get("paper_difficulty") == difficulty
        )
        for difficulty in ("easy", "medium", "hard")
    }
    selected = scale.select_factorization_cases(candidates)
    exp2_ids = tuple(
        str(row["case_id"])
        for row in selected["exp2_real_ai_scalability"]["hard"]
    )
    for condition in exp2_conditions[:4]:
        expected_position = next(
            item[0] for item in _EXP2_CASES if item[1] == condition.case_id
        )
        expected_index = next(
            item[2] for item in _EXP2_CASES if item[1] == condition.case_id
        )
        case = by_id.get(condition.case_id)
        if (
            case is None
            or case.get("factor_position_quantile") != expected_position
            or exp2_ids[expected_index] != condition.case_id
        ):
            raise ValueError("Experiment 2 catalog membership/position drift")
    exp3_ids = {
        str(row["case_id"])
        for row in selected["exp3_real_ai_fault_recovery"]["easy"]
    }
    required_exp3 = {case.case_id for case in exp3_cases}
    required_exp3.add(capability.factor.case_id)
    if not required_exp3 <= exp3_ids:
        raise ValueError("Experiment 3 catalog membership drift")


def _validate_lean_readiness(
    *, authorities: PipelineAuthorities, capability: CapabilityOnlineProfile
) -> None:
    lean = capability.lean
    lean_case_id = lean.case_id
    selected_proof_node_id = lean.selected_proof_node_id
    rows = _load_jsonl_objects(
        authorities.lean_catalog_path, "Lean lemma graph catalog"
    )
    matches = [row for row in rows if row.get("case_id") == lean_case_id]
    if len(matches) != 1:
        raise ValueError("Lean capability catalog membership drift")
    case = matches[0]
    lemma_graph = _mapping(case.get("lemma_graph"), "Lean lemma graph")
    nodes = lemma_graph.get("nodes")
    selected_nodes = (
        [
            _mapping(node, "Lean capability proof node")
            for node in nodes
            if isinstance(node, Mapping)
            and node.get("node_id") == selected_proof_node_id
        ]
        if isinstance(nodes, list)
        else []
    )
    dependency_order = _mapping(
        case.get("merge_plan_shape"), "Lean merge plan shape"
    ).get("dependency_order")
    if (
        case.get("domain") != "lean_proof"
        or case.get("paper_difficulty") != "simple"
        or case.get("topic_family") != "pure_logic"
        or case.get("preflight_status") != "passed"
        or case.get("expected_ai_unit_count") != 2
        or len(selected_nodes) != 1
        or selected_nodes[0].get("node_kind") != "leaf_sublemma"
        or lean.stable_planned_ai_unit_id != selected_proof_node_id
        or lean.planned_first_ai_units != 1
        or not isinstance(dependency_order, list)
        or not dependency_order
        or dependency_order[0] != selected_proof_node_id
    ):
        raise ValueError("Lean capability catalog readiness drift")

    readiness = _load_json_object(authorities.lean_readiness_path, "Lean readiness")
    cells = readiness.get("cells")
    if (
        readiness.get("schema_version")
        != "tokenshare.lean_task14_3x3_readiness.v1"
        or readiness.get("provider_calls_made") != 0
        or not isinstance(cells, list)
    ):
        raise ValueError("Lean readiness manifest drift")
    matching_cells = [
        _mapping(cell, "Lean readiness cell")
        for cell in cells
        if isinstance(cell, Mapping) and lean_case_id in cell.get("case_ids", [])
    ]
    if len(matching_cells) != 1:
        raise ValueError("Lean readiness case membership drift")
    budget_input = _mapping(
        readiness.get("task15_budget_input"), "Lean readiness task15 budget input"
    )
    for field_name in ("catalog_digest", "selection_digest", "matrix_digest"):
        value = budget_input.get(field_name)
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise ValueError(f"Lean readiness {field_name} commitment drift")
    if (
        budget_input.get("catalog_digest") != readiness.get("catalog_digest")
        or budget_input.get("matrix_digest") != readiness.get("matrix_digest")
        or budget_input.get("provider_calls_made") != 0
    ):
        raise ValueError("Lean readiness catalog/selection/matrix commitment drift")
    cell = matching_cells[0]
    evidence = _mapping(
        _mapping(cell.get("golden_evidence_by_case_id"), "golden evidence").get(
            lean_case_id
        ),
        "Lean capability golden evidence",
    )
    if lean_case_id not in cell.get("golden_case_ids", []) or any(
        evidence.get(field) != "passed"
        for field in (
            "checker_preflight",
            "child_proof_file_construction",
            "dependency_aware_merge",
            "deterministic_split",
            "root_recheck",
        )
    ):
        raise ValueError("Lean capability checker readiness drift")


def _load_metric_controls(value: Mapping[str, Any]) -> MetricControls:
    _require_exact_keys(
        value,
        {
            "exp2_429_or_timeout_union_fraction_max",
            "exp2_union_denominator",
            "exp2_union_identity",
            "exp2_online_trace_same_case_intersection_min",
            "severe_speedup_ratio_min",
            "severe_speedup_ratio_max",
            "severe_opposed_trend_high",
            "severe_opposed_trend_low",
        },
        "metric_controls",
    )
    profile = MetricControls(
        exp2_429_or_timeout_union_fraction_max=_decimal(
            value, "exp2_429_or_timeout_union_fraction_max"
        ),
        exp2_union_denominator=str(value.get("exp2_union_denominator")),
        exp2_union_identity=str(value.get("exp2_union_identity")),
        exp2_online_trace_same_case_intersection_min=_integer(
            value, "exp2_online_trace_same_case_intersection_min"
        ),
        severe_speedup_ratio_min=_decimal(value, "severe_speedup_ratio_min"),
        severe_speedup_ratio_max=_decimal(value, "severe_speedup_ratio_max"),
        severe_opposed_trend_high=_decimal(value, "severe_opposed_trend_high"),
        severe_opposed_trend_low=_decimal(value, "severe_opposed_trend_low"),
    )
    if profile != MetricControls(
        exp2_429_or_timeout_union_fraction_max=Decimal("0.2"),
        exp2_union_denominator="actual_first_provider_attempts_by_worker",
        exp2_union_identity="attempt_identity",
        exp2_online_trace_same_case_intersection_min=3,
        severe_speedup_ratio_min=Decimal("0.5"),
        severe_speedup_ratio_max=Decimal("2.0"),
        severe_opposed_trend_high=Decimal("1.1"),
        severe_opposed_trend_low=Decimal("0.9"),
    ):
        raise ValueError("EPD-027 metric controls drift")
    return profile


def _load_paid_authorization(value: Mapping[str, Any]) -> PaidAuthorization:
    _require_exact_keys(
        value,
        {
            "allow_provider_calls_flag",
            "required_receipt_schema",
            "offline_approval_authorizes_provider_write",
            "provider_scopes",
            "output_mode_values",
        },
        "paid_authorization",
    )
    profile = PaidAuthorization(
        allow_provider_calls_flag=_boolean(value, "allow_provider_calls_flag"),
        required_receipt_schema=str(value.get("required_receipt_schema")),
        offline_approval_authorizes_provider_write=_boolean(
            value, "offline_approval_authorizes_provider_write"
        ),
        provider_scopes=_string_tuple(
            value.get("provider_scopes"), "paid_authorization.provider_scopes"
        ),
        output_mode_values=_string_tuple(
            value.get("output_mode_values"),
            "paid_authorization.output_mode_values",
        ),
    )
    if profile != PaidAuthorization(
        allow_provider_calls_flag=True,
        required_receipt_schema="tokenshare.paid_execution_receipt.v1",
        offline_approval_authorizes_provider_write=False,
        provider_scopes=(
            "epd027_l3_capability_and_online_checks",
            "epd027_full_bank_acquisition",
            "exp1_full_online",
            "exp5_capability_smoke",
            "exp5_full_online",
        ),
        output_mode_values=("new-run", "resume"),
    ):
        raise ValueError("EPD-027 paid authorization controls drift")
    return profile


def _load_budget(
    budget: Mapping[str, Any],
    *,
    exp2: Exp2OnlineProfile,
    exp3: Exp3OnlineProfile,
    capability: CapabilityOnlineProfile,
    expected_budget_digest: str,
) -> PaperPipelineBudget:
    _require_exact_keys(
        budget,
        {
            "capability_calls_exact",
            "exp2_calls_upper",
            "exp3_calls_upper",
            "online_checks_calls_upper",
            "planned_calls_upper",
            "ambiguous_reserve_calls",
            "calls_hard_limit",
            "prompt_tokens_per_call",
            "completion_tokens_per_call",
            "tokens_per_call",
            "tokens_hard_limit",
            "cny_per_call_reservation",
            "cny_reservation_hard_limit",
            "cny_absolute_hard_stop",
            "max_in_flight_acquisition_capability",
            "max_in_flight_exp2_online",
            "unlimited_budget_provider_writes_allowed",
            "prompt_admission_profile_digest",
            "budget_digest",
        },
        "budget",
    )
    profile = PaperPipelineBudget(
        capability_calls_exact=_integer(budget, "capability_calls_exact"),
        exp2_calls_upper=_integer(budget, "exp2_calls_upper"),
        exp3_calls_upper=_integer(budget, "exp3_calls_upper"),
        online_checks_calls_upper=_integer(budget, "online_checks_calls_upper"),
        planned_calls_upper=_integer(budget, "planned_calls_upper"),
        ambiguous_reserve_calls=_integer(budget, "ambiguous_reserve_calls"),
        calls_hard_limit=_integer(budget, "calls_hard_limit"),
        prompt_tokens_per_call=_integer(budget, "prompt_tokens_per_call"),
        completion_tokens_per_call=_integer(
            budget, "completion_tokens_per_call"
        ),
        tokens_per_call=_integer(budget, "tokens_per_call"),
        tokens_hard_limit=_integer(budget, "tokens_hard_limit"),
        cny_per_call_reservation=_decimal(budget, "cny_per_call_reservation"),
        cny_reservation_hard_limit=_decimal(
            budget, "cny_reservation_hard_limit"
        ),
        cny_absolute_hard_stop=_decimal(budget, "cny_absolute_hard_stop"),
        max_in_flight_acquisition_capability=_integer(
            budget, "max_in_flight_acquisition_capability"
        ),
        max_in_flight_exp2_online=_integer(
            budget, "max_in_flight_exp2_online"
        ),
        unlimited_budget_provider_writes_allowed=_boolean(
            budget, "unlimited_budget_provider_writes_allowed"
        ),
        prompt_admission_profile_digest=str(
            budget.get("prompt_admission_profile_digest")
        ),
        budget_digest=str(budget.get("budget_digest")),
    )
    if (
        profile.capability_calls_exact != capability.provider_calls_exact
        or profile.capability_calls_exact != 4
        or profile.exp2_calls_upper != exp2.provider_calls_upper
        or profile.exp2_calls_upper != 480
        or profile.exp3_calls_upper != exp3.provider_calls_upper
        or profile.exp3_calls_upper != 12
        or profile.online_checks_calls_upper
        != profile.exp2_calls_upper + profile.exp3_calls_upper
        or profile.planned_calls_upper
        != profile.capability_calls_exact + profile.online_checks_calls_upper
        or profile.ambiguous_reserve_calls != 20
        or profile.calls_hard_limit
        != profile.planned_calls_upper + profile.ambiguous_reserve_calls
        or profile.calls_hard_limit != 516
        or profile.prompt_tokens_per_call != 32_768
        or profile.completion_tokens_per_call != 300_000
        or profile.tokens_per_call
        != profile.prompt_tokens_per_call + profile.completion_tokens_per_call
        or profile.tokens_hard_limit
        != profile.calls_hard_limit * profile.tokens_per_call
        or profile.tokens_hard_limit != 171_708_288
    ):
        raise ValueError("EPD-027 call/token budget arithmetic drift")
    expected_per_call_cny = Decimal(32_768) * Decimal("3.0") / Decimal(
        1_000_000
    ) + Decimal(300_000) * Decimal("6.0") / Decimal(1_000_000)
    if (
        profile.cny_per_call_reservation != expected_per_call_cny
        or profile.cny_reservation_hard_limit
        != Decimal(profile.calls_hard_limit) * profile.cny_per_call_reservation
        or profile.cny_reservation_hard_limit != Decimal("979.524864")
        or profile.cny_absolute_hard_stop != Decimal("1000.0")
        or profile.max_in_flight_acquisition_capability != 10
        or profile.max_in_flight_exp2_online != 50
        or profile.unlimited_budget_provider_writes_allowed is not False
        or profile.prompt_admission_profile_digest
        != PROMPT_ADMISSION_PROFILE_DIGEST
        or profile.budget_digest != expected_budget_digest
    ):
        raise ValueError("EPD-027 CNY/concurrency budget arithmetic drift")
    return profile


def _digest_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _repository_path(value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    path = Path(value)
    return path if path.is_absolute() else _REPOSITORY_ROOT / path


def _load_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load {label}") from exc
    return _mapping(value, label)


def _load_jsonl_objects(path: Path, label: str) -> tuple[Mapping[str, Any], ...]:
    try:
        values = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load {label}") from exc
    if not all(isinstance(value, Mapping) for value in values):
        raise ValueError(f"{label} entries must be objects")
    return values


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field_name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field_name} schema drift")


def _integer(value: Mapping[str, Any], field_name: str) -> int:
    return _integer_value(value.get(field_name), field_name)


def _integer_value(item: Any, field_name: str) -> int:
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field_name} must be an integer")
    return item


def _boolean(value: Mapping[str, Any], field_name: str) -> bool:
    item = value.get(field_name)
    if not isinstance(item, bool):
        raise ValueError(f"{field_name} must be a bool")
    return item


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _decimal(value: Mapping[str, Any], field_name: str) -> Decimal:
    item = value.get(field_name)
    if not isinstance(item, str):
        raise ValueError(f"{field_name} must be a decimal string")
    try:
        return Decimal(item)
    except Exception as exc:
        raise ValueError(f"{field_name} must be a decimal string") from exc


__all__ = [
    "CapabilityOnlineProfile",
    "DEFAULT_EPD027_PIPELINE_PROFILE_PATH",
    "Exp2OnlineCondition",
    "Exp2OnlineProfile",
    "Exp3OnlineCase",
    "Exp3OnlineProfile",
    "FactorCapabilityProfile",
    "LeanCapabilityProfile",
    "MetricControls",
    "OnlineChecksProfile",
    "PROMPT_ADMISSION_ALGORITHM",
    "PROMPT_ADMISSION_PROFILE_DIGEST",
    "PROMPT_ADMISSION_PROFILE_ID",
    "PROMPT_ADMISSION_PROFILE_VERSION",
    "OfflineImplementationApproval",
    "PaidAuthorization",
    "PaperPipelineBudget",
    "PaperPipelineProfile",
    "PipelineAuthorities",
    "PromptAdmissionProfile",
    "compute_budget_digest",
    "compute_profile_digest",
    "load_paper_pipeline_profile",
    "reject_offline_implementation_approval",
]
