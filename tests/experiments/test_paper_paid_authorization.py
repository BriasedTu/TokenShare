"""Task 26 paid-receipt/marker 离线契约测试。

本文件中的 receipt-shaped 字典全部是 synthetic、non-authorizing test fixtures；
它们不是用户授权、真实付费 receipt 或可发布 evidence，且只写入 pytest 临时目录。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import ast
import inspect
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_paid_authorization import (
    PAID_OUTPUT_BINDING_FILENAME,
    PaidAuthorizationError,
    compute_output_binding_marker_digest,
    compute_receipt_digest,
    output_root_path_digest,
    validate_paid_execution_receipt,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
L3_SCOPE = "epd027_l3_capability_and_online_checks"
L3_SELECTION = ("l3_capability", "exp2_online", "exp3_online")


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _synthetic_receipt_shape(output_root: Path, *, scope: str = L3_SCOPE) -> dict[str, object]:
    """生成明确不构成真实授权的 receipt-shaped 测试输入。"""

    selected = (
        L3_SELECTION
        if scope == L3_SCOPE
        else {
            "epd027_full_bank_acquisition": ("exp2", "exp3", "exp4"),
            "exp1_full_online": ("exp1",),
            "exp5_capability_smoke": ("exp5_capability",),
            "exp5_full_online": ("exp5",),
        }[scope]
    )
    body: dict[str, object] = {
        "schema_version": "tokenshare.paid_execution_receipt.v1",
        "receipt_digest": "sha256:" + "0" * 64,
        "scope": scope,
        "authorized_plan_digest": _digest({"synthetic": "task25-plan"}),
        "profile_digest": _digest({"synthetic": "profile"}),
        "budget_digest": _digest({"synthetic": "budget"}),
        "inventory_digest": _digest({"synthetic": "inventory"}),
        "prompt_admission_profile_digest": _digest({"synthetic": "admission"}),
        "selected_experiments": list(selected),
        "output_root_path_digest": output_root_path_digest(output_root),
        "not_before": "2026-08-03T00:00:00Z",
        "expires_at": "2026-08-04T00:00:00Z",
        "user_approval_reference": "synthetic-test-only-not-real-approval",
    }
    canonical = {key: value for key, value in body.items() if key != "receipt_digest"}
    body["receipt_digest"] = _digest(canonical)
    return body


def _validate(
    receipt: dict[str, object],
    output_root: Path,
    *,
    requested_scope: str | None = None,
    selected_experiments: tuple[str, ...] | None = None,
    output_mode: str = "new_run",
    action: str = "dispatch",
    allow_provider_calls: bool = True,
    now: datetime = NOW,
    expected_overrides: dict[str, object] | None = None,
):
    expected = expected_overrides or {}
    return validate_paid_execution_receipt(
        receipt=receipt,
        requested_scope=requested_scope or str(receipt["scope"]),
        authorized_plan_digest=str(
            expected.get("authorized_plan_digest", receipt["authorized_plan_digest"])
        ),
        profile_digest=str(expected.get("profile_digest", receipt["profile_digest"])),
        budget_digest=str(expected.get("budget_digest", receipt["budget_digest"])),
        inventory_digest=str(
            expected.get("inventory_digest", receipt["inventory_digest"])
        ),
        prompt_admission_profile_digest=str(
            expected.get(
                "prompt_admission_profile_digest",
                receipt["prompt_admission_profile_digest"],
            )
        ),
        selected_experiments=selected_experiments
        or tuple(receipt["selected_experiments"]),
        output_root=output_root,
        output_mode=output_mode,
        action=action,
        allow_provider_calls=allow_provider_calls,
        now=now,
    )


def _assert_no_secret_reserve_dispatch_surface() -> None:
    source = inspect.getsource(
        __import__(
            "tokenshare.experiments.paper_paid_authorization", fromlist=["*"]
        )
    ).lower()
    for forbidden in ("os.environ", "getenv(", ".reserve(", ".dispatch(", "socket"):
        assert forbidden not in source


def test_receipt_digest_hashes_canonical_receipt_excluding_receipt_digest(
    tmp_path: Path,
) -> None:
    receipt = _synthetic_receipt_shape(tmp_path / "new")
    expected = _digest(
        {key: value for key, value in receipt.items() if key != "receipt_digest"}
    )
    assert compute_receipt_digest(receipt) == expected

    changed_digest_only = {**receipt, "receipt_digest": "sha256:" + "f" * 64}
    assert compute_receipt_digest(changed_digest_only) == expected
    changed_approval_ref = {**receipt, "user_approval_reference": "different-synthetic-ref"}
    assert compute_receipt_digest(changed_approval_ref) != expected


def test_receipt_binds_authorized_plan_inventory_and_prompt_admission_digests(
    tmp_path: Path,
) -> None:
    fields = (
        "authorized_plan_digest",
        "profile_digest",
        "budget_digest",
        "inventory_digest",
        "prompt_admission_profile_digest",
        "selected_experiments",
        "output_root_path_digest",
    )
    for index, field in enumerate(fields):
        root = tmp_path / f"bound-{index}"
        receipt = _synthetic_receipt_shape(root)
        kwargs: dict[str, object] = {}
        if field == "selected_experiments":
            kwargs[field] = ("different-selection",)
        elif field == "output_root_path_digest":
            root = tmp_path / "different-output-root"
        else:
            kwargs[field] = _digest({"different": field})
        with pytest.raises(PaidAuthorizationError):
            validate_paid_execution_receipt(
                receipt=receipt,
                requested_scope=L3_SCOPE,
                authorized_plan_digest=str(
                    kwargs.get("authorized_plan_digest", receipt["authorized_plan_digest"])
                ),
                profile_digest=str(kwargs.get("profile_digest", receipt["profile_digest"])),
                budget_digest=str(kwargs.get("budget_digest", receipt["budget_digest"])),
                inventory_digest=str(
                    kwargs.get("inventory_digest", receipt["inventory_digest"])
                ),
                prompt_admission_profile_digest=str(
                    kwargs.get(
                        "prompt_admission_profile_digest",
                        receipt["prompt_admission_profile_digest"],
                    )
                ),
                selected_experiments=tuple(
                    kwargs.get("selected_experiments", receipt["selected_experiments"])
                ),
                output_root=root,
                output_mode="new_run",
                action="dispatch",
                allow_provider_calls=True,
                now=NOW,
            )


def test_same_valid_receipt_supports_new_run_then_resume_invocation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-receipt"
    receipt = _synthetic_receipt_shape(root)
    new_result = _validate(receipt, root)
    resumed = _validate(receipt, root, output_mode="resume")
    assert resumed.marker.marker_digest == new_result.marker.marker_digest


def test_new_run_requires_absent_root_and_derives_marker_after_receipt_validation(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(PaidAuthorizationError):
        _validate(_synthetic_receipt_shape(existing), existing)

    root = tmp_path / "validate-first"
    invalid = _synthetic_receipt_shape(root)
    invalid["receipt_digest"] = "sha256:" + "1" * 64
    with pytest.raises(PaidAuthorizationError):
        _validate(invalid, root)
    assert not root.exists()

    receipt = _synthetic_receipt_shape(root)
    result = _validate(receipt, root)
    assert (root / PAID_OUTPUT_BINDING_FILENAME).is_file()
    assert result.marker.receipt_digest == receipt["receipt_digest"]


def test_marker_digest_derives_only_receipt_plan_profile_budget_inventory_admission_path(
    tmp_path: Path,
) -> None:
    receipt = _synthetic_receipt_shape(tmp_path / "marker")
    exact_body = {
        "receipt_digest": receipt["receipt_digest"],
        "authorized_plan_digest": receipt["authorized_plan_digest"],
        "profile_digest": receipt["profile_digest"],
        "budget_digest": receipt["budget_digest"],
        "inventory_digest": receipt["inventory_digest"],
        "prompt_admission_profile_digest": receipt[
            "prompt_admission_profile_digest"
        ],
        "output_root_path_digest": receipt["output_root_path_digest"],
    }
    actual = compute_output_binding_marker_digest(**exact_body)
    assert actual == _digest(exact_body)


def test_resume_requires_existing_exact_marker(tmp_path: Path) -> None:
    root = tmp_path / "resume"
    receipt = _synthetic_receipt_shape(root)
    root.mkdir()
    with pytest.raises(PaidAuthorizationError):
        _validate(receipt, root, output_mode="resume")

    root.rmdir()
    _validate(receipt, root)
    marker_path = root / PAID_OUTPUT_BINDING_FILENAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["inventory_digest"] = _digest({"conflicting": "inventory"})
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(PaidAuthorizationError):
        _validate(receipt, root, output_mode="resume")


def test_inventory_or_admission_change_rejects_receipt_before_secret_reserve_dispatch(
    tmp_path: Path,
) -> None:
    for field in ("inventory_digest", "prompt_admission_profile_digest"):
        root = tmp_path / field
        receipt = _synthetic_receipt_shape(root)
        expected = receipt[field]
        receipt[field] = _digest({"drift": field})
        receipt["receipt_digest"] = compute_receipt_digest(receipt)
        with pytest.raises(PaidAuthorizationError):
            _validate(
                receipt,
                root,
                expected_overrides={field: expected},
            )
        assert not root.exists()
    _assert_no_secret_reserve_dispatch_surface()


def test_partial_or_conflicting_root_rejected_before_secret_reserve_dispatch(
    tmp_path: Path,
) -> None:
    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    (partial_root / "partial.json").write_text("{}", encoding="utf-8")
    receipt = _synthetic_receipt_shape(partial_root)
    with pytest.raises(PaidAuthorizationError):
        _validate(receipt, partial_root, output_mode="resume")

    conflicting_root = tmp_path / "conflict"
    first = _synthetic_receipt_shape(conflicting_root)
    _validate(first, conflicting_root)
    second = deepcopy(first)
    second["authorized_plan_digest"] = _digest({"conflict": "plan"})
    second["receipt_digest"] = compute_receipt_digest(second)
    with pytest.raises(PaidAuthorizationError):
        _validate(second, conflicting_root, output_mode="resume")


def test_expired_receipt_allows_pure_reconcile_close_but_never_new_dispatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expired"
    receipt = _synthetic_receipt_shape(root)
    _validate(receipt, root)
    expired_now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    result = _validate(
        receipt,
        root,
        output_mode="resume",
        action="reconcile_close",
        allow_provider_calls=False,
        now=expired_now,
    )
    assert result.provider_dispatch_allowed is False
    assert result.authorization_state == "reconcile_close_only"

    for mode, path in (("new_run", tmp_path / "expired-new"), ("resume", root)):
        shaped = _synthetic_receipt_shape(path) if mode == "new_run" else receipt
        with pytest.raises(PaidAuthorizationError):
            _validate(shaped, path, output_mode=mode, now=expired_now)


def test_offline_approval_is_rejected(tmp_path: Path) -> None:
    offline_approval = {
        "approval_id": "epd027_offline_implementation_only",
        "approved": True,
        "approved_profile_digest": _digest({"synthetic": "profile"}),
    }
    with pytest.raises(PaidAuthorizationError):
        validate_paid_execution_receipt(
            receipt=offline_approval,
            requested_scope=L3_SCOPE,
            authorized_plan_digest=_digest({"synthetic": "plan"}),
            profile_digest=_digest({"synthetic": "profile"}),
            budget_digest=_digest({"synthetic": "budget"}),
            inventory_digest=_digest({"synthetic": "inventory"}),
            prompt_admission_profile_digest=_digest({"synthetic": "admission"}),
            selected_experiments=L3_SELECTION,
            output_root=tmp_path / "offline",
            output_mode="new_run",
            action="dispatch",
            allow_provider_calls=True,
            now=NOW,
        )


def test_l3_receipt_cannot_authorize_full_bank_exp1_or_either_exp5_scope(
    tmp_path: Path,
) -> None:
    forbidden_scopes = (
        "epd027_full_bank_acquisition",
        "exp1_full_online",
        "exp5_capability_smoke",
        "exp5_full_online",
    )
    for index, requested_scope in enumerate(forbidden_scopes):
        root = tmp_path / f"scope-{index}"
        receipt = _synthetic_receipt_shape(root)
        with pytest.raises(PaidAuthorizationError):
            _validate(receipt, root, requested_scope=requested_scope)


def test_exp5_capability_receipt_cannot_authorize_exp5_full_online(
    tmp_path: Path,
) -> None:
    root = tmp_path / "exp5-capability"
    receipt = _synthetic_receipt_shape(root, scope="exp5_capability_smoke")
    with pytest.raises(PaidAuthorizationError):
        _validate(receipt, root, requested_scope="exp5_full_online")


def test_validator_has_no_command_or_function_to_mint_receipt() -> None:
    module = __import__(
        "tokenshare.experiments.paper_paid_authorization", fromlist=["*"]
    )
    source = inspect.getsource(module)
    tree = ast.parse(source)
    function_names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not any(
        forbidden in name
        for name in function_names
        for forbidden in ("mint", "approve", "record_paid", "create_receipt")
    )
    assert "argparse" not in source
    assert "__main__" not in source


def test_allow_provider_calls_flag_is_also_required(tmp_path: Path) -> None:
    root = tmp_path / "flag"
    receipt = _synthetic_receipt_shape(root)
    with pytest.raises(PaidAuthorizationError):
        _validate(receipt, root, allow_provider_calls=False)
    assert not root.exists()


def test_exp4_excluded_results_first_scopes_are_explicit_and_not_generic() -> None:
    """Exp4 排除后的两种正式规模必须各有精确 paid scope。"""

    from tokenshare.experiments.paper_paid_authorization import PAID_SCOPES
    from tokenshare.experiments.paper_formal_gate import (
        selected_experiments_for_provider_scope,
    )

    expected = {
        "results_first_representative_exp1_exp3_exp5",
        "results_first_full_exp1_exp3_exp5",
    }
    assert expected.issubset(PAID_SCOPES)
    for scope in expected:
        assert selected_experiments_for_provider_scope(scope) == ("exp1", "exp5")
