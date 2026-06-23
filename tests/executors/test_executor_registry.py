from tokenshare.executors.contracts import ExecutorStatus
from tokenshare.executors.registry import ExecutorRegistry

from tests.phase3_fixtures import make_executor_descriptor


def test_executor_registry_exposes_explicit_status_contract_and_filters_available_only() -> None:
    registry = ExecutorRegistry()
    registry.register(make_executor_descriptor(executor_id="executor_available"))
    registry.register(
        make_executor_descriptor(executor_id="executor_busy", status=ExecutorStatus.BUSY)
    )
    registry.register(
        make_executor_descriptor(executor_id="executor_offline", status=ExecutorStatus.OFFLINE)
    )
    registry.register(
        make_executor_descriptor(executor_id="executor_disabled", status=ExecutorStatus.DISABLED)
    )

    candidates = registry.match_available(
        executor_type="mock_ai",
        hard_requirements={"executor": "mock_ai"},
        request_schema_version="phase3.execution_request.v1",
    )
    no_match = registry.no_match_reasons(
        executor_type="mock_ai",
        hard_requirements={"executor": "mock_ai"},
        request_schema_version="phase3.execution_request.v1",
    )

    assert [candidate.executor_id for candidate in candidates] == ["executor_available"]
    assert no_match["executor_busy"] == "status:Busy"
    assert no_match["executor_offline"] == "status:Offline"
    assert no_match["executor_disabled"] == "status:Disabled"
