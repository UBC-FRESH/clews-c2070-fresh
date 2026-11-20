"""FastAPI surface for the CLEWS/C2070 live scenario runner.

The server owns the durable scenario files and run job files.  It never solves
a model inside the request process: ``POST /api/runs`` persists a job and
spawns ``python -m workflows.ui.run_scenario`` as a subprocess.  Milestone 1b
replaces the runner body, not this API or the on-disk job contract.

Run locally::

    python -m workflows.ui.server --port 8013 --allow-runs

The default bind is loopback.  Set ``CLEWS_UI_READ_ONLY=1`` (or pass
``--read-only``) to reject every mutating HTTP method.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response, status
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

__all__ = ["AppSettings", "RunStore", "create_app", "main"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UI_DIR = REPO_ROOT / "ui"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "runtime" / "ui"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "not_implemented"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
RUNNER_TERMINAL_STATUSES = frozenset({"completed", "not_implemented"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
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


class AppSettings(BaseModel):
    """Resolved server settings exposed on ``app.state.settings``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_dir: Path
    allow_runs: bool
    read_only: bool
    heartbeat_seconds: float = Field(gt=0)
    poll_seconds: float = Field(gt=0)


class RunRequest(BaseModel):
    """Request body for enqueuing a stored scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=SCENARIO_ID_PATTERN.pattern)


class RunStore:
    """Persistent JSON job store with an append-only event log per run."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._lock = threading.RLock()

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def new_run_id(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
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
        """Persist a queued job before any subprocess is started."""

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
        with self._lock:
            job_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._job_path(run_id), job)
        return job

    def read(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            payload = read_json(self._job_path(run_id))
        if not isinstance(payload, dict):
            raise TypeError(f"job {run_id} is not a JSON object")
        return payload

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._runs_dir.is_dir():
                return []
            job_paths = sorted(
                self._runs_dir.glob("*/job.json"),
                key=lambda path: path.stat().st_mtime,
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

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self.read(run_id)
            job.update(changes)
            job["updated_utc"] = _utc_now()
            atomic_write_json(self._job_path(run_id), job)
            return job

    def append_event(
        self,
        run_id: str,
        *,
        event: str,
        level: str,
        message: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = self.read(run_id)
            sequence = int(job.get("event_count", 0)) + 1
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
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            job["event_count"] = sequence
            job["updated_utc"] = _utc_now()
            atomic_write_json(self._job_path(run_id), job)
            return record

    def read_events(self, run_id: str, offset: int) -> tuple[list[dict[str, Any]], int]:
        """Return complete JSONL events after ``offset`` and the new offset."""

        events_path = self._events_path(run_id)
        with self._lock:
            if not events_path.is_file():
                return [], offset
            with events_path.open("rb") as handle:
                handle.seek(offset)
                payload = handle.read()
        events: list[dict[str, Any]] = []
        consumed = 0
        for raw_line in payload.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                break
            consumed += len(raw_line)
            events.append(json.loads(raw_line.decode("utf-8")))
        return events, offset + consumed

    def mark_active_interrupted(self) -> int:
        """Startup repair: no process survives a server restart."""

        interrupted = 0
        for job in self.list_jobs():
            if job.get("status") not in ACTIVE_STATUSES:
                continue
            run_id = job.get("run_id")
            if not isinstance(run_id, str):
                continue
            message = "server restarted before the run finished"
            self.update(
                run_id,
                status="interrupted",
                finished_utc=_utc_now(),
                message=message,
            )
            self.append_event(
                run_id,
                event="interrupted",
                level="warning",
                message=message,
            )
            interrupted += 1
        return interrupted


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


def _final_status(exit_code: int, summary: dict[str, Any] | None) -> tuple[str, str]:
    if exit_code != 0:
        return "failed", f"runner exited with code {exit_code}"
    runner_status = summary.get("status") if summary else None
    if runner_status in RUNNER_TERMINAL_STATUSES:
        return runner_status, f"runner reported {runner_status}"
    if runner_status is None:
        return "completed", "runner exited successfully"
    return "failed", f"runner reported unknown status {runner_status!r}"


def create_app(
    runtime_dir: Path | str | None = None,
    *,
    allow_runs: bool = False,
    read_only: bool | None = None,
    runner_command: Sequence[str] | None = None,
    heartbeat_seconds: float = 15.0,
    poll_seconds: float = 0.05,
    ui_dir: Path | str | None = None,
) -> FastAPI:
    """Build the application around a writable runtime directory.

    ``runner_command`` exists for tests: a fake runner can be substituted while
    keeping the real subprocess, job-file, and SSE paths.  The default command
    is the milestone-1a stub module.
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
    )
    scenarios_dir = resolved_runtime / "scenarios"
    runs_dir = resolved_runtime / "runs"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    scenario_lock = threading.Lock()
    run_store = RunStore(runs_dir)
    run_store.mark_active_interrupted()

    app = FastAPI(
        title="CLEWS/C2070 live scenario runner",
        version="0.1.0",
        description=(
            "Validated clews.scenario.v1 scenarios, durable run jobs, and SSE "
            "status. Solves happen only in runner subprocesses."
        ),
    )
    app.state.settings = settings
    app.state.run_store = run_store
    app.state.scenarios_dir = scenarios_dir

    @app.middleware("http")
    async def read_only_guard(request, call_next):  # type: ignore[no-untyped-def]
        if settings.read_only and request.method in MUTATING_METHODS:
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
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc

    def spawn_runner(job: dict[str, Any]) -> None:
        run_id = str(job["run_id"])
        output_dir = Path(str(job["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [str(part) for part in job["command"]]
        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            message = f"could not start runner: {exc}"
            run_store.update(
                run_id,
                status="failed",
                finished_utc=_utc_now(),
                message=message,
            )
            run_store.append_event(
                run_id, event="failed", level="error", message=message
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message
            ) from exc

        run_store.update(
            run_id,
            status="running",
            pid=process.pid,
            started_utc=_utc_now(),
            message="runner started",
        )
        run_store.append_event(
            run_id,
            event="started",
            level="info",
            message=f"runner started with pid {process.pid}",
        )
        threading.Thread(
            target=_monitor_runner,
            args=(run_store, run_id, process, output_dir),
            daemon=True,
            name=f"runner-{run_id}",
        ).start()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "read_only": settings.read_only,
            "allow_runs": settings.allow_runs,
        }

    @app.get("/api/scenarios")
    def list_scenarios() -> dict[str, Any]:
        scenarios: list[dict[str, Any]] = []
        for path in sorted(scenarios_dir.glob("*.json")):
            try:
                scenario = load_scenario(path)
            except ValueError as exc:
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
        except ValueError as exc:
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
            load_scenario(scenario_path)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"stored scenario {scenario_id} is invalid: {exc}",
            ) from exc

        run_id = run_store.new_run_id()
        output_dir = run_store.run_dir(run_id) / "output"
        command = (
            *resolved_runner,
            "--scenario",
            str(scenario_path),
            "--out",
            str(output_dir),
        )
        job = run_store.create(
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_path=scenario_path,
            output_dir=output_dir,
            command=command,
        )
        spawn_runner(job)
        return run_store.read(run_id)

    @app.get("/api/runs/{raw_run_id}/events")
    def stream_run_events(raw_run_id: str) -> StreamingResponse:
        run_id = require_run_id(raw_run_id)
        try:
            run_store.read(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id} not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        return StreamingResponse(
            _event_stream(run_store, run_id, settings),
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


def _monitor_runner(
    store: RunStore,
    run_id: str,
    process: subprocess.Popen[str],
    output_dir: Path,
) -> None:
    """Mirror runner output into the event log, then persist the terminal state."""

    failure: str | None = None
    exit_code = -1
    try:
        if process.stdout is not None:
            for line in process.stdout:
                message = line.rstrip("\n")
                if message:
                    store.append_event(
                        run_id, event="log", level="info", message=message
                    )
        exit_code = process.wait()
    except Exception as exc:  # noqa: BLE001 - keep the job terminal even if plumbing breaks
        failure = f"runner monitor failed: {exc}"
        process.kill()
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            exit_code = -1

    summary = _read_runner_summary(output_dir / "run_summaries.json")
    terminal_status, message = _final_status(exit_code, summary)
    if failure is not None:
        terminal_status, message = "failed", failure
    runner_status = summary.get("status") if summary else None
    store.append_event(
        run_id,
        event=terminal_status,
        level="error" if terminal_status == "failed" else "info",
        message=message,
    )
    store.update(
        run_id,
        status=terminal_status,
        exit_code=exit_code,
        finished_utc=_utc_now(),
        runner_status=runner_status,
        message=message,
    )


async def _event_stream(
    store: RunStore, run_id: str, settings: AppSettings
) -> AsyncIterator[str]:
    offset = 0
    last_heartbeat = time.monotonic()
    while True:
        events, offset = store.read_events(run_id, offset)
        for event in events:
            yield (
                f"id: {event['seq']}\n"
                f"event: {event['event']}\n"
                f"data: {json.dumps(event, sort_keys=True)}\n\n"
            )
        try:
            job = store.read(run_id)
        except (FileNotFoundError, ValueError):
            return
        if job.get("status") in TERMINAL_STATUSES and not events:
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
    )
    uvicorn.run(
        application,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
