"""Pure TaskGraph view and ready-node logic for Phase 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from tokenshare.core.models import ArtifactRef, ProtocolConfig, TaskRelation, TaskState, TaskUnit


@dataclass(frozen=True)
class TaskGraph:
    """Rebuildable in-memory view over TaskUnit nodes and TaskRelation edges."""

    task_id: str
    units: dict[str, TaskUnit]
    relations: Iterable[TaskRelation] = field(default_factory=tuple)
    canonical_outputs_by_unit_id: dict[str, dict[str, ArtifactRef]] = field(default_factory=dict)
    protocol_config: ProtocolConfig | None = None
    out_edges_by_unit_id: dict[str, list[TaskRelation]] = field(init=False)
    in_edges_by_unit_id: dict[str, list[TaskRelation]] = field(init=False)
    max_depth_observed: int = field(init=False)

    def __post_init__(self) -> None:
        units = dict(self.units)
        relations = tuple(self.relations)
        canonical_outputs = {
            unit_id: dict(outputs)
            for unit_id, outputs in self.canonical_outputs_by_unit_id.items()
        }

        self._validate_units(units)
        out_edges = {unit_id: [] for unit_id in units}
        in_edges = {unit_id: [] for unit_id in units}
        seen_target_inputs: set[tuple[str, str | None]] = set()

        for relation in relations:
            self._validate_relation(relation, units, seen_target_inputs)
            out_edges[relation.source_unit_id].append(relation)
            in_edges[relation.target_unit_id].append(relation)

        self._validate_acyclic(units, out_edges)
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "relations", relations)
        object.__setattr__(self, "canonical_outputs_by_unit_id", canonical_outputs)
        object.__setattr__(self, "out_edges_by_unit_id", out_edges)
        object.__setattr__(self, "in_edges_by_unit_id", in_edges)
        object.__setattr__(self, "max_depth_observed", max((u.depth for u in units.values()), default=0))

    def ready_unit_ids(self) -> list[str]:
        """Return Ready units whose named dependencies are already canonical."""

        ready: list[str] = []
        for unit_id, unit in self.units.items():
            if unit.state == TaskState.READY and self._dependencies_are_satisfied(unit_id):
                ready.append(unit_id)
        return ready

    def _validate_units(self, units: dict[str, TaskUnit]) -> None:
        for unit_id, unit in units.items():
            if unit_id != unit.unit_id:
                raise ValueError(f"unit key does not match unit_id: {unit_id}")
            if unit.task_id != self.task_id:
                raise ValueError(f"unit task_id does not match graph task_id: {unit_id}")
        if self.protocol_config is None:
            return
        if len(units) > self.protocol_config.max_total_units:
            raise ValueError("TaskGraph exceeds max_total_units")
        max_depth = max((unit.depth for unit in units.values()), default=0)
        if max_depth > self.protocol_config.max_depth:
            raise ValueError("TaskGraph exceeds max_depth")

    def _validate_relation(
        self,
        relation: TaskRelation,
        units: dict[str, TaskUnit],
        seen_target_inputs: set[tuple[str, str | None]],
    ) -> None:
        if relation.task_id != self.task_id:
            raise ValueError(f"relation task_id does not match graph task_id: {relation.relation_id}")
        if relation.source_unit_id not in units:
            raise ValueError(f"unknown source_unit_id: {relation.source_unit_id}")
        if relation.target_unit_id not in units:
            raise ValueError(f"unknown target_unit_id: {relation.target_unit_id}")
        if relation.relation_type == "depends_on_output":
            if not relation.source_output_name or not relation.target_input_name:
                raise ValueError("depends_on_output relation requires named source and target")
            target_key = (relation.target_unit_id, relation.target_input_name)
            if target_key in seen_target_inputs:
                raise ValueError(f"duplicate target input binding: {relation.target_unit_id}")
            seen_target_inputs.add(target_key)

    def _validate_acyclic(
        self,
        units: dict[str, TaskUnit],
        out_edges: dict[str, list[TaskRelation]],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(unit_id: str) -> None:
            if unit_id in visiting:
                raise ValueError("TaskGraph contains a cycle")
            if unit_id in visited:
                return
            visiting.add(unit_id)
            for relation in out_edges.get(unit_id, []):
                visit(relation.target_unit_id)
            visiting.remove(unit_id)
            visited.add(unit_id)

        for unit_id in units:
            visit(unit_id)

    def _dependencies_are_satisfied(self, unit_id: str) -> bool:
        for relation in self.in_edges_by_unit_id.get(unit_id, []):
            if relation.relation_type != "depends_on_output":
                continue
            source_outputs = self.canonical_outputs_by_unit_id.get(
                relation.source_unit_id,
                self.units[relation.source_unit_id].canonical_output_refs,
            )
            if relation.source_output_name not in source_outputs:
                return False
        return True
