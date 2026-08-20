from __future__ import annotations

import importlib.util
import socket
import sys
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from typing import Callable
from unittest.mock import patch


StageAResult = dict[str, object]
StageABuilder = Callable[[Path], StageAResult]


class SingleAttemptStageACache:
    """跨测试 consumer 身份共享的一次性 Stage-A 构建状态。"""

    def __init__(self) -> None:
        self._attempted = False
        self._base: Path | None = None
        self._value: StageAResult | None = None

    @property
    def build_count(self) -> int:
        return int(self._attempted)

    def get(self, base: Path, *, builder: StageABuilder) -> StageAResult:
        requested_base = Path(base)
        if self._value is not None:
            if requested_base != self._base:
                raise AssertionError("shared r13 Stage-A base identity drift")
            return self._value
        if self._attempted:
            raise AssertionError("shared r13 Stage-A already attempted without a result")
        self._attempted = True
        self._base = requested_base
        self._value = builder(requested_base)
        return self._value


def _build_r13_full_stage_a(base: Path) -> StageAResult:
    """用真实 typed seam 构造并重载一次完整 Stage-A inventory。"""

    script = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid_representative_postfix_restore_overlay.py"
    )
    spec = importlib.util.spec_from_file_location(
        "r13_shared_full_stage_a_overlay",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    from tokenshare.executors import ai_api_transport
    from tokenshare.experiments import (
        paper_catalog,
        paper_formal_plan,
        paper_formal_runner,
        run_paper_pipeline,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        FactorizationRuntimeAdapter,
    )
    from tokenshare.experiments.lean_paper_adapter import LeanRuntimeAdapter
    from tokenshare.plugins.factorization import validator as factor_validator
    from tokenshare.plugins.lean_proof import checker as lean_checker

    base.mkdir(parents=False, exist_ok=False)
    paths = module.ProviderZeroPreparedInventoryPaths(
        source_v1_root=module.DEFAULT_SOURCE_V1_ROOT,
        output_inventory_root=base / "fresh-inventory",
        planning_artifact_root=base / "fresh-inventory-planning",
    )
    calls = {
        "freeze_inventory": 0,
        "persist_inventory": 0,
        "reload_inventory": 0,
        "factor_request_builder": 0,
        "lean_request_builder": 0,
        "outbound_prepare": 0,
    }
    forbidden = {
        name: 0
        for name in (
            "catalog_builder",
            "full_snapshot_builder",
            "full_authority_builder",
            "provider",
            "network",
            "execution",
            "checker",
            "verifier",
        )
    }
    loaded_inventory: list[object] = []
    outbound_by_digest: dict[str, object] = {}
    original_freeze = paper_formal_plan.freeze_paper_formal_prepared_request_inventory
    original_persist = paper_formal_plan.persist_formal_prepared_request_inventory
    original_reload = paper_formal_plan.load_formal_prepared_request_inventory
    original_factor_build = FactorizationRuntimeAdapter.build_execution_request
    original_lean_build = LeanRuntimeAdapter.build_planning_execution_request
    original_outbound = paper_formal_plan.prepare_ai_api_outbound_request

    def freeze_spy(**kwargs: object) -> object:
        calls["freeze_inventory"] += 1
        return original_freeze(**kwargs)

    def persist_spy(**kwargs: object) -> object:
        calls["persist_inventory"] += 1
        return original_persist(**kwargs)

    def reload_spy(**kwargs: object) -> object:
        calls["reload_inventory"] += 1
        inventory = original_reload(**kwargs)
        loaded_inventory.append(inventory)
        return inventory

    def factor_spy(self: object, *args: object, **kwargs: object) -> object:
        calls["factor_request_builder"] += 1
        return original_factor_build(self, *args, **kwargs)

    def lean_spy(self: object, *args: object, **kwargs: object) -> object:
        calls["lean_request_builder"] += 1
        return original_lean_build(self, *args, **kwargs)

    def outbound_spy(*args: object, **kwargs: object) -> object:
        calls["outbound_prepare"] += 1
        outbound = original_outbound(*args, **kwargs)
        prepared = outbound.prepared_request
        previous = outbound_by_digest.setdefault(
            prepared.inference_request_digest,
            outbound,
        )
        assert outbound == previous
        return outbound

    def bomb(name: str):
        def callback(*_args: object, **_kwargs: object) -> object:
            forbidden[name] += 1
            raise AssertionError(f"forbidden A boundary reached: {name}")

        return callback

    with ExitStack() as stack:
        for owner, name, replacement in (
            (paper_formal_plan, "freeze_paper_formal_prepared_request_inventory", freeze_spy),
            (paper_formal_plan, "persist_formal_prepared_request_inventory", persist_spy),
            (paper_formal_plan, "load_formal_prepared_request_inventory", reload_spy),
            (FactorizationRuntimeAdapter, "build_execution_request", factor_spy),
            (LeanRuntimeAdapter, "build_planning_execution_request", lean_spy),
            (paper_formal_plan, "prepare_ai_api_outbound_request", outbound_spy),
            (paper_catalog, "load_paper_catalogs", bomb("catalog_builder")),
            (
                paper_formal_plan,
                "freeze_paper_formal_plan_snapshot",
                bomb("full_snapshot_builder"),
            ),
            (
                run_paper_pipeline,
                "build_results_first_execution_authority",
                bomb("full_authority_builder"),
            ),
            (
                run_paper_pipeline,
                "ResultsFirstExecutionAuthority",
                bomb("full_authority_builder"),
            ),
            (
                ai_api_transport.UrlLibDeepSeekTransport,
                "post_chat_completion",
                bomb("provider"),
            ),
            (
                ai_api_transport.UrlLibSiliconFlowTransport,
                "post_chat_completion",
                bomb("provider"),
            ),
            (paper_formal_runner, "execute_paper_formal_suite", bomb("execution")),
            (lean_checker, "check_lean_proof", bomb("checker")),
            (factor_validator, "verify_range_result", bomb("verifier")),
            (socket, "create_connection", bomb("network")),
            (socket, "getaddrinfo", bomb("network")),
            (urllib.request, "urlopen", bomb("network")),
        ):
            stack.enter_context(patch.object(owner, name, replacement, create=True))
        dependencies = module._build_actual_provider_zero_prepared_inventory_dependencies()
        terminal = module.create_provider_zero_prepared_inventory(
            paths=paths,
            expected=module.DEFAULT_DIGESTS,
            dependencies=dependencies,
        )

    from tokenshare.experiments.paper_formal_plan import (
        FormalPreparedRequestInventory,
    )

    assert len(loaded_inventory) == 1
    inventory = loaded_inventory[0]
    assert type(inventory) is FormalPreparedRequestInventory
    assert inventory.record_count == 40_520
    assert inventory.unique_inference_request_count == 9_550
    assert inventory.provider_calls_made == 0
    assert calls["freeze_inventory"] == 1
    assert calls["persist_inventory"] == 1
    assert calls["reload_inventory"] == 1
    assert calls["factor_request_builder"] > 0
    assert calls["lean_request_builder"] > 0
    assert calls["outbound_prepare"] >= inventory.unique_inference_request_count
    assert len(outbound_by_digest) == inventory.unique_inference_request_count
    assert terminal["unique_prepare_call_count"] == inventory.unique_inference_request_count
    assert forbidden == {name: 0 for name in forbidden}
    return {
        "module": module,
        "paths": paths,
        "terminal": dict(terminal),
        "inventory": inventory,
        "calls": dict(calls),
        "outbound_distinct_digest_count": len(outbound_by_digest),
        "forbidden": dict(forbidden),
    }


_SHARED_CACHE = SingleAttemptStageACache()


def get_r13_full_stage_a(session_temp_root: Path) -> StageAResult:
    """同一 pytest 进程中只允许一次完整 Stage-A 构建尝试。"""

    base = Path(session_temp_root) / "r13-full-stage-a-shared"
    return _SHARED_CACHE.get(base, builder=_build_r13_full_stage_a)


def r13_full_stage_a_build_count() -> int:
    return _SHARED_CACHE.build_count
