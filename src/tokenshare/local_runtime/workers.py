"""本地 worker backend；只执行 unit 并记录事实，不拥有协议恢复权威。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, current_thread
from time import monotonic, sleep
from typing import Callable, Iterable

from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.local_runtime.contracts import (
    WorkerCompletionSchedule,
    WorkerTerminationPolicy,
)


@dataclass(frozen=True, kw_only=True)
class WorkerExecutionFact:
    """一次 backend 调用的事实摘要，不包含 retry/requeue 结论。"""

    execution_index: int
    request_id: str | None
    submission_id: str | None
    result_kind: str
    unit_id: str | None = None
    attempt_id: str | None = None
    lease_id: str | None = None
    worker_id: str | None = None
    worker_pid: int | None = None
    process_exitcode: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    kill_point: str | None = None
    kill_progress_target_ratio: float | None = None
    kill_progress_completed_ai_unit_count: int | None = None
    kill_progress_total_ai_unit_count: int | None = None
    kill_progress_actual_ratio: float | None = None
    kill_progress_observed_at: str | None = None
    kill_progress_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_index": self.execution_index,
            "request_id": self.request_id,
            "submission_id": self.submission_id,
            "result_kind": self.result_kind,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "worker_id": self.worker_id,
            "worker_pid": self.worker_pid,
            "process_exitcode": self.process_exitcode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "kill_point": self.kill_point,
            "kill_progress_target_ratio": self.kill_progress_target_ratio,
            "kill_progress_completed_ai_unit_count": (
                self.kill_progress_completed_ai_unit_count
            ),
            "kill_progress_total_ai_unit_count": (
                self.kill_progress_total_ai_unit_count
            ),
            "kill_progress_actual_ratio": self.kill_progress_actual_ratio,
            "kill_progress_observed_at": self.kill_progress_observed_at,
            "kill_progress_error": self.kill_progress_error,
        }


@dataclass(frozen=True, kw_only=True)
class WorkerBatchOutcome:
    """一个已调度 execution request 的 backend 结果。"""

    request: ExecutionRequest
    submission: ExecutionSubmission | None
    fact: WorkerExecutionFact
    failure_kind: str | None = None
    error_message: str | None = None
    logical_completion: WorkerCompletionSchedule | None = None


class WorkerExecutionError(RuntimeError):
    """单请求兼容 API 无法返回 batch failure fact 时使用。"""


class WorkerProcessBootstrapError(RuntimeError):
    """真实 worker 子进程在 executor 放行前无法启动。"""


class SequentialWorkerBackend:
    """在调用线程内依次执行请求的单 worker backend。"""

    def __init__(
        self,
        *,
        executor: object,
        submitted_at: Callable[[], str],
        completion_schedule: Callable[
            [ExecutionRequest, ExecutionSubmission | None, str | None],
            WorkerCompletionSchedule | None,
        ]
        | None = None,
    ) -> None:
        self._executor = executor
        self._submitted_at = submitted_at
        self._completion_schedule = _completion_schedule_resolver(
            executor, completion_schedule
        )
        self._execution_facts: list[WorkerExecutionFact] = []
        self._next_execution_index = 1

    @property
    def capacity(self) -> int:
        return 1

    @property
    def execution_facts(self) -> tuple[WorkerExecutionFact, ...]:
        return tuple(self._execution_facts)

    def execute(self, request: ExecutionRequest) -> ExecutionSubmission:
        outcome = self.execute_batch((request,))[0]
        if outcome.submission is None:
            raise WorkerExecutionError(outcome.error_message or outcome.failure_kind or "worker failed")
        return outcome.submission

    def execute_batch(
        self,
        requests: Iterable[ExecutionRequest],
    ) -> tuple[WorkerBatchOutcome, ...]:
        return tuple(self._execute_one(request) for request in requests)

    def _execute_one(self, request: ExecutionRequest) -> WorkerBatchOutcome:
        execution_index = self._next_execution_index
        self._next_execution_index += 1
        submission_id = _submission_id(request, execution_index=execution_index)
        started_at = _utc_now()
        try:
            worker_value = self._executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=self._submitted_at(),
            )
        except Exception as error:
            fact = _fact(
                request=request,
                execution_index=execution_index,
                submission_id=None,
                result_kind="executor_error",
                worker_id="sequential-worker-1",
                started_at=started_at,
                ended_at=_utc_now(),
            )
            self._execution_facts.append(fact)
            return _attach_completion_schedule(
                WorkerBatchOutcome(
                    request=request,
                    submission=None,
                    fact=fact,
                    failure_kind="executor_error",
                    error_message=f"{type(error).__name__}: {error}",
                ),
                self._completion_schedule,
            )
        if not isinstance(worker_value, ExecutionSubmission):
            raise TypeError("worker executor must return ExecutionSubmission")
        submission = worker_value
        fact = _fact(
            request=request,
            execution_index=execution_index,
            submission_id=getattr(submission, "submission_id", submission_id),
            result_kind=getattr(submission, "result_kind", "succeeded"),
            worker_id="sequential-worker-1",
            started_at=started_at,
            ended_at=_utc_now(),
        )
        self._execution_facts.append(fact)
        return _attach_completion_schedule(
            WorkerBatchOutcome(
                request=request,
                submission=submission,
                fact=fact,
            ),
            self._completion_schedule,
        )


class ThreadWorkerBackend:
    """以固定线程容量并发执行同一 runtime root 的已调度 units。"""

    def __init__(
        self,
        *,
        executor: object,
        capacity: int,
        submitted_at: Callable[[], str],
        completion_schedule: Callable[
            [ExecutionRequest, ExecutionSubmission | None, str | None],
            WorkerCompletionSchedule | None,
        ]
        | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("worker capacity must be positive")
        self._executor = executor
        self._capacity = capacity
        self._submitted_at = submitted_at
        self._completion_schedule = _completion_schedule_resolver(
            executor, completion_schedule
        )
        self._lock = Lock()
        self._next_execution_index = 1
        self._execution_facts: list[WorkerExecutionFact] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def execution_facts(self) -> tuple[WorkerExecutionFact, ...]:
        with self._lock:
            return tuple(sorted(self._execution_facts, key=lambda item: item.execution_index))

    def execute(self, request: ExecutionRequest) -> ExecutionSubmission:
        outcome = self.execute_batch((request,))[0]
        if outcome.submission is None:
            raise WorkerExecutionError(outcome.error_message or outcome.failure_kind or "worker failed")
        return outcome.submission

    def execute_batch(
        self,
        requests: Iterable[ExecutionRequest],
    ) -> tuple[WorkerBatchOutcome, ...]:
        request_batch = tuple(requests)
        if not request_batch:
            return ()
        if len(request_batch) > self.capacity:
            raise ValueError("request batch exceeds thread worker capacity")
        reservations = tuple(
            (request, self._reserve_execution_index()) for request in request_batch
        )
        outcomes: list[WorkerBatchOutcome | None] = [None] * len(reservations)
        with ThreadPoolExecutor(
            max_workers=self.capacity,
            thread_name_prefix="tokenshare-runtime-worker",
        ) as pool:
            futures = {
                pool.submit(self._execute_reserved, request, execution_index): index
                for index, (request, execution_index) in enumerate(reservations)
            }
            for future in as_completed(futures):
                outcomes[futures[future]] = future.result()
        return tuple(outcome for outcome in outcomes if outcome is not None)

    def _reserve_execution_index(self) -> int:
        with self._lock:
            execution_index = self._next_execution_index
            self._next_execution_index += 1
            return execution_index

    def _execute_reserved(
        self,
        request: ExecutionRequest,
        execution_index: int,
    ) -> WorkerBatchOutcome:
        submission_id = _submission_id(request, execution_index=execution_index)
        worker_id = current_thread().name
        started_at = _utc_now()
        try:
            worker_value = self._executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=self._submitted_at(),
            )
        except Exception as error:
            fact = _fact(
                request=request,
                execution_index=execution_index,
                submission_id=None,
                result_kind="executor_error",
                worker_id=worker_id,
                started_at=started_at,
                ended_at=_utc_now(),
            )
            self._append_fact(fact)
            return _attach_completion_schedule(
                WorkerBatchOutcome(
                    request=request,
                    submission=None,
                    fact=fact,
                    failure_kind="executor_error",
                    error_message=f"{type(error).__name__}: {error}",
                ),
                self._completion_schedule,
            )
        if not isinstance(worker_value, ExecutionSubmission):
            raise TypeError("worker executor must return ExecutionSubmission")
        submission = worker_value
        fact = _fact(
            request=request,
            execution_index=execution_index,
            submission_id=getattr(submission, "submission_id", submission_id),
            result_kind=getattr(submission, "result_kind", "succeeded"),
            worker_id=worker_id,
            started_at=started_at,
            ended_at=_utc_now(),
        )
        self._append_fact(fact)
        return _attach_completion_schedule(
            WorkerBatchOutcome(
                request=request,
                submission=submission,
                fact=fact,
            ),
            self._completion_schedule,
        )

    def _append_fact(self, fact: WorkerExecutionFact) -> None:
        with self._lock:
            self._execution_facts.append(fact)


class ProcessWorkerBackend:
    """每个 request 在独立进程执行，并可终止有限个匹配的 unit process。"""

    def __init__(
        self,
        *,
        executor: object,
        capacity: int,
        submitted_at: Callable[[], str],
        terminate_once: Callable[[ExecutionRequest], bool] | None = None,
        termination_limit: int = 1,
        termination_policy: WorkerTerminationPolicy | None = None,
        kill_point: str | None = None,
        process_timeout_seconds: float = 30.0,
        completion_schedule: Callable[
            [ExecutionRequest, ExecutionSubmission | None, str | None],
            WorkerCompletionSchedule | None,
        ]
        | None = None,
    ) -> None:
        if termination_policy is not None:
            if terminate_once is not None:
                raise ValueError(
                    "termination_policy cannot be combined with terminate_once"
                )
            terminate_once = termination_policy.matches
            termination_limit = termination_policy.termination_limit
            kill_point = termination_policy.kill_point
            process_timeout_seconds = (
                termination_policy.process_timeout_seconds
            )
        if capacity < 1:
            raise ValueError("worker capacity must be positive")
        if process_timeout_seconds <= 0:
            raise ValueError("process_timeout_seconds must be positive")
        if termination_limit < 1:
            raise ValueError("termination_limit must be positive")
        self._executor = executor
        self._capacity = capacity
        self._submitted_at = submitted_at
        self._terminate_once = terminate_once
        self._termination_limit = termination_limit
        self._kill_point = kill_point
        self._termination_policy = termination_policy
        self._process_timeout_seconds = process_timeout_seconds
        self._completion_schedule = completion_schedule
        self._termination_count = 0
        self._terminated_planned_ai_unit_ids: set[str] = set()
        self._completed_planned_ai_unit_ids: set[str] = set()
        self._next_execution_index = 1
        self._execution_facts: list[WorkerExecutionFact] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def execution_facts(self) -> tuple[WorkerExecutionFact, ...]:
        return tuple(sorted(self._execution_facts, key=lambda item: item.execution_index))

    def execute(self, request: ExecutionRequest) -> ExecutionSubmission:
        outcome = self.execute_batch((request,))[0]
        if outcome.submission is None:
            raise WorkerExecutionError(outcome.error_message or outcome.failure_kind or "worker failed")
        return outcome.submission

    def execute_batch(
        self,
        requests: Iterable[ExecutionRequest],
    ) -> tuple[WorkerBatchOutcome, ...]:
        request_batch = tuple(requests)
        if not request_batch:
            return ()
        if len(request_batch) > self.capacity:
            raise ValueError("request batch exceeds process worker capacity")
        with TemporaryDirectory(prefix="tokenshare-process-workers-") as temp_root:
            handles: list[dict[str, object]] = []
            try:
                for request in request_batch:
                    execution_index = self._next_execution_index
                    self._next_execution_index += 1
                    handles.append(
                        self._start_process_handle(
                            request=request,
                            execution_index=execution_index,
                            temp_root=Path(temp_root),
                        )
                    )
                for handle in handles:
                    _signal_process_path(Path(str(handle["start_path"])))
                return tuple(
                    self._finish_process_handle(handle) for handle in handles
                )
            finally:
                for handle in handles:
                    process = handle.get("process")
                    if isinstance(process, subprocess.Popen) and process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

    def _start_process_handle(
        self,
        *,
        request: ExecutionRequest,
        execution_index: int,
        temp_root: Path,
    ) -> dict[str, object]:
        submission_id = _submission_id(request, execution_index=execution_index)
        submitted_at = self._submitted_at()
        started_at = _utc_now()
        failures: list[str] = []
        for bootstrap_attempt in range(1, 4):
            attempt_root = temp_root / (
                f"execution-{execution_index}-bootstrap-{bootstrap_attempt}"
            )
            attempt_root.mkdir(parents=True, exist_ok=False)
            payload_path = attempt_root / "payload.pkl"
            ready_path = attempt_root / "ready"
            start_path = attempt_root / "start"
            result_path = attempt_root / "result.pkl"
            release_path = attempt_root / "release"
            error_path = attempt_root / "bootstrap-error.json"
            with payload_path.open("wb") as handle:
                pickle.dump(
                    (
                        self._executor,
                        request,
                        execution_index,
                        submission_id,
                        submitted_at,
                    ),
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            try:
                process = _open_process_worker(
                    (
                        sys.executable,
                        "-m",
                        "tokenshare.local_runtime.process_worker_child",
                        payload_path.as_posix(),
                        ready_path.as_posix(),
                        start_path.as_posix(),
                        result_path.as_posix(),
                        release_path.as_posix(),
                        error_path.as_posix(),
                    ),
                )
            except OSError as error:
                failures.append(
                    "stage=process_create, "
                    f"error_kind={type(error).__name__}, message={error}"
                )
                continue
            deadline = monotonic() + min(30.0, self._process_timeout_seconds)
            while monotonic() < deadline:
                if ready_path.is_file():
                    return {
                        "request": request,
                        "execution_index": execution_index,
                        "submission_id": submission_id,
                        "process": process,
                        "worker_id": f"process-worker-{execution_index}",
                        "result_path": result_path,
                        "start_path": start_path,
                        "release_path": release_path,
                        "error_path": error_path,
                        "matches_termination_target": (
                            self._termination_count < self._termination_limit
                            and self._terminate_once is not None
                            and bool(self._terminate_once(request))
                        ),
                        "started_at": started_at,
                    }
                if process.poll() is not None:
                    break
                sleep(0.01)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            failures.append(_process_bootstrap_failure(error_path, process.returncode))
        raise WorkerProcessBootstrapError(
            "worker subprocess bootstrap failed before executor release: "
            + "; ".join(failures)
        )

    def _finish_process_handle(self, handle: dict[str, object]) -> WorkerBatchOutcome:
        request = handle["request"]
        assert isinstance(request, ExecutionRequest)
        execution_index = int(handle["execution_index"])
        submission_id = str(handle["submission_id"])
        process = handle["process"]
        assert isinstance(process, subprocess.Popen)
        worker_id = str(handle["worker_id"])
        result_path = Path(str(handle["result_path"]))
        release_path = Path(str(handle["release_path"]))
        error_path = Path(str(handle["error_path"]))
        matches_termination_target = bool(handle["matches_termination_target"])
        started_at = str(handle["started_at"])
        try:
            deadline = monotonic() + self._process_timeout_seconds
            while (
                not result_path.is_file()
                and process.poll() is None
                and monotonic() < deadline
            ):
                sleep(0.01)
            if not result_path.is_file():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                fact = _fact(
                    request=request,
                    execution_index=execution_index,
                    submission_id=None,
                    result_kind="executor_error",
                    worker_id=worker_id,
                    worker_pid=process.pid,
                    process_exitcode=process.returncode,
                    started_at=started_at,
                    ended_at=_utc_now(),
                )
                outcome = WorkerBatchOutcome(
                    request=request,
                    submission=None,
                    fact=fact,
                    failure_kind="executor_error",
                    error_message=(
                        _process_bootstrap_failure(error_path, process.returncode)
                        if process.returncode is not None
                        else "worker process timed out"
                    ),
                )
            else:
                with result_path.open("rb") as result_handle:
                    message = pickle.load(result_handle)
                message_kind, payload = message[:2]
                process_result = message[2] if len(message) > 2 else None
                process_completion = message[3] if len(message) > 3 else None
                if process_completion is not None and not isinstance(
                    process_completion, WorkerCompletionSchedule
                ):
                    raise TypeError(
                        "process worker returned an invalid completion schedule"
                    )
                progress_observation = self._termination_progress_observation(
                    matches_termination_target=matches_termination_target,
                    request=request,
                )
                should_terminate = (
                    matches_termination_target
                    and self._termination_count < self._termination_limit
                    and progress_observation["armed"] is True
                    and self._termination_target_is_available(request)
                )
                if message_kind == "submission" and should_terminate:
                    if not isinstance(payload, ExecutionSubmission):
                        raise TypeError("worker executor must return ExecutionSubmission")
                    self._termination_count += 1
                    planned_ai_unit_id = request.soft_hints.get(
                        "planned_ai_unit_id"
                    )
                    if (
                        isinstance(planned_ai_unit_id, str)
                        and planned_ai_unit_id
                    ):
                        self._terminated_planned_ai_unit_ids.add(
                            planned_ai_unit_id
                        )
                    _ingest_process_result(self._executor, process_result)
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    observed_at = _utc_now()
                    fact = _fact(
                        request=request,
                        execution_index=execution_index,
                        submission_id=None,
                        result_kind="worker_terminated",
                        worker_id=worker_id,
                        worker_pid=process.pid,
                        process_exitcode=process.returncode,
                        started_at=started_at,
                        ended_at=observed_at,
                        kill_point=self._kill_point,
                        kill_progress_target_ratio=progress_observation[
                            "target_ratio"
                        ],
                        kill_progress_completed_ai_unit_count=progress_observation[
                            "completed_count"
                        ],
                        kill_progress_total_ai_unit_count=progress_observation[
                            "total_count"
                        ],
                        kill_progress_actual_ratio=progress_observation[
                            "actual_ratio"
                        ],
                        kill_progress_observed_at=observed_at,
                        kill_progress_error=progress_observation["error"],
                    )
                    outcome = WorkerBatchOutcome(
                        request=request,
                        submission=None,
                        fact=fact,
                        failure_kind="worker_terminated",
                        error_message="worker process terminated before submission",
                        logical_completion=process_completion,
                    )
                elif message_kind == "submission":
                    if not isinstance(payload, ExecutionSubmission):
                        raise TypeError("worker executor must return ExecutionSubmission")
                    submission = payload
                    _ingest_process_result(self._executor, process_result)
                    _signal_process_path(release_path)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    fact = _fact(
                        request=request,
                        execution_index=execution_index,
                        submission_id=getattr(payload, "submission_id", submission_id),
                        result_kind=getattr(payload, "result_kind", "succeeded"),
                        worker_id=worker_id,
                        worker_pid=process.pid,
                        process_exitcode=process.returncode,
                        started_at=started_at,
                        ended_at=_utc_now(),
                        kill_point=(
                            self._kill_point
                            if matches_termination_target
                            else None
                        ),
                        kill_progress_target_ratio=(
                            progress_observation["target_ratio"]
                            if matches_termination_target
                            else None
                        ),
                        kill_progress_completed_ai_unit_count=(
                            progress_observation["completed_count"]
                            if matches_termination_target
                            else None
                        ),
                        kill_progress_total_ai_unit_count=(
                            progress_observation["total_count"]
                            if matches_termination_target
                            else None
                        ),
                        kill_progress_actual_ratio=(
                            progress_observation["actual_ratio"]
                            if matches_termination_target
                            else None
                        ),
                        kill_progress_observed_at=(
                            _utc_now() if matches_termination_target else None
                        ),
                        kill_progress_error=(
                            progress_observation["error"]
                            if matches_termination_target
                            else None
                        ),
                    )
                    self._record_completed_planned_ai_unit(request, payload)
                    outcome = WorkerBatchOutcome(
                        request=request,
                        submission=submission,
                        fact=fact,
                        logical_completion=process_completion,
                    )
                else:
                    _signal_process_path(release_path)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    fact = _fact(
                        request=request,
                        execution_index=execution_index,
                        submission_id=None,
                        result_kind="executor_error",
                        worker_id=worker_id,
                        worker_pid=process.pid,
                        process_exitcode=process.returncode,
                        started_at=started_at,
                        ended_at=_utc_now(),
                    )
                    outcome = WorkerBatchOutcome(
                        request=request,
                        submission=None,
                        fact=fact,
                        failure_kind="executor_error",
                        error_message=str(payload),
                    )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        outcome = _attach_completion_schedule(outcome, self._completion_schedule)
        self._execution_facts.append(outcome.fact)
        return outcome

    def _termination_progress_observation(
        self,
        *,
        matches_termination_target: bool,
        request: ExecutionRequest,
    ) -> dict[str, object]:
        policy = self._termination_policy
        if not matches_termination_target or policy is None:
            return {
                "armed": matches_termination_target,
                "target_ratio": None,
                "completed_count": None,
                "total_count": None,
                "actual_ratio": None,
                "error": None,
            }
        total_count = policy.total_planned_ai_unit_count
        if total_count is None:
            return {
                "armed": False,
                "target_ratio": policy.target_progress_ratio,
                "completed_count": len(self._completed_planned_ai_unit_ids),
                "total_count": None,
                "actual_ratio": None,
                "error": "missing_total_planned_ai_unit_count",
            }
        planned_ai_unit_id = request.soft_hints.get("planned_ai_unit_id")
        current_completion = (
            1
            if isinstance(planned_ai_unit_id, str)
            and planned_ai_unit_id
            and planned_ai_unit_id not in self._completed_planned_ai_unit_ids
            else 0
        )
        completed_count = (
            len(self._completed_planned_ai_unit_ids) + current_completion
        )
        actual_ratio = completed_count / total_count
        armed = actual_ratio >= policy.target_progress_ratio
        return {
            "armed": armed,
            "target_ratio": policy.target_progress_ratio,
            "completed_count": completed_count,
            "total_count": total_count,
            "actual_ratio": actual_ratio,
            "error": None if armed else "actual_progress_below_target",
        }

    def _termination_target_is_available(
        self,
        request: ExecutionRequest,
    ) -> bool:
        policy = self._termination_policy
        if policy is None:
            return True
        planned_ai_unit_id = request.soft_hints.get("planned_ai_unit_id")
        if not isinstance(planned_ai_unit_id, str) or not planned_ai_unit_id:
            return False
        pending_targets = (
            set(policy.target_planned_ai_unit_ids)
            - self._terminated_planned_ai_unit_ids
        )
        # 先让每个冻结逻辑 target 各终止一次；达到唯一 target 覆盖后，
        # 允许在 replacement process 上继续终止，直到满足 death-count。
        return (
            planned_ai_unit_id not in self._terminated_planned_ai_unit_ids
            or not pending_targets
        )

    def _record_completed_planned_ai_unit(
        self,
        request: ExecutionRequest,
        submission: object,
    ) -> None:
        if getattr(submission, "result_kind", None) != "succeeded":
            return
        planned_ai_unit_id = request.soft_hints.get("planned_ai_unit_id")
        if isinstance(planned_ai_unit_id, str) and planned_ai_unit_id:
            self._completed_planned_ai_unit_ids.add(planned_ai_unit_id)


def execute_worker_batch(
    backend: object,
    requests: Iterable[ExecutionRequest],
) -> tuple[WorkerBatchOutcome, ...]:
    """调用 backend 的 batch 边界；多容量 backend 必须显式实现该边界。"""

    request_batch = tuple(requests)
    execute_batch = getattr(backend, "execute_batch", None)
    if callable(execute_batch):
        outcomes = tuple(execute_batch(request_batch))
        if len(outcomes) != len(request_batch):
            raise ValueError("worker backend returned an incomplete request batch")
        return outcomes
    if getattr(backend, "capacity", 0) != 1:
        raise ValueError(
            "multi-worker backend must implement execute_batch; only single-worker-compatible fallback is supported"
        )
    outcomes: list[WorkerBatchOutcome] = []
    for request in request_batch:
        try:
            worker_value = backend.execute(request)
        except Exception as error:
            fact = _fact(
                request=request,
                execution_index=len(outcomes) + 1,
                submission_id=None,
                result_kind="executor_error",
                worker_id="single-worker-compatible",
                started_at=None,
                ended_at=None,
            )
            outcomes.append(
                WorkerBatchOutcome(
                    request=request,
                    submission=None,
                    fact=fact,
                    failure_kind="executor_error",
                    error_message=f"{type(error).__name__}: {error}",
                )
            )
        else:
            if not isinstance(worker_value, ExecutionSubmission):
                raise TypeError("worker executor must return ExecutionSubmission")
            submission = worker_value
            fact = _fact(
                request=request,
                execution_index=len(outcomes) + 1,
                submission_id=getattr(submission, "submission_id", None),
                result_kind=getattr(submission, "result_kind", "succeeded"),
                worker_id="single-worker-compatible",
                started_at=None,
                ended_at=None,
            )
            outcomes.append(
                WorkerBatchOutcome(
                    request=request,
                    submission=submission,
                    fact=fact,
                )
            )
    return tuple(outcomes)


def _attach_completion_schedule(
    outcome: WorkerBatchOutcome,
    resolver: Callable[
        [ExecutionRequest, ExecutionSubmission | None, str | None],
        WorkerCompletionSchedule | None,
    ]
    | None,
) -> WorkerBatchOutcome:
    if resolver is None:
        return outcome
    completion = resolver(
        outcome.request,
        outcome.submission,
        outcome.failure_kind,
    )
    if completion is not None and not isinstance(
        completion, WorkerCompletionSchedule
    ):
        raise TypeError(
            "completion_schedule must return WorkerCompletionSchedule or None"
        )
    if (
        outcome.logical_completion is not None
        and completion is not None
        and outcome.logical_completion != completion
    ):
        raise ValueError("child and parent completion schedules disagree")
    return replace(
        outcome,
        logical_completion=completion or outcome.logical_completion,
    )


def _completion_schedule_resolver(
    executor: object,
    explicit: Callable[
        [ExecutionRequest, ExecutionSubmission | None, str | None],
        WorkerCompletionSchedule | None,
    ]
    | None,
):
    if explicit is not None:
        return explicit
    candidate = getattr(executor, "worker_completion_schedule", None)
    return candidate if callable(candidate) else None


def _signal_process_path(path: Path) -> None:
    """以原子文件信号放行子进程，避免依赖 Windows 句柄继承。"""

    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text("signal\n", encoding="utf-8")
    os.replace(temporary, path)


def _open_process_worker(arguments: tuple[str, ...]) -> subprocess.Popen:
    return subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=_process_worker_environment(),
    )


def _process_worker_environment() -> dict[str, str]:
    """把父解释器的真实导入路径传给独立 worker 解释器。"""

    environment = dict(os.environ)
    import_paths = tuple(
        str(Path(path).resolve()) if path else str(Path.cwd())
        for path in sys.path
    )
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(import_paths))
    return environment


def _process_bootstrap_failure(error_path: Path, returncode: int | None) -> str:
    """读取子进程在执行器放行前持久化的结构化启动错误。"""

    if error_path.is_file():
        try:
            payload = json.loads(error_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return (
                "worker bootstrap error evidence is unreadable "
                f"({type(error).__name__}: {error}; exitcode={returncode})"
            )
        stage = payload.get("stage", "unknown")
        error_kind = payload.get("error_kind", "unknown")
        message = payload.get("message", "")
        return (
            f"stage={stage}, error_kind={error_kind}, message={message}, "
            f"exitcode={returncode}"
        )
    return f"worker exited before ready signal (exitcode={returncode})"


def _ingest_process_result(executor: object, process_result: object) -> None:
    if process_result is None:
        return
    ingest_process_result = getattr(executor, "ingest_process_result", None)
    if callable(ingest_process_result):
        ingest_process_result(process_result)


def _fact(
    *,
    request: ExecutionRequest,
    execution_index: int,
    submission_id: str | None,
    result_kind: str,
    worker_id: str | None,
    started_at: str | None,
    ended_at: str | None,
    worker_pid: int | None = None,
    process_exitcode: int | None = None,
    kill_point: str | None = None,
    kill_progress_target_ratio: float | None = None,
    kill_progress_completed_ai_unit_count: int | None = None,
    kill_progress_total_ai_unit_count: int | None = None,
    kill_progress_actual_ratio: float | None = None,
    kill_progress_observed_at: str | None = None,
    kill_progress_error: str | None = None,
) -> WorkerExecutionFact:
    return WorkerExecutionFact(
        execution_index=execution_index,
        request_id=getattr(request, "request_id", None),
        submission_id=submission_id,
        result_kind=result_kind,
        unit_id=getattr(request, "unit_id", None),
        attempt_id=getattr(request, "attempt_id", None),
        lease_id=getattr(request, "lease_id", None),
        worker_id=worker_id,
        worker_pid=worker_pid,
        process_exitcode=process_exitcode,
        started_at=started_at,
        ended_at=ended_at,
        kill_point=kill_point,
        kill_progress_target_ratio=kill_progress_target_ratio,
        kill_progress_completed_ai_unit_count=(
            kill_progress_completed_ai_unit_count
        ),
        kill_progress_total_ai_unit_count=kill_progress_total_ai_unit_count,
        kill_progress_actual_ratio=kill_progress_actual_ratio,
        kill_progress_observed_at=kill_progress_observed_at,
        kill_progress_error=kill_progress_error,
    )


def _submission_id(request: object, *, execution_index: int) -> str:
    attempt_id = getattr(request, "attempt_id", None)
    if not isinstance(attempt_id, str) or not attempt_id:
        return f"submission_{execution_index:06d}"
    readable = "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in attempt_id
    )[:48]
    digest = sha256(attempt_id.encode("utf-8")).hexdigest()[:16]
    return f"submission_{readable}_{digest}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
