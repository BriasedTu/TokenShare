"""`ProcessWorkerBackend` 的无 multiprocessing-pipe 子进程入口。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pickle
import sys
from time import monotonic, sleep


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 6:
        return 2
    payload_path, ready_path, start_path, result_path, release_path, error_path = (
        Path(value) for value in arguments
    )
    try:
        with payload_path.open("rb") as handle:
            executor, request, execution_index, submission_id, submitted_at = (
                pickle.load(handle)
            )
    except BaseException as error:
        _write_error(error_path, stage="payload_load", error=error)
        return 3

    _signal(ready_path)
    if not _wait_for(start_path, timeout_seconds=60.0):
        _write_error(
            error_path,
            stage="execution_release",
            error=TimeoutError("parent did not release worker execution"),
        )
        return 4

    try:
        prepare_process_execution = getattr(
            executor,
            "prepare_process_execution",
            None,
        )
        if callable(prepare_process_execution):
            prepare_process_execution(request, execution_index)
        submission = executor.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )
        export_process_result = getattr(executor, "export_process_result", None)
        process_result = (
            export_process_result(request, submission)
            if callable(export_process_result)
            else None
        )
        message = ("submission", submission, process_result)
    except BaseException as error:
        message = ("error", f"{type(error).__name__}: {error}")
    try:
        _write_pickle(result_path, message)
    except BaseException as error:
        _write_error(error_path, stage="result_write", error=error)
        return 5
    _wait_for(release_path, timeout_seconds=30.0)
    return 0


def _signal(path: Path) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text("ready\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_pickle(path: Path, value: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_error(path: Path, *, stage: str, error: BaseException) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {
                "stage": stage,
                "error_kind": type(error).__name__,
                "message": str(error),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _wait_for(path: Path, *, timeout_seconds: float) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if path.is_file():
            return True
        sleep(0.01)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
