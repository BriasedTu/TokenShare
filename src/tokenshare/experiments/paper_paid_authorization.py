"""EPD-027 用户提供 paid receipt 的纯离线校验与 output marker 绑定。

该模块不读取 secret/env，不预留预算，不调用 provider，也不提供 receipt
签发接口。它只解析既有 receipt，并在完整校验后建立或验证本地 marker。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence


RECEIPT_SCHEMA_VERSION = "tokenshare.paid_execution_receipt.v1"
MARKER_SCHEMA_VERSION = "tokenshare.paid_output_binding.v1"
PAID_OUTPUT_BINDING_FILENAME = "paid_output_binding.v1.json"

PAID_SCOPES = frozenset(
    {
        "epd027_l3_capability_and_online_checks",
        "epd027_full_bank_acquisition",
        "exp1_full_online",
        "exp5_capability_smoke",
        "exp5_full_online",
    }
)

_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_digest",
        "scope",
        "authorized_plan_digest",
        "profile_digest",
        "budget_digest",
        "inventory_digest",
        "prompt_admission_profile_digest",
        "selected_experiments",
        "output_root_path_digest",
        "not_before",
        "expires_at",
        "user_approval_reference",
    }
)
_MARKER_BINDING_FIELDS = (
    "receipt_digest",
    "authorized_plan_digest",
    "profile_digest",
    "budget_digest",
    "inventory_digest",
    "prompt_admission_profile_digest",
    "output_root_path_digest",
)
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PaidAuthorizationError(ValueError):
    """receipt 或 output marker 未满足离线授权契约。"""


@dataclass(frozen=True, kw_only=True)
class PaidExecutionReceipt:
    schema_version: str
    receipt_digest: str
    scope: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    selected_experiments: tuple[str, ...]
    output_root_path_digest: str
    not_before: str
    expires_at: str
    user_approval_reference: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "receipt_digest": self.receipt_digest,
            "scope": self.scope,
            "authorized_plan_digest": self.authorized_plan_digest,
            "profile_digest": self.profile_digest,
            "budget_digest": self.budget_digest,
            "inventory_digest": self.inventory_digest,
            "prompt_admission_profile_digest": self.prompt_admission_profile_digest,
            "selected_experiments": list(self.selected_experiments),
            "output_root_path_digest": self.output_root_path_digest,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "user_approval_reference": self.user_approval_reference,
        }


@dataclass(frozen=True, kw_only=True)
class PaidOutputBindingMarker:
    schema_version: str
    marker_digest: str
    receipt_digest: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    output_root_path_digest: str

    def binding_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in _MARKER_BINDING_FIELDS}

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "marker_digest": self.marker_digest,
            **self.binding_dict(),
        }


@dataclass(frozen=True, kw_only=True)
class PaidAuthorizationValidation:
    receipt: PaidExecutionReceipt
    marker: PaidOutputBindingMarker
    output_mode: str
    authorization_state: str
    provider_dispatch_allowed: bool


def compute_receipt_digest(
    receipt: PaidExecutionReceipt | Mapping[str, object],
) -> str:
    """只排除 ``receipt_digest`` 本身，重算 canonical receipt digest。"""

    if isinstance(receipt, PaidExecutionReceipt):
        body = receipt.to_dict()
    elif isinstance(receipt, Mapping):
        body = dict(receipt)
    else:
        raise TypeError("receipt must be a PaidExecutionReceipt or mapping")
    body.pop("receipt_digest", None)
    return _digest_json(body)


def compute_output_binding_marker_digest(
    *,
    receipt_digest: str,
    authorized_plan_digest: str,
    profile_digest: str,
    budget_digest: str,
    inventory_digest: str,
    prompt_admission_profile_digest: str,
    output_root_path_digest: str,
) -> str:
    """从 3.7 节冻结的七个字段派生 marker digest。"""

    body = {
        "receipt_digest": receipt_digest,
        "authorized_plan_digest": authorized_plan_digest,
        "profile_digest": profile_digest,
        "budget_digest": budget_digest,
        "inventory_digest": inventory_digest,
        "prompt_admission_profile_digest": prompt_admission_profile_digest,
        "output_root_path_digest": output_root_path_digest,
    }
    for name, value in body.items():
        _require_digest(value, name)
    return _digest_json(body)


def output_root_path_digest(output_root: str | os.PathLike[str]) -> str:
    """绑定 canonical absolute output path；不要求目标已存在。"""

    path = Path(output_root)
    canonical = path.resolve(strict=False).as_posix()
    return _digest_json(
        {
            "schema_version": "tokenshare.output_root_path.v1",
            "canonical_absolute_path": canonical,
        }
    )


def validate_paid_execution_receipt(
    *,
    receipt: PaidExecutionReceipt | Mapping[str, object],
    requested_scope: str,
    authorized_plan_digest: str,
    profile_digest: str,
    budget_digest: str,
    inventory_digest: str,
    prompt_admission_profile_digest: str,
    selected_experiments: Sequence[str],
    output_root: str | os.PathLike[str],
    output_mode: str,
    action: str,
    allow_provider_calls: bool,
    now: datetime,
) -> PaidAuthorizationValidation:
    """校验用户提供的 receipt，并建立 ``new_run`` 或验证 ``resume`` marker。

    ``action='dispatch'`` 才可能返回 provider dispatch permission，且仍要求调用方
    单独传入 ``allow_provider_calls=True``。过期 receipt 只允许 matching marker 上的
    ``reconcile_close``，永不允许新 root、新 reservation 或新 dispatch。
    """

    parsed = _parse_receipt(receipt)
    _validate_receipt_identity(
        parsed,
        requested_scope=requested_scope,
        authorized_plan_digest=authorized_plan_digest,
        profile_digest=profile_digest,
        budget_digest=budget_digest,
        inventory_digest=inventory_digest,
        prompt_admission_profile_digest=prompt_admission_profile_digest,
        selected_experiments=selected_experiments,
        output_root=output_root,
    )
    not_before = _parse_timestamp(parsed.not_before, "not_before")
    expires_at = _parse_timestamp(parsed.expires_at, "expires_at")
    current = _aware_utc(now, "now")
    if expires_at <= not_before:
        raise PaidAuthorizationError("receipt expiry must be after not_before")
    if current < not_before:
        raise PaidAuthorizationError("receipt is not active yet")
    if type(allow_provider_calls) is not bool:
        raise PaidAuthorizationError("allow_provider_calls must be a bool")
    if output_mode not in {"new_run", "resume"}:
        raise PaidAuthorizationError("output_mode must be new_run or resume")
    if action not in {"dispatch", "reconcile_close"}:
        raise PaidAuthorizationError("action must be dispatch or reconcile_close")

    expired = current >= expires_at
    if expired and (action != "reconcile_close" or output_mode != "resume"):
        raise PaidAuthorizationError(
            "expired receipt permits only resume reconcile_close"
        )
    if action == "reconcile_close" and output_mode != "resume":
        raise PaidAuthorizationError("reconcile_close requires resume mode")
    if action == "dispatch" and not allow_provider_calls:
        raise PaidAuthorizationError("dispatch also requires allow_provider_calls")

    marker = _marker_from_validated_receipt(parsed)
    root = Path(output_root)
    if output_mode == "new_run":
        _establish_new_root_marker(root, marker)
    else:
        _verify_existing_root_marker(root, marker)

    provider_dispatch_allowed = action == "dispatch" and not expired
    return PaidAuthorizationValidation(
        receipt=parsed,
        marker=marker,
        output_mode=output_mode,
        authorization_state=(
            "dispatch_authorized"
            if provider_dispatch_allowed
            else "reconcile_close_only"
        ),
        provider_dispatch_allowed=provider_dispatch_allowed,
    )


def _parse_receipt(
    receipt: PaidExecutionReceipt | Mapping[str, object],
) -> PaidExecutionReceipt:
    if isinstance(receipt, PaidExecutionReceipt):
        raw = receipt.to_dict()
    elif isinstance(receipt, Mapping):
        raw = dict(receipt)
    else:
        raise PaidAuthorizationError("paid receipt must be a mapping")
    if frozenset(raw) != _RECEIPT_FIELDS:
        raise PaidAuthorizationError("paid receipt fields do not match v1 schema")
    if raw.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise PaidAuthorizationError("paid receipt schema_version is invalid")

    string_fields = _RECEIPT_FIELDS - {"selected_experiments"}
    for name in string_fields:
        _require_nonempty_string(raw.get(name), name)
    for name in (
        "receipt_digest",
        "authorized_plan_digest",
        "profile_digest",
        "budget_digest",
        "inventory_digest",
        "prompt_admission_profile_digest",
        "output_root_path_digest",
    ):
        _require_digest(raw[name], name)
    scope = str(raw["scope"])
    if scope not in PAID_SCOPES:
        raise PaidAuthorizationError("paid receipt scope is not supported")
    selected = _string_tuple(raw["selected_experiments"], "selected_experiments")
    parsed = PaidExecutionReceipt(
        schema_version=str(raw["schema_version"]),
        receipt_digest=str(raw["receipt_digest"]),
        scope=scope,
        authorized_plan_digest=str(raw["authorized_plan_digest"]),
        profile_digest=str(raw["profile_digest"]),
        budget_digest=str(raw["budget_digest"]),
        inventory_digest=str(raw["inventory_digest"]),
        prompt_admission_profile_digest=str(raw["prompt_admission_profile_digest"]),
        selected_experiments=selected,
        output_root_path_digest=str(raw["output_root_path_digest"]),
        not_before=str(raw["not_before"]),
        expires_at=str(raw["expires_at"]),
        user_approval_reference=str(raw["user_approval_reference"]),
    )
    if compute_receipt_digest(parsed) != parsed.receipt_digest:
        raise PaidAuthorizationError("paid receipt digest mismatch")
    return parsed


def _validate_receipt_identity(
    receipt: PaidExecutionReceipt,
    *,
    requested_scope: str,
    authorized_plan_digest: str,
    profile_digest: str,
    budget_digest: str,
    inventory_digest: str,
    prompt_admission_profile_digest: str,
    selected_experiments: Sequence[str],
    output_root: str | os.PathLike[str],
) -> None:
    expected = {
        "scope": requested_scope,
        "authorized_plan_digest": authorized_plan_digest,
        "profile_digest": profile_digest,
        "budget_digest": budget_digest,
        "inventory_digest": inventory_digest,
        "prompt_admission_profile_digest": prompt_admission_profile_digest,
        "output_root_path_digest": output_root_path_digest(output_root),
    }
    if requested_scope not in PAID_SCOPES:
        raise PaidAuthorizationError("requested paid scope is not supported")
    for name, value in expected.items():
        if getattr(receipt, name) != value:
            raise PaidAuthorizationError(f"paid receipt {name} mismatch")
    expected_selection = _string_tuple(selected_experiments, "selected_experiments")
    if receipt.selected_experiments != expected_selection:
        raise PaidAuthorizationError("paid receipt selected_experiments mismatch")


def _marker_from_validated_receipt(
    receipt: PaidExecutionReceipt,
) -> PaidOutputBindingMarker:
    binding = {name: str(getattr(receipt, name)) for name in _MARKER_BINDING_FIELDS}
    return PaidOutputBindingMarker(
        schema_version=MARKER_SCHEMA_VERSION,
        marker_digest=compute_output_binding_marker_digest(**binding),
        **binding,
    )


def _establish_new_root_marker(
    root: Path,
    marker: PaidOutputBindingMarker,
) -> None:
    if root.exists():
        raise PaidAuthorizationError("new_run requires an absent output root")
    try:
        root.mkdir(parents=False, exist_ok=False)
        marker_path = root / PAID_OUTPUT_BINDING_FILENAME
        with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                marker.to_dict(),
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except PaidAuthorizationError:
        raise
    except OSError as exc:
        raise PaidAuthorizationError("failed to establish create-new output marker") from exc


def _verify_existing_root_marker(
    root: Path,
    expected: PaidOutputBindingMarker,
) -> None:
    if not root.is_dir():
        raise PaidAuthorizationError("resume requires an existing output root directory")
    marker_path = root / PAID_OUTPUT_BINDING_FILENAME
    if not marker_path.is_file():
        raise PaidAuthorizationError("resume output root is partial: marker missing")
    try:
        raw = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaidAuthorizationError("resume output marker is unreadable") from exc
    if raw != expected.to_dict():
        raise PaidAuthorizationError("resume output marker conflicts with paid receipt")
    if compute_output_binding_marker_digest(**expected.binding_dict()) != expected.marker_digest:
        raise PaidAuthorizationError("resume output marker digest mismatch")


def _parse_timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaidAuthorizationError(f"{name} must be an ISO-8601 timestamp") from exc
    return _aware_utc(parsed, name)


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PaidAuthorizationError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PaidAuthorizationError(f"{name} must be a sequence of strings")
    result = tuple(value)
    if not result or any(not isinstance(item, str) or not item for item in result):
        raise PaidAuthorizationError(f"{name} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise PaidAuthorizationError(f"{name} must not contain duplicates")
    return result


def _require_nonempty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PaidAuthorizationError(f"{name} must be a non-empty string")


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise PaidAuthorizationError(f"{name} must be a canonical sha256 digest")


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
