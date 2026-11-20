"""FastAPI surface for the CLEWS/C2070 live scenario runner.

The server owns the durable scenario files and run job files.  It never solves
a model inside the request process: ``POST /api/runs`` persists a job and
spawns ``python -m workflows.ui.run_scenario`` as a subprocess.  Milestone 1b
replaces the runner body, not this API or the on-disk job contract.

Run locally::

    python -m workflows.ui.server --port 8013 --allow-runs

The default bind is loopback.  Set ``CLEWS_UI_READ_ONLY=1`` (or pass
``--read-only``) to reject every mutating HTTP method.  Read-only startup does
not create directories or repair job records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workflows.ui.contracts import (
    SCENARIO_ID_PATTERN,
    SCHEMA_VERSION,
    Scenario,
    load_scenario,
    validate_scenario_id,
)
from workflows.ui.storage import atomic_write_json, read_json

__all__ = ["AppSettings", "RunStore", "RunSupervisor", "create_app", "main"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UI_DIR = REPO_ROOT / "ui"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "runtime" / "ui"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "not_implemented"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
RUNNER_TERMINAL_STATUSES = frozenset({"completed", "failed", "not_implemented"})
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_EVENT_READ_BYTES = 256 * 1024
TERMINAL_EVENT_LEVELS = {
    "completed": "info",
    "not_implemented": "info",
    "interrupted": "warning",
    "failed": "error",
}
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _env_flag(name: str) -> bool | None:
    """Parse an optional boolean environment flag, failing closed on garbage."""

    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeError(
        f"{name} must be one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}, got {raw!r}"
    )


def _validate_run_id(value: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError("run_id must match ^[a-z0-9][a-z0-9-]{0,95}$")
    return value


def _decode_event_line(raw_line: bytes) -> dict[str, Any] | None:
    """Return a well-formed event record, or ``None`` for corrupt JSONL input."""

    try:
        payload = json.loads(raw_line.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    sequence = payload.get("seq")
    event = payload.get("event")
    level = payload.get("level")
    message = payload.get("message")
    timestamp = payload.get("ts")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        return None
    if not isinstance(event, str) or not event:
        return None
    if not isinstance(level, str) or not isinstance(message, str):
        return None
    return {
        "seq": sequence,
        "ts": timestamp if isinstance(timestamp, str) else "",
        "event": event,
        "level": level,
        "message": message,
    }


def _runner_pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH")
    if existing:
        return f"{REPO_ROOT}{os.pathsep}{existing}"
    return str(REPO_ROOT)


class AppSettings(BaseModel):
    """Resolved server settings exposed on ``app.state.settings``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_dir: Path
    allow_runs: bool
    read_only: bool
    heartbeat_seconds: float = Field(gt=0)
    poll_seconds: float = Field(gt=0)
    max_concurrent_runs: int = Field(ge=1)


class RunRequest(BaseModel):
    """Request body for enqueuing a stored scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=SCENARIO_ID_PATTERN.pattern)


class RunStore:
    """Persistent JSON job store with an append-only event log per run."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._lock = threading.RLock()
        self._locks_guard = threading.Lock()
        self._run_locks: dict[str, threading.RLock] = {}
        self._event_sequences: dict[str, int] = {}

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def _lock_for(self, run_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._run_locks.setdefault(run_id, threading.RLock())

    def new_run_id(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
        with self._lock:
            while True:
                candidate = f"{stamp}-{uuid.uuid4().hex[:8]}"
                if not (self._runs_dir / candidate).exists():
                    return candidate

    def run_dir(self, run_id: str) -> Path:
        return self._runs_dir / _validate_run_id(run_id)

    def _job_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "job.json"

    def _events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def create(
        self,
        *,
        run_id: str,
        scenario_id: str,
        scenario_path: Path,
        output_dir: Path,
        command: Sequence[str],
    ) -> dict[str, Any]:
        """Persist a queued job before any subprocess is started.

        The run directory is created exclusively (``exist_ok=False``) so two
        writers can never share a run identifier.
        """

        job_dir = self.run_dir(run_id)
        now = _utc_now()
        job: dict[str, Any] = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "queued",
            "created_utc": now,
            "updated_utc": now,
            "started_utc": None,
            "finished_utc": None,
            "exit_code": None,
            "pid": None,
            "scenario_path": str(scenario_path),
            "output_dir": str(output_dir),
            "command": list(command),
            "runner_status": None,
            "message": "queued",
            "event_count": 0,
        }
        with self._lock, self._lock_for(run_id):
            job_dir.mkdir(parents=True, exist_ok=False)
            atomic_write_json(self._job_path(run_id), job)
        return job

    def read(self, run_id: str) -> dict[str, Any]:
        with self._lock_for(run_id):
            payload = read_json(self._job_path(run_id))
        if not isinstance(payload, dict):
            raise ValueError(  # noqa: TRY004 - corrupt data maps to HTTP 500
                f"job {run_id} is not a JSON object"
            )
        return payload

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._runs_dir.is_dir():
                return []
            job_paths = sorted(
                self._runs_dir.glob("*/job.json"),
                key=self._safe_mtime,
                reverse=True,
            )
        jobs: list[dict[str, Any]] = []
        for job_path in job_paths:
            try:
                payload = read_json(job_path)
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                jobs.append(payload)
        return jobs

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock_for(run_id):
            job = self.read(run_id)
            job.update(changes)
            job["updated_utc"] = _utc_now()
            atomic_write_json(self._job_path(run_id), job)
            return job

    def _read_last_event_sequence(self, events_path: Path) -> int:
        """Read only the tail of the event log to recover the last sequence."""

        try:
            with events_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - MAX_EVENT_READ_BYTES)
                handle.seek(start)
                payload = handle.read()
        except OSError:
            return 0

        last_sequence = 0
        lines = payload.splitlines()
        if start > 0 and lines:
            lines = lines[1:]  # the first line may be truncated
        for raw_line in lines:
            record = _decode_event_line(raw_line)
            if record is not None:
                last_sequence = max(last_sequence, int(record["seq"]))
        return last_sequence

    def _next_sequence_locked(self, run_id: str, job: dict[str, Any]) -> int:
        sequence = self._event_sequences.get(run_id)
        if sequence is None:
            persisted = job.get("event_count", 0)
            persisted_sequence = (
                int(persisted)
                if isinstance(persisted, int) and not isinstance(persisted, bool)
                else 0
            )
            sequence = max(
                persisted_sequence,
                self._read_last_event_sequence(self._events_path(run_id)),
            )
        sequence += 1
        self._event_sequences[run_id] = sequence
        return sequence

    def append_event(
        self,
        run_id: str,
        *,
        event: str,
        level: str,
        message: str,
        persist_count: bool = False,
        durable: bool = False,
    ) -> dict[str, Any]:
        """Append one event without fsyncing or rewriting the job per log line.

        ``durable`` is used only for the terminal event, so it survives before
        the terminal job status is written.
        """

        with self._lock_for(run_id):
            job = self.read(run_id)
            sequence = self._next_sequence_locked(run_id, job)
            record = {
                "seq": sequence,
                "ts": _utc_now(),
                "event": event,
                "level": level,
                "message": message,
            }
            events_path = self._events_path(run_id)
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                if durable:
                    os.fsync(handle.fileno())
            if persist_count:
                job["event_count"] = sequence
                job["updated_utc"] = _utc_now()
                atomic_write_json(self._job_path(run_id), job)
            return record

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        message: str,
        **changes: Any,
    ) -> dict[str, Any]:
        """Write a terminal event before the terminal job status, exactly once."""

        if status not in TERMINAL_STATUSES:
            raise ValueError(f"{status!r} is not a terminal run status")
        with self._lock_for(run_id):
            job = self.read(run_id)
            if job.get("status") in TERMINAL_STATUSES:
                return job
            record: dict[str, Any] | None = None
            try:
                record = self.append_event(
                    run_id,
                    event=status,
                    level=TERMINAL_EVENT_LEVELS[status],
                    message=message,
                    persist_count=False,
                    durable=True,
                )
            finally:
                if record is not None:
                    job["event_count"] = record["seq"]
                job.update(changes)
                job["status"] = status
                job["message"] = message
                job["finished_utc"] = _utc_now()
                job["updated_utc"] = job["finished_utc"]
                atomic_write_json(self._job_path(run_id), job)
            return job

    def read_events(
        self, run_id: str, offset: int, max_bytes: int = MAX_EVENT_READ_BYTES
    ) -> tuple[list[dict[str, Any]], int]:
        """Return complete JSONL events after ``offset`` and the new offset.

        Reads are bounded, partial trailing lines are left for the next poll,
        and blank or corrupt lines are skipped rather than aborting the stream.
        """

        events_path = self._events_path(run_id)
        with self._lock_for(run_id):
            if not events_path.is_file():
                return [], offset
            with events_path.open("rb") as handle:
                handle.seek(offset)
                payload = handle.read(max_bytes)

        if not payload:
            return [], offset
        complete_end = payload.rfind(b"\n")
        if complete_end < 0:
            if len(payload) < max_bytes:
                return [], offset
            skipped = self._discard_oversized_line(events_path, offset + len(payload))
            return [], offset + len(payload) + skipped
        chunk = payload[: complete_end + 1]

        events: list[dict[str, Any]] = []
        for raw_line in chunk.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                break
            record = _decode_event_line(raw_line)
            if record is not None:
                events.append(record)
        return events, offset + len(chunk)

    @staticmethod
    def _discard_oversized_line(events_path: Path, start: int) -> int:
        """Skip an over-long corrupt line in bounded chunks."""

        skipped = 0
        with events_path.open("rb") as handle:
            handle.seek(start)
            while True:
                block = handle.read(MAX_EVENT_READ_BYTES)
                if not block:
                    return skipped
                newline = block.find(b"\n")
                if newline >= 0:
                    return skipped + newline + 1
                skipped += len(block)

    def offset_after_sequence(self, run_id: str, sequence: int) -> int:
        """Return the byte offset immediately after ``sequence`` and earlier."""

        events_path = self._events_path(run_id)
        with self._lock_for(run_id):
            try:
                handle = events_path.open("rb")
            except OSError:
                return 0
            with handle:
                offset = 0
                for raw_line in handle:
                    if not raw_line.endswith(b"\n"):
                        break
                    record = _decode_event_line(raw_line)
                    if record is not None and int(record["seq"]) > sequence:
                        return offset
                    offset += len(raw_line)
        return offset

    def mark_active_interrupted(self) -> int:
        """Startup repair: no process survives a server restart."""

        interrupted = 0
        for job in self.list_jobs():
            if job.get("status") not in ACTIVE_STATUSES:
                continue
            run_id = job.get("run_id")
            if not isinstance(run_id, str):
                continue
            try:
                self.finish(
                    run_id,
                    status="interrupted",
                    message="server restarted before the run finished",
                )
            except (OSError, ValueError):
                continue
            interrupted += 1
        return interrupted


class RunnerStartError(RuntimeError):
    """Raised when a runner process cannot be started or registered."""


@dataclass
class ActiveRun:
    """One live runner process and the monitor responsible for finalizing it."""

    run_id: str
    process: subprocess.Popen[str]
    stop_event: threading.Event
    thread: threading.Thread | None = None


def _signal_process_group(process: subprocess.Popen[str], signal_number: int) -> None:
    """Signal the runner's whole process group, never just the group leader."""

    pid = process.pid
    if pid is None or process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(pid, signal_number)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.send_signal(signal_number)
    except ProcessLookupError:
        pass
    except OSError:
        pass


def _wait_for_process(process: subprocess.Popen[str], timeout: float) -> bool:
    """Wait up to ``timeout`` seconds, returning whether the process exited."""

    deadline = time.monotonic() + max(0.0, timeout)
    while process.poll() is None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _terminate_process_group(
    process: subprocess.Popen[str], grace_seconds: float = 2.0
) -> None:
    """Terminate, then kill, a runner process group and reap the leader."""

    if process.poll() is not None:
        return
    _signal_process_group(process, signal.SIGTERM)
    if not _wait_for_process(process, grace_seconds):
        _signal_process_group(process, signal.SIGKILL)
        _wait_for_process(process, 2.0)
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


class RunSupervisor:
    """Start runner processes with bounded concurrency and clean shutdown.

    The durable job store is the queue: excess submitted runs stay persisted as
    ``queued`` until a slot opens, so a crash never loses them.  Only
    ``max_concurrent_runs`` process groups can exist at once.
    """

    def __init__(
        self,
        store: RunStore,
        *,
        max_concurrent_runs: int,
        shutdown_grace_seconds: float = 5.0,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        self._store = store
        self._max_concurrent_runs = max_concurrent_runs
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self._pending: list[str] = []
        self._active: dict[str, ActiveRun] = {}
        self._closing = False
        self._shutdown_complete = False

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._active)

    def submit(self, job: dict[str, Any]) -> None:
        """Queue a persisted job; it starts as soon as a concurrency slot is free."""

        run_id = str(job["run_id"])
        with self._lock:
            if self._closing:
                raise RunnerStartError("server is shutting down")
            if run_id in self._active or run_id in self._pending:
                return
            self._pending.append(run_id)
        self._dispatch()

    def _dispatch(self) -> None:
        with self._dispatch_lock:
            while True:
                with self._lock:
                    if self._closing:
                        return
                    if len(self._active) >= self._max_concurrent_runs:
                        return
                    while self._pending:
                        run_id = self._pending.pop(0)
                        if run_id not in self._active:
                            break
                    else:
                        return
                try:
                    job = self._store.read(run_id)
                except (FileNotFoundError, ValueError):
                    continue
                if job.get("status") != "queued":
                    continue
                try:
                    self._start(job)
                except RunnerStartError:
                    continue

    def _start(self, job: dict[str, Any]) -> None:
        run_id = str(job["run_id"])
        output_dir = Path(str(job["output_dir"]))
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            message = f"could not create run output directory: {exc}"
            try:
                self._store.finish(run_id, status="failed", message=message)
            except (OSError, ValueError):
                pass
            raise RunnerStartError(message) from exc
        command = [str(part) for part in job["command"]]
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONPATH"] = _runner_pythonpath()

        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            message = f"could not start runner: {exc}"
            try:
                self._store.finish(
                    run_id,
                    status="failed",
                    message=message,
                    finished_utc=_utc_now(),
                )
            except (OSError, ValueError):
                pass
            raise RunnerStartError(message) from exc

        active = ActiveRun(
            run_id=run_id,
            process=process,
            stop_event=threading.Event(),
        )
        with self._lock:
            self._active[run_id] = active
        try:
            self._store.update(
                run_id,
                status="running",
                pid=process.pid,
                started_utc=_utc_now(),
                message="runner started",
            )
            self._store.append_event(
                run_id,
                event="started",
                level="info",
                message=f"runner started with pid {process.pid}",
            )
            monitor = threading.Thread(
                target=_monitor_runner,
                args=(
                    self._store,
                    run_id,
                    process,
                    output_dir,
                    active.stop_event,
                    self._run_finished,
                ),
                daemon=False,
                name=f"runner-{run_id}",
            )
            active.thread = monitor
            monitor.start()
        except BaseException as exc:
            with self._lock:
                self._active.pop(run_id, None)
            _terminate_process_group(process)
            message = f"could not register runner: {exc}"
            try:
                self._store.finish(
                    run_id,
                    status="failed",
                    message=message,
                    exit_code=process.returncode,
                    finished_utc=_utc_now(),
                )
            except (OSError, ValueError):
                pass
            raise RunnerStartError(message) from exc

    def _run_finished(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)
        self._dispatch()

    def _mark_interrupted(self, run_id: str, message: str) -> None:
        try:
            self._store.finish(run_id, status="interrupted", message=message)
        except (FileNotFoundError, OSError, ValueError):
            pass

    def shutdown(self) -> None:
        """Stop admission, terminate live groups, join monitors, repair statuses."""

        with self._dispatch_lock, self._lock:
            if self._shutdown_complete:
                return
            self._closing = True
            self._shutdown_complete = True
            pending = list(self._pending)
            self._pending.clear()
            active = list(self._active.values())

        for run_id in pending:
            self._mark_interrupted(
                run_id, "server shut down before the run started"
            )

        for item in active:
            item.stop_event.set()
        for item in active:
            _signal_process_group(item.process, signal.SIGTERM)

        deadline = time.monotonic() + self._shutdown_grace_seconds
        for item in active:
            remaining = max(0.0, deadline - time.monotonic())
            _wait_for_process(item.process, remaining)
        for item in active:
            if item.process.poll() is None:
                _signal_process_group(item.process, signal.SIGKILL)
        for item in active:
            _wait_for_process(item.process, 2.0)
        for item in active:
            if item.thread is None:
                continue
            if item.thread.is_alive() and item.process.stdout is not None:
                # A stray grandchild may still hold the pipe open; closing the
                # read end guarantees the monitor can reach its finally block.
                try:
                    item.process.stdout.close()
                except OSError:
                    pass
            item.thread.join(timeout=5.0)

        for item in active:
            self._mark_interrupted(
                item.run_id, "server shut down before the run finished"
            )
        for job in self._store.list_jobs():
            if job.get("status") not in ACTIVE_STATUSES:
                continue
            queued_run_id = job.get("run_id")
            if isinstance(queued_run_id, str):
                self._mark_interrupted(
                    queued_run_id, "server shut down before the run finished"
                )


def _resolve_runtime_dir(runtime_dir: Path | str | None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir).expanduser().resolve()
    from_env = os.environ.get("CLEWS_UI_RUNTIME_DIR")
    if from_env:
        return Path(from_env).expanduser().resolve()
    return DEFAULT_RUNTIME_DIR


def _read_runner_summary(summary_path: Path) -> dict[str, Any] | None:
    try:
        payload = read_json(summary_path)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _summary_message(summary: dict[str, Any], status: str) -> str:
    message = summary.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return f"runner reported {status}"


def _final_status(exit_code: int, summary: dict[str, Any] | None) -> tuple[str, str]:
    """Map an exit code and optional summary to a terminal job status."""

    raw_status = summary.get("status") if summary else None
    runner_status = raw_status if isinstance(raw_status, str) else None
    if exit_code != 0:
        if runner_status == "failed" and summary is not None:
            return "failed", _summary_message(summary, "failed")
        return "failed", f"runner exited with code {exit_code}"
    if summary is None:
        return "failed", (
            "runner exited successfully but produced no valid run_summaries.json"
        )
    if runner_status not in RUNNER_TERMINAL_STATUSES:
        return "failed", f"runner reported unknown status {runner_status!r}"
    return str(runner_status), _summary_message(summary, str(runner_status))


def create_app(
    runtime_dir: Path | str | None = None,
    *,
    allow_runs: bool = False,
    read_only: bool | None = None,
    runner_command: Sequence[str] | None = None,
    heartbeat_seconds: float = 15.0,
    poll_seconds: float = 0.05,
    max_concurrent_runs: int = 2,
    ui_dir: Path | str | None = None,
) -> FastAPI:
    """Build the application around a writable runtime directory.

    ``runner_command`` exists for tests: a fake runner can be substituted while
    keeping the real subprocess, job-file, and SSE paths.  The default command
    is the milestone-1b ``run_scenario`` module.
    """

    resolved_runtime = _resolve_runtime_dir(runtime_dir)
    resolved_read_only = _env_flag("CLEWS_UI_READ_ONLY")
    if read_only is not None:
        resolved_read_only = read_only
    resolved_runner = tuple(
        runner_command
        if runner_command is not None
        else (sys.executable, "-m", "workflows.ui.run_scenario")
    )
    if not resolved_runner:
        raise ValueError("runner_command must not be empty")

    settings = AppSettings(
        runtime_dir=resolved_runtime,
        allow_runs=allow_runs,
        read_only=bool(resolved_read_only),
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
        max_concurrent_runs=max_concurrent_runs,
    )
    scenarios_dir = resolved_runtime / "scenarios"
    runs_dir = resolved_runtime / "runs"

    scenario_lock = threading.Lock()
    run_store = RunStore(runs_dir)
    supervisor = RunSupervisor(
        run_store,
        max_concurrent_runs=settings.max_concurrent_runs,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if not settings.read_only:
            scenarios_dir.mkdir(parents=True, exist_ok=True)
            runs_dir.mkdir(parents=True, exist_ok=True)
            run_store.mark_active_interrupted()
        try:
            yield
        finally:
            if not settings.read_only:
                supervisor.shutdown()

    app = FastAPI(
        title="CLEWS/C2070 live scenario runner",
        version="0.1.0",
        description=(
            "Validated clews.scenario.v1 scenarios, durable run jobs, and SSE "
            "status. Solves happen only in runner subprocesses."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.run_store = run_store
    app.state.run_supervisor = supervisor
    app.state.scenarios_dir = scenarios_dir

    @app.middleware("http")
    async def read_only_guard(request, call_next):  # type: ignore[no-untyped-def]
        if settings.read_only and request.method not in READ_ONLY_METHODS:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "detail": (
                        "server is read-only; set CLEWS_UI_READ_ONLY=0 and "
                        "restart without --read-only to mutate state"
                    )
                },
            )
        return await call_next(request)

    def require_run_id(raw_run_id: str) -> str:
        try:
            return _validate_run_id(raw_run_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    def read_run_or_404(raw_run_id: str) -> dict[str, Any]:
        run_id = require_run_id(raw_run_id)
        try:
            return run_store.read(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id} not found",
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "read_only": settings.read_only,
            "allow_runs": settings.allow_runs,
            "max_concurrent_runs": settings.max_concurrent_runs,
        }

    @app.get("/api/scenarios")
    def list_scenarios() -> dict[str, Any]:
        if not scenarios_dir.is_dir():
            return {"scenarios": []}
        scenarios: list[dict[str, Any]] = []
        for path in sorted(scenarios_dir.glob("*.json")):
            try:
                scenario = load_scenario(path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"stored scenario {path.name} is invalid: {exc}",
                ) from exc
            scenarios.append(scenario.model_dump(mode="json"))
        return {"scenarios": scenarios}

    @app.get("/api/scenarios/{raw_scenario_id}")
    def get_scenario(raw_scenario_id: str) -> dict[str, Any]:
        try:
            scenario_id = validate_scenario_id(raw_scenario_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        path = scenarios_dir / f"{scenario_id}.json"
        if not path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scenario {scenario_id} not found",
            )
        try:
            return load_scenario(path).model_dump(mode="json")
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scenario {scenario_id} not found",
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc

    @app.post("/api/scenarios", status_code=status.HTTP_201_CREATED)
    def create_scenario(
        scenario: Scenario,
        response: Response,
        overwrite: bool = Query(default=False),
    ) -> dict[str, Any]:
        target = scenarios_dir / f"{scenario.scenario_id}.json"
        with scenario_lock:
            exists = target.is_file()
            if exists and not overwrite:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"scenario {scenario.scenario_id} already exists; "
                        "pass ?overwrite=true to replace it"
                    ),
                )
            atomic_write_json(target, scenario.model_dump(mode="json"))
        if exists:
            response.status_code = status.HTTP_200_OK
        return scenario.model_dump(mode="json")

    @app.get("/api/runs")
    def list_runs() -> dict[str, Any]:
        return {"runs": run_store.list_jobs()}

    @app.get("/api/runs/{raw_run_id}")
    def get_run(raw_run_id: str) -> dict[str, Any]:
        return read_run_or_404(raw_run_id)

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    def enqueue_run(request: RunRequest) -> dict[str, Any]:
        if not settings.allow_runs:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="run enqueueing is disabled; start the server with --allow-runs",
            )
        scenario_id = validate_scenario_id(request.scenario_id)
        scenario_path = scenarios_dir / f"{scenario_id}.json"
        if not scenario_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scenario {scenario_id} not found",
            )
        try:
            scenario = load_scenario(scenario_path)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scenario {scenario_id} not found",
            ) from exc
        except (OSError, ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"stored scenario {scenario_id} is invalid: {exc}",
            ) from exc

        run_id = run_store.new_run_id()
        run_directory = run_store.run_dir(run_id)
        output_dir = run_directory / "output"
        snapshot_path = run_directory / "scenario.json"
        command = (
            *resolved_runner,
            "--scenario",
            str(snapshot_path),
            "--out",
            str(output_dir),
        )
        job = run_store.create(
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_path=snapshot_path,
            output_dir=output_dir,
            command=command,
        )
        try:
            atomic_write_json(snapshot_path, scenario.model_dump(mode="json"))
        except OSError as exc:
            message = f"could not snapshot scenario: {exc}"
            run_store.finish(run_id, status="failed", message=message)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message
            ) from exc

        try:
            supervisor.submit(job)
        except RunnerStartError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        current = run_store.read(run_id)
        if current.get("status") == "failed" and current.get("started_utc") is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(current.get("message")),
            )
        return current

    @app.get("/api/runs/{raw_run_id}/events")
    def stream_run_events(
        raw_run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        run_id = require_run_id(raw_run_id)
        try:
            run_store.read(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id} not found",
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        resume_after = _parse_last_event_id(last_event_id)
        return StreamingResponse(
            _event_stream(run_store, run_id, settings, resume_after),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    static_dir = Path(ui_dir).expanduser().resolve() if ui_dir else DEFAULT_UI_DIR
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


def _parse_last_event_id(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _monitor_runner(
    store: RunStore,
    run_id: str,
    process: subprocess.Popen[str],
    output_dir: Path,
    stop_event: threading.Event,
    on_finished: Callable[[str], None],
) -> None:
    """Mirror runner output into the event log, then persist the terminal state."""

    failure: str | None = None
    exit_code = -1
    stdout = process.stdout
    try:
        if stdout is not None:
            for line in stdout:
                message = line.rstrip("\r\n")
                if message:
                    store.append_event(
                        run_id, event="log", level="info", message=message
                    )
        exit_code = process.wait()
    except Exception as exc:  # noqa: BLE001 - keep the job terminal even if plumbing breaks
        failure = f"runner monitor failed: {exc}"
        _terminate_process_group(process)
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            exit_code = -1
    finally:
        if stdout is not None:
            try:
                stdout.close()
            except OSError:
                pass
        runner_status: str | None = None
        try:
            summary = _read_runner_summary(output_dir / "run_summaries.json")
            raw_runner_status = summary.get("status") if summary else None
            if isinstance(raw_runner_status, str):
                runner_status = raw_runner_status
            if stop_event.is_set():
                terminal_status = "interrupted"
                message = "server shut down before the run finished"
            elif failure is not None:
                terminal_status, message = "failed", failure
            else:
                terminal_status, message = _final_status(exit_code, summary)
        except Exception as exc:  # noqa: BLE001 - never leave a run non-terminal
            terminal_status = "failed"
            message = f"runner monitor failed while finalizing: {exc}"
        try:
            store.finish(
                run_id,
                status=terminal_status,
                message=message,
                exit_code=exit_code,
                runner_status=runner_status,
            )
        except (OSError, ValueError):
            pass
        finally:
            on_finished(run_id)


async def _event_stream(
    store: RunStore,
    run_id: str,
    settings: AppSettings,
    resume_after: int | None,
) -> AsyncIterator[str]:
    offset = 0
    if resume_after is not None:
        try:
            offset = await asyncio.to_thread(
                store.offset_after_sequence, run_id, resume_after
            )
        except (OSError, ValueError):
            offset = 0
    last_heartbeat = time.monotonic()
    while True:
        try:
            events, offset = await asyncio.to_thread(
                store.read_events, run_id, offset
            )
        except (OSError, ValueError):
            return
        for event in events:
            yield (
                f"id: {event['seq']}\n"
                f"event: {event['event']}\n"
                f"data: {json.dumps(event, sort_keys=True, allow_nan=False)}\n\n"
            )
        try:
            job = await asyncio.to_thread(store.read, run_id)
        except (FileNotFoundError, OSError, ValueError):
            return
        if job.get("status") in TERMINAL_STATUSES:
            # Drain events appended just before the terminal status write.
            try:
                events, offset = await asyncio.to_thread(
                    store.read_events, run_id, offset
                )
            except (OSError, ValueError):
                return
            for event in events:
                yield (
                    f"id: {event['seq']}\n"
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event, sort_keys=True, allow_nan=False)}\n\n"
                )
            return
        now = time.monotonic()
        if now - last_heartbeat >= settings.heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_heartbeat = now
        await asyncio.sleep(settings.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workflows.ui.server",
        description="Serve the CLEWS/C2070 live scenario API and static UI.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=8013, help="bind port (default: 8013)"
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="writable runtime root (default: runtime/ui)",
    )
    parser.add_argument(
        "--allow-runs",
        action="store_true",
        help="permit POST /api/runs to spawn runner subprocesses",
    )
    parser.add_argument(
        "--read-only",
        action="store_const",
        const=True,
        default=None,
        help="reject POST/PUT/PATCH/DELETE (also CLEWS_UI_READ_ONLY=1)",
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=2,
        help="maximum live runner subprocesses (default: 2)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    application = create_app(
        runtime_dir=args.runtime_dir,
        allow_runs=args.allow_runs,
        read_only=args.read_only,
        max_concurrent_runs=args.max_concurrent_runs,
    )
    uvicorn.run(
        application,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
