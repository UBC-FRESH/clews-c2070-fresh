"""Tests for the clews.scenario.v1 contract and live-runner API skeleton."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from workflows.ui.contracts import Scenario, validate_scenario_id
from workflows.ui.server import RunStore, create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def scenario_payload() -> dict:
    return {
        "scenario_id": "screening_150m_base",
        "label": "150 Mm3 screening base",
        "description": "Milestone 1a contract test scenario.",
        "schema_version": "clews.scenario.v1",
        "harvest": {
            "level_m3_per_year": 150_000_000.0,
            "path": "high",
            "recovery_fraction": 0.6,
            "cost_basis": "adopted_rest_of_canada_curve",
            "supply_curve": "tiered",
        },
        "energy": {
            "carbon_price_schedule": [
                {"year": 2021, "price_usd_per_tco2e": 95.0},
                {"year": 2026, "price_usd_per_tco2e": 200.0},
            ],
            "hydro_mode": "cap_2021",
            "pwr_bio_cap_fraction": 0.05,
            "nuclear_cap_gw": 0.0,
            "wind_cost_usd_per_gj": 3.33,
            "wind_cap_fraction": 0.2,
            "t3_cap_pj": 200.0,
            "t4_cap_pj": 0.0,
            "generic_bio_cost_usd_per_gj": 3.0,
        },
        "constraints": {"set_id": "screening_v0"},
        "provenance": {
            "base_data_commit": "4be339d",
            "model_commit": "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44",
            "created_utc": "2026-11-20T00:00:00+00:00",
        },
    }


FAKE_RUNNER = """\
import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

print("fake runner started", flush=True)
time.sleep(0.05)
scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "run_summaries.json").write_text(
    json.dumps(
        {
            "status": "completed",
            "scenario_id": scenario["scenario_id"],
            "message": "fake runner completed",
        }
    ),
    encoding="utf-8",
)
print("fake runner finished", flush=True)
"""

SLOW_RUNNER = """\
import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

print("slow fake runner started", flush=True)
time.sleep(0.6)
scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "run_summaries.json").write_text(
    json.dumps({"status": "completed", "scenario_id": scenario["scenario_id"]}),
    encoding="utf-8",
)
print("slow fake runner finished", flush=True)
"""


def write_fake_runner(tmp_path: Path, source: str) -> tuple[str, ...]:
    script = tmp_path / "fake_runner.py"
    script.write_text(source, encoding="utf-8")
    return (sys.executable, str(script))


def make_client(
    runtime: Path,
    *,
    runner: tuple[str, ...] | None = None,
    allow_runs: bool = True,
    read_only: bool = False,
    heartbeat_seconds: float = 0.02,
) -> TestClient:
    app = create_app(
        runtime,
        allow_runs=allow_runs,
        read_only=read_only,
        runner_command=runner,
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=0.01,
    )
    return TestClient(app)


def create_scenario(client: TestClient, payload: dict | None = None) -> dict:
    body = payload if payload is not None else scenario_payload()
    response = client.post("/api/scenarios", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_terminal(
    client: TestClient, run_id: str, timeout_seconds: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"completed", "failed", "interrupted", "not_implemented"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_contract_accepts_valid_payload_and_sorts_schedule() -> None:
    payload = scenario_payload()
    payload["energy"]["carbon_price_schedule"].reverse()

    scenario = Scenario.model_validate(payload)

    assert scenario.scenario_id == "screening_150m_base"
    assert [point.year for point in scenario.energy.carbon_price_schedule] == [
        2021,
        2026,
    ]
    assert scenario.provenance.created_utc.isoformat() == "2026-11-20T00:00:00+00:00"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"extra_top_level": True}),
        lambda p: p["harvest"].update({"level_m3_per_year": 0}),
        lambda p: p["harvest"].update({"level_m3_per_year": -1}),
        lambda p: p["harvest"].update({"recovery_fraction": 0}),
        lambda p: p["harvest"].update({"recovery_fraction": 1.01}),
        lambda p: p["harvest"].update({"path": "invented"}),
        lambda p: p["harvest"].update({"supply_curve": "flat"}),
        lambda p: p["harvest"].update({"cost_basis": "invented"}),
        lambda p: p["harvest"].update({"unknown": 1}),
        lambda p: p["energy"].update({"hydro_mode": "normal"}),
        lambda p: p["energy"].update({"nuclear_cap_gw": -0.1}),
        lambda p: p["energy"].update({"wind_cost_usd_per_gj": -1}),
        lambda p: p["energy"].update({"wind_cap_fraction": 1.1}),
        lambda p: p["energy"].update({"pwr_bio_cap_fraction": -0.1}),
        lambda p: p["energy"].update({"t3_cap_pj": -1}),
        lambda p: p["energy"].update({"generic_bio_cost_usd_per_gj": -1}),
        lambda p: p["energy"].update({"carbon_price_schedule": []}),
        lambda p: p["energy"]["carbon_price_schedule"][0].update({"year": 2020}),
        lambda p: p["energy"]["carbon_price_schedule"][1].update({"year": 2071}),
        lambda p: p["energy"]["carbon_price_schedule"][1].update(
            {"price_usd_per_tco2e": -1}
        ),
        lambda p: p["energy"]["carbon_price_schedule"].append(
            {"year": 2021, "price_usd_per_tco2e": 50.0}
        ),
        lambda p: p["energy"].update({"carbon_price_schedule": {"2021": 95}}),
        lambda p: p.update({"scenario_id": "../etc/passwd"}),
        lambda p: p.update({"schema_version": "clews.scenario.v2"}),
        lambda p: p["constraints"].update({"set_id": ""}),
        lambda p: p["provenance"].update({"created_utc": "2026-11-20T00:00:00"}),
        lambda p: p["provenance"].update({"model_commit": "   "}),
        lambda p: p.update({"label": "   "}),
    ],
)
def test_contract_rejects_invalid_payload(mutate) -> None:
    payload = copy.deepcopy(scenario_payload())
    mutate(payload)

    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)


def test_scenario_id_helper_rejects_traversal() -> None:
    assert validate_scenario_id("screening_v0") == "screening_v0"
    for candidate in ("../x", "a/b", "A", "", ".hidden", "x" * 65):
        with pytest.raises(ValueError):
            validate_scenario_id(candidate)


# ---------------------------------------------------------------------------
# Scenario API
# ---------------------------------------------------------------------------


def test_scenario_create_read_and_atomic_write(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime) as client:
        created = create_scenario(client)
        assert created["scenario_id"] == "screening_150m_base"

        stored = runtime / "scenarios" / "screening_150m_base.json"
        assert json.loads(stored.read_text(encoding="utf-8")) == created
        assert list(stored.parent.glob("*.tmp")) == []
        assert list(stored.parent.glob(".*.tmp")) == []

        listing = client.get("/api/scenarios")
        assert listing.status_code == 200
        assert [item["scenario_id"] for item in listing.json()["scenarios"]] == [
            "screening_150m_base"
        ]

        fetched = client.get("/api/scenarios/screening_150m_base")
        assert fetched.status_code == 200
        assert fetched.json() == created

        missing = client.get("/api/scenarios/does_not_exist")
        assert missing.status_code == 404


def test_scenario_duplicate_is_409_and_overwrite_is_200(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime) as client:
        create_scenario(client)

        duplicate = client.post("/api/scenarios", json=scenario_payload())
        assert duplicate.status_code == 409

        replacement = scenario_payload()
        replacement["label"] = "Replaced label"
        overwritten = client.post(
            "/api/scenarios", params={"overwrite": "true"}, json=replacement
        )
        assert overwritten.status_code == 200
        assert overwritten.json()["label"] == "Replaced label"

        fetched = client.get("/api/scenarios/screening_150m_base")
        assert fetched.json()["label"] == "Replaced label"


def test_invalid_scenario_is_422_and_not_written(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime) as client:
        payload = scenario_payload()
        payload["energy"]["unknown_lever"] = 1

        response = client.post("/api/scenarios", json=payload)

        assert response.status_code == 422
        assert not (runtime / "scenarios" / "screening_150m_base.json").exists()


def test_read_only_blocks_all_mutations(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime, read_only=True) as client:
        assert client.get("/api/health").json()["read_only"] is True

        scenario_response = client.post("/api/scenarios", json=scenario_payload())
        assert scenario_response.status_code == 403
        run_response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert run_response.status_code == 403


def test_read_only_environment_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLEWS_UI_READ_ONLY", "1")
    runtime = tmp_path / "runtime"
    app = create_app(
        runtime, allow_runs=True, runner_command=(sys.executable, "-c", "pass")
    )
    with TestClient(app) as client:
        response = client.post("/api/scenarios", json=scenario_payload())
        assert response.status_code == 403


def test_malformed_read_only_flag_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLEWS_UI_READ_ONLY", "sometimes")
    with pytest.raises(RuntimeError, match="CLEWS_UI_READ_ONLY"):
        create_app(tmp_path / "runtime")


# ---------------------------------------------------------------------------
# Run queue, status, and SSE
# ---------------------------------------------------------------------------


def test_enqueue_status_and_sse_with_fake_runner(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)

        response = client.post("/api/runs", json={"scenario_id": "screening_150m_base"})
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]

        job = wait_for_terminal(client, run_id)
        assert job["status"] == "completed"
        assert job["exit_code"] == 0
        assert job["runner_status"] == "completed"
        assert job["scenario_id"] == "screening_150m_base"
        assert "--scenario" in job["command"]
        assert "--out" in job["command"]

        summary_path = Path(job["output_dir"]) / "run_summaries.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["status"] == "completed"
        assert summary["scenario_id"] == "screening_150m_base"

        listing = client.get("/api/runs")
        assert listing.status_code == 200
        assert [item["run_id"] for item in listing.json()["runs"]] == [run_id]

        with client.stream("GET", f"/api/runs/{run_id}/events") as events:
            assert events.status_code == 200
            assert events.headers["content-type"].startswith("text/event-stream")
            body = "".join(events.iter_text())

        assert "event: started" in body
        assert "fake runner started" in body
        assert "fake runner finished" in body
        assert "event: completed" in body


def test_enqueue_requires_allow_runs(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime, allow_runs=False) as client:
        create_scenario(client)
        response = client.post("/api/runs", json={"scenario_id": "screening_150m_base"})
        assert response.status_code == 403


def test_enqueue_unknown_scenario_is_404(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        response = client.post("/api/runs", json={"scenario_id": "missing_scenario"})
        assert response.status_code == 404


def test_sse_emits_heartbeat_for_slow_runner(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SLOW_RUNNER)
    with make_client(runtime, runner=runner, heartbeat_seconds=0.01) as client:
        create_scenario(client)
        response = client.post("/api/runs", json={"scenario_id": "screening_150m_base"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/events") as events:
            chunks: list[str] = []
            for chunk in events.iter_text():
                chunks.append(chunk)
                if ": heartbeat" in "".join(chunks):
                    break

        assert ": heartbeat" in "".join(chunks)
        wait_for_terminal(client, run_id)


def test_restart_marks_active_jobs_interrupted(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runs_dir = runtime / "runs"
    runs_dir.mkdir(parents=True)
    store = RunStore(runs_dir)
    run_id = store.new_run_id()
    store.create(
        run_id=run_id,
        scenario_id="screening_150m_base",
        scenario_path=runtime / "scenarios" / "screening_150m_base.json",
        output_dir=runs_dir / run_id / "output",
        command=(sys.executable, "-c", "pass"),
    )
    store.update(run_id, status="running", pid=12345)

    with make_client(runtime, runner=(sys.executable, "-c", "pass")) as client:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "interrupted"
        assert "restarted" in job["message"]

        with client.stream("GET", f"/api/runs/{run_id}/events") as events:
            body = "".join(events.iter_text())
        assert "event: interrupted" in body


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        for path in (
            "/api/scenarios/%2E%2E%2F%2E%2E%2Fetc%2Fpasswd",
            "/api/scenarios/..%2F..%2Fetc%2Fpasswd",
            "/api/runs/%2E%2E%2F%2E%2E%2Fetc%2Fpasswd",
        ):
            response = client.get(path)
            assert response.status_code in {400, 404}, (path, response.status_code)

        response = client.post("/api/runs", json={"scenario_id": "../etc/passwd"})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Milestone-1a runner stub
# ---------------------------------------------------------------------------


def test_runner_stub_writes_not_implemented_summary(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario_payload()), encoding="utf-8")
    output_dir = tmp_path / "output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflows.ui.run_scenario",
            "--scenario",
            str(scenario_path),
            "--out",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output_dir / "run_summaries.json").read_text("utf-8"))
    assert summary["status"] == "not_implemented"
    assert summary["scenario_id"] == "screening_150m_base"
    assert summary["schema_version"] == "clews.scenario.v1"
    assert summary["artifacts"] == []
