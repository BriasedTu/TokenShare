from __future__ import annotations

import ast
import inspect
from collections import Counter
from dataclasses import fields, is_dataclass
from importlib.util import resolve_name
from pathlib import Path

import pytest
import tokenshare
from tokenshare.experiments import (
    factorization_paper_adapter,
    lean_paper_adapter,
)
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ProtocolMechanismPolicy,
    ProtocolRunRequest,
    ProtocolRunResult,
    ProtocolTaskPluginRuntime,
    RuntimeHooks,
    WorkerBackend,
)


PACKAGE_ROOT = Path(tokenshare.__file__).resolve().parent


def _python_files(package_name: str) -> tuple[Path, ...]:
    return tuple(sorted((PACKAGE_ROOT / package_name).rglob("*.py")))


def _normalized_imports(path: Path) -> tuple[str, ...]:
    return _normalized_imports_from_source(
        path.read_text(encoding="utf-8"),
        module_name=_relative_module(path),
    )


def _normalized_imports_from_source(
    source: str,
    *,
    module_name: str,
) -> tuple[str, ...]:
    tree = ast.parse(source, filename=module_name)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_module = _resolved_import_from_module(node, module_name)
            if imported_module == "tokenshare":
                imports.extend(
                    f"tokenshare.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
            elif imported_module:
                imports.append(imported_module)
    return tuple(imports)


def _resolved_import_from_module(node: ast.ImportFrom, module_name: str) -> str:
    if not node.level:
        return node.module or ""
    package = module_name.rpartition(".")[0]
    relative_name = "." * node.level + (node.module or "")
    return resolve_name(relative_name, package)


def _relative_module(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    return "tokenshare." + ".".join(relative.parts)


def _expression_key(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _expression_key(node.value)
        if owner:
            return f"{owner}.{node.attr}"
    return None


def _protocol_state_write_violations_from_source(
    source: str,
    *,
    module_name: str,
) -> tuple[str, ...]:
    tree = ast.parse(source, filename=module_name)
    transition_names = {"transition_task_unit"}
    transition_import_lines: list[int] = []
    ledger_class_names: set[str] = set()
    ledger_module_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tokenshare.storage.events":
                    ledger_module_names.add(alias.asname or alias.name.split(".")[0])
                if alias.name == "tokenshare.storage":
                    ledger_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported_module = _resolved_import_from_module(node, module_name)
            if imported_module == "tokenshare":
                ledger_module_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "storage"
                )
            if imported_module == "tokenshare.core.state_machines":
                for alias in node.names:
                    if alias.name == "transition_task_unit":
                        transition_names.add(alias.asname or alias.name)
                        transition_import_lines.append(node.lineno)
            if imported_module == "tokenshare.storage.events":
                ledger_class_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "EventLedger"
                )
            if imported_module == "tokenshare.storage":
                ledger_class_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "EventLedger"
                )
                ledger_module_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "events"
                )

    def is_ledger_constructor(node: ast.expr) -> bool:
        if not isinstance(node, ast.Call):
            return False
        constructor = node.func
        if isinstance(constructor, ast.Name):
            return constructor.id in ledger_class_names
        if not isinstance(constructor, ast.Attribute) or constructor.attr != "EventLedger":
            return False
        owner = _expression_key(constructor.value)
        return owner in ledger_module_names or owner == "tokenshare.storage.events"

    def nodes_in_scope(
        scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[ast.AST, ...]:
        nodes: list[ast.AST] = []

        def visit(node: ast.AST) -> None:
            if isinstance(node, ast.Assign):
                visit(node.value)
                nodes.append(node)
                return
            if isinstance(node, ast.AnnAssign):
                if node.value is not None:
                    visit(node.value)
                nodes.append(node)
                return
            nodes.append(node)
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for statement in scope.body:
            visit(statement)
        return tuple(nodes)

    def annotation_is_event_ledger(annotation: ast.expr | None) -> bool:
        if isinstance(annotation, ast.Name):
            return annotation.id in ledger_class_names
        if isinstance(annotation, ast.Attribute) and annotation.attr == "EventLedger":
            owner = _expression_key(annotation.value)
            return owner in ledger_module_names or owner == "tokenshare.storage.events"
        return (
            isinstance(annotation, ast.Constant)
            and isinstance(annotation.value, str)
            and annotation.value in ledger_class_names
        )

    def ledger_parameter_names(
        scope: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> set[str]:
        arguments = [
            *scope.args.posonlyargs,
            *scope.args.args,
            *scope.args.kwonlyargs,
        ]
        if scope.args.vararg is not None:
            arguments.append(scope.args.vararg)
        if scope.args.kwarg is not None:
            arguments.append(scope.args.kwarg)
        conservative_names = {"ledger", "event_ledger", "_event_ledger"}
        return {
            argument.arg
            for argument in arguments
            if argument.arg in conservative_names
            or annotation_is_event_ledger(argument.annotation)
        }

    violations = [
        f"line {line_number}: transition_task_unit import"
        for line_number in transition_import_lines
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if (
            isinstance(called, ast.Name)
            and called.id in transition_names
            or isinstance(called, ast.Attribute)
            and called.attr == "transition_task_unit"
        ):
            violations.append(f"line {node.lineno}: transition_task_unit call")

    scopes: tuple[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef, ...] = (
        tree,
        *(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
    )
    for scope in scopes:
        scope_nodes = nodes_in_scope(scope)
        ledger_bindings = (
            ledger_parameter_names(scope)
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            else set()
        )
        for node in scope_nodes:
            targets: tuple[ast.expr, ...] = ()
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
                value = node.value

            target_keys = {
                key
                for target in targets
                if (key := _expression_key(target)) is not None
            }
            if target_keys:
                value_key = _expression_key(value) if value is not None else None
                value_is_ledger = value is not None and (
                    is_ledger_constructor(value) or value_key in ledger_bindings
                )
                ledger_bindings.difference_update(target_keys)
                if value_is_ledger:
                    ledger_bindings.update(target_keys)

            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"append", "append_batch"}:
                continue
            owner = node.func.value
            owner_key = _expression_key(owner)
            if (
                owner_key in ledger_bindings
                or owner_key in ledger_class_names
                or is_ledger_constructor(owner)
            ):
                violations.append(
                    f"line {node.lineno}: EventLedger.{node.func.attr} call"
                )
    return tuple(violations)


def _parameter_kinds(callable_object: object) -> dict[str, inspect._ParameterKind]:
    return {
        name: parameter.kind
        for name, parameter in inspect.signature(callable_object).parameters.items()
        if name != "self"
    }


def test_runtime_public_contracts_are_frozen() -> None:
    assert is_dataclass(ProtocolRunRequest)
    assert is_dataclass(ProtocolRunResult)
    assert ProtocolTaskPluginRuntime._is_protocol
    assert RuntimeHooks._is_protocol
    assert WorkerBackend._is_protocol

    required_fields = {
        ProtocolRunRequest: {
            "run_id",
            "root_input",
            "plugin_runtime",
            "worker_backend",
            "mechanism_policy",
            "hooks",
        },
        ProtocolRunResult: {
            "run_id",
            "task_id",
            "root_unit_id",
            "status",
            "event_refs",
            "artifact_refs",
            "summary",
        },
        ProtocolMechanismPolicy: {
            "parser_policy_enabled",
            "verification_enabled",
            "replacement_attempts_allowed",
            "merge_gate_enabled",
            "slot_integrity_enabled",
        },
    }
    for contract_type, required in required_fields.items():
        assert contract_type.__dataclass_params__.frozen is True
        assert required <= {field.name for field in fields(contract_type)}
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(contract_type).parameters.values()
        )

    with pytest.raises(TypeError):
        ProtocolRunRequest("run-1", object(), object(), object())
    with pytest.raises(TypeError):
        ProtocolRunResult("run-1")
    with pytest.raises(TypeError):
        ProtocolMechanismPolicy(True)

    assert _parameter_kinds(ProtocolTaskPluginRuntime.plan_root) == {
        "root_input": inspect.Parameter.POSITIONAL_OR_KEYWORD,
        "artifact_store": inspect.Parameter.KEYWORD_ONLY,
    }
    assert _parameter_kinds(ProtocolTaskPluginRuntime.build_execution_request) == {
        "unit": inspect.Parameter.POSITIONAL_OR_KEYWORD,
        "attempt": inspect.Parameter.KEYWORD_ONLY,
        "lease": inspect.Parameter.KEYWORD_ONLY,
    }
    assert _parameter_kinds(ProtocolTaskPluginRuntime.verify_submission) == {
        "submission": inspect.Parameter.POSITIONAL_OR_KEYWORD,
        "unit": inspect.Parameter.KEYWORD_ONLY,
    }
    assert _parameter_kinds(ProtocolTaskPluginRuntime.build_merge) == {
        "parent": inspect.Parameter.KEYWORD_ONLY,
        "canonical_children": inspect.Parameter.KEYWORD_ONLY,
        "slot_integrity_enabled": inspect.Parameter.KEYWORD_ONLY,
    }
    assert _parameter_kinds(
        ProtocolTaskPluginRuntime.planned_ai_unit_id
    ) == {
        "unit": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    assert _parameter_kinds(
        ProtocolTaskPluginRuntime.evaluate_merge_readiness
    ) == {
        "context": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }

    expected_hook_parameter = {
        "context": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    for method_name in (
        "after_raw_output_persisted",
        "before_parser",
        "before_verification",
        "before_requeue",
        "before_merge",
        "on_unit_progress",
    ):
        assert _parameter_kinds(getattr(RuntimeHooks, method_name)) == expected_hook_parameter

    assert isinstance(WorkerBackend.capacity, property)
    assert _parameter_kinds(WorkerBackend.execute) == {
        "request": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }


def test_full_policy_and_noop_hooks_preserve_default_runtime_semantics() -> None:
    policy = ProtocolMechanismPolicy()
    policy_values = {field.name: getattr(policy, field.name) for field in fields(policy)}
    assert {
        "parser_policy_enabled": True,
        "verification_enabled": True,
        "replacement_attempts_allowed": True,
        "merge_gate_enabled": True,
        "slot_integrity_enabled": True,
    }.items() <= policy_values.items()

    request = ProtocolRunRequest(
        run_id="run-1",
        root_input={"subject": "fixture"},
        plugin_runtime=object(),
        worker_backend=object(),
    )
    assert request.mechanism_policy == policy
    assert isinstance(request.hooks, NoOpRuntimeHooks)

    hooks = NoOpRuntimeHooks()
    context = {"candidate": "unchanged"}
    for method_name in (
        "after_raw_output_persisted",
        "before_parser",
        "before_verification",
        "before_requeue",
        "before_merge",
        "on_unit_progress",
    ):
        assert getattr(hooks, method_name)(context) is None
        assert context == {"candidate": "unchanged"}


def test_runtime_and_plugins_do_not_import_experiments() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): imported
        for package_name in ("local_runtime", "plugins")
        for path in _python_files(package_name)
        for imported in _normalized_imports(path)
        if imported == "tokenshare.experiments"
        or imported.startswith("tokenshare.experiments.")
    }
    assert violations == {}


def test_core_dependency_direction_freezes_grandfathered_storage_imports() -> None:
    # 这五条导入是迁移前的历史债务；Task 1 不移动它们，只用精确清单冻结并禁止增长。
    grandfathered_storage_imports = Counter(
        (
            ("tokenshare.core.contribution", "tokenshare.storage.events"),
            ("tokenshare.core.merge_coordinator", "tokenshare.storage.artifacts"),
            ("tokenshare.core.merge_coordinator", "tokenshare.storage.events"),
            ("tokenshare.core.registration", "tokenshare.storage.artifacts"),
            ("tokenshare.core.registration", "tokenshare.storage.events"),
        )
    )
    actual_storage_imports = Counter(
        (_relative_module(path), imported)
        for path in _python_files("core")
        for imported in _normalized_imports(path)
        if imported == "tokenshare.storage" or imported.startswith("tokenshare.storage.")
    )
    assert actual_storage_imports == grandfathered_storage_imports

    forbidden_imports = {
        (_relative_module(path), imported)
        for path in _python_files("core")
        for imported in _normalized_imports(path)
        if imported == "tokenshare.local_runtime"
        or imported.startswith("tokenshare.local_runtime.")
        or imported == "tokenshare.executors"
        or imported.startswith("tokenshare.executors.")
    }
    assert forbidden_imports == set()


def test_experiments_do_not_own_protocol_state_event_writes() -> None:
    violations: list[str] = []
    for path in _python_files("experiments"):
        violations.extend(
            f"{path.name}:{violation}"
            for violation in _protocol_state_write_violations_from_source(
                path.read_text(encoding="utf-8"),
                module_name=_relative_module(path),
            )
        )
    assert violations == []


def test_paper_adapter_public_compatibility_api_is_deprecated_and_dead_helpers_are_removed() -> None:
    assert not hasattr(factorization_paper_adapter, "_stable_task_artifact_refs")
    assert not hasattr(lean_paper_adapter, "_stable_lean_task_artifact_refs")

    for public_api in (
        factorization_paper_adapter.run_factorization_paper_case,
        lean_paper_adapter.run_lean_paper_case,
    ):
        assert "Deprecated compatibility API" in (inspect.getdoc(public_api) or "")


def test_import_analysis_normalizes_reexports_relative_imports_and_duplicates() -> None:
    source = """
from tokenshare import experiments as absolute_experiments
from tokenshare import storage as first_storage
from tokenshare import storage as duplicate_storage
from .. import experiments as relative_experiments
from .. import storage as relative_storage
"""

    imports = _normalized_imports_from_source(
        source,
        module_name="tokenshare.local_runtime.synthetic",
    )

    assert Counter(imports) == Counter(
        {
            "tokenshare.experiments": 2,
            "tokenshare.storage": 3,
        }
    )


def test_protocol_state_analysis_tracks_transition_and_event_ledger_aliases() -> None:
    source = """
import tokenshare.core.state_machines as state_machines
from ..core.state_machines import transition_task_unit as move_unit
from ..storage.events import EventLedger as LedgerClass

state_machines.transition_task_unit(object(), object())
move_unit(object(), object())

events = LedgerClass("events.jsonl")
events.append(object())

class Holder:
    def write(self):
        self.sink = LedgerClass("other.jsonl")
        self.sink.append(object())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("transition_task_unit call" in item for item in violations) == 2
    assert sum("EventLedger.append call" in item for item in violations) == 2


def test_protocol_state_analysis_rejects_transition_import_without_call() -> None:
    source = """
from ..core.state_machines import transition_task_unit as move_unit
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("transition_task_unit import" in item for item in violations) == 1


def test_protocol_state_analysis_tracks_supported_event_ledger_call_forms() -> None:
    source = """
from tokenshare.storage import EventLedger
from tokenshare.storage import EventLedger as LedgerAlias

records = LedgerAlias("aliased.jsonl")
records.append(object())

EventLedger("immediate.jsonl").append(object())

def write_typed(sink: EventLedger):
    sink.append(object())

def write_injected(ledger, event_ledger, _event_ledger):
    ledger.append(object())
    event_ledger.append(object())
    _event_ledger.append(object())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("EventLedger.append call" in item for item in violations) == 6


def test_protocol_state_analysis_rejects_event_ledger_append_batch() -> None:
    source = """
from tokenshare.storage import EventLedger

records = EventLedger("records.jsonl")
records.append_batch("batch:records", ())

EventLedger("immediate.jsonl").append_batch("batch:immediate", ())

def write_typed(sink: EventLedger):
    sink.append_batch("batch:typed", ())

def write_injected(ledger):
    ledger.append_batch("batch:injected", ())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("EventLedger.append_batch call" in item for item in violations) == 4


def test_protocol_state_analysis_tracks_same_scope_rebinding_and_aliases() -> None:
    source = """
from tokenshare.storage import EventLedger

def write_local():
    records = []
    records.append(object())
    records = EventLedger("records.jsonl")
    alias = records
    alias.append_batch("batch:alias", ())
    records = []
    records.append(object())

def write_injected(ledger):
    alias = ledger
    alias.append_batch("batch:injected-alias", ())

def write_during_rebinding(ledger):
    ledger = ledger.append_batch("batch:during-rebinding", ())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("EventLedger.append call" in item for item in violations) == 0
    assert sum("EventLedger.append_batch call" in item for item in violations) == 3


def test_protocol_state_analysis_keeps_ledger_bindings_in_lexical_scope() -> None:
    source = """
from tokenshare.storage import EventLedger

def write_ledger():
    events = EventLedger("events.jsonl")
    events.append(object())

def write_list():
    events = []
    events.append(object())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("EventLedger.append call" in item for item in violations) == 1


def test_protocol_state_analysis_tracks_storage_module_constructor_chains() -> None:
    source = """
import tokenshare.storage as storage
import tokenshare.storage
from tokenshare import storage as storage_alias

storage.EventLedger("aliased-module.jsonl").append(object())
tokenshare.storage.EventLedger("full-module.jsonl").append(object())
storage_alias.EventLedger("reexported-module.jsonl").append(object())
"""

    violations = _protocol_state_write_violations_from_source(
        source,
        module_name="tokenshare.experiments.synthetic",
    )

    assert sum("EventLedger.append call" in item for item in violations) == 3
