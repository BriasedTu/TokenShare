import pytest

from tests.phase2_fixtures import make_artifact_ref, make_relation, make_unit
from tokenshare.core.models import TaskState
from tokenshare.core.task_graph import TaskGraph


def test_task_graph_calculates_ready_units_from_state_and_named_dependencies() -> None:
    root_output = make_artifact_ref("artifact_root_result")
    root = make_unit(
        "unit_root",
        state=TaskState.COMPLETED,
        canonical_output_refs={"result": root_output},
    )
    child = make_unit("unit_child", state=TaskState.READY, depth=1)
    relation = make_relation()

    graph = TaskGraph(
        task_id="task_demo",
        units={"unit_root": root, "unit_child": child},
        relations=[relation],
        canonical_outputs_by_unit_id={"unit_root": {"result": root_output}},
    )

    assert graph.ready_unit_ids() == ["unit_child"]
    assert graph.in_edges_by_unit_id["unit_child"] == [relation]


def test_task_graph_rejects_missing_relation_endpoints_and_cycles() -> None:
    root = make_unit("unit_root")
    child = make_unit("unit_child", depth=1)

    with pytest.raises(ValueError, match="unknown target_unit_id"):
        TaskGraph(
            task_id="task_demo",
            units={"unit_root": root},
            relations=[make_relation(target_unit_id="unit_missing")],
        )

    with pytest.raises(ValueError, match="cycle"):
        TaskGraph(
            task_id="task_demo",
            units={"unit_root": root, "unit_child": child},
            relations=[
                make_relation(
                    relation_id="rel_root_child",
                    source_unit_id="unit_root",
                    target_unit_id="unit_child",
                ),
                make_relation(
                    relation_id="rel_child_root",
                    source_unit_id="unit_child",
                    target_unit_id="unit_root",
                ),
            ],
        )
