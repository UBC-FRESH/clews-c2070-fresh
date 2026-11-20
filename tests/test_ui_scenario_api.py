"""Tests for the clews.scenario.v1 contract and live-runner API skeleton."""

from __future__ import annotations

import copy
import json
import os
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
    max_concurrent_runs: int = 2,
) -> TestClient:
    app = create_app(
        runtime,
        allow_runs=allow_runs,
        read_only=read_only,
        runner_command=runner,
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=0.01,
        max_concurrent_runs=max_concurrent_runs,
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
        lambda p: p["constraints"].update({"set_id": "invented_set"}),
        lambda p: p["provenance"].update({"created_utc": "2026-11-20T00:00:00"}),
        lambda p: p["provenance"].update({"model_commit": "   "}),
        lambda p: p.update({"label": "   "}),
        lambda p: p["harvest"].update({"level_m3_per_year": True}),
        lambda p: p["energy"].update({"nuclear_cap_gw": True}),
        lambda p: p["energy"].update({"pwr_bio_cap_fraction": False}),
        lambda p: p["energy"]["carbon_price_schedule"][0].update({"year": True}),
        lambda p: p["energy"]["carbon_price_schedule"][0].update(
            {"price_usd_per_tco2e": True}
        ),
        lambda p: p["energy"].update(
            {
                "carbon_price_schedule": [
                    {"year": 2021 + (index % 50), "price_usd_per_tco2e": 1.0}
                    for index in range(51)
                ]
            }
        ),
    ],
)
def test_contract_rejects_invalid_payload(mutate) -> None:
    payload = copy.deepcopy(scenario_payload())
    mutate(payload)

    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)


def test_contract_accepts_both_constraint_sets() -> None:
    for set_id in ("screening_v0", "policy_test_v1"):
        payload = copy.deepcopy(scenario_payload())
        payload["constraints"]["set_id"] = set_id
        scenario = Scenario.model_validate(payload)
        assert scenario.constraints.set_id == set_id

    payload = copy.deepcopy(scenario_payload())
    payload["constraints"]["set_id"] = "unknown_set"
    with pytest.raises(ValidationError, match="unsupported constraint set"):
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
# Milestone-1b runner dry-run integration
# ---------------------------------------------------------------------------


MODEL_ROOT = Path(
    os.environ.get("CLEWS_OSECAN_MODEL_ROOT", REPO_ROOT / "runtime/osecan_model")
)
MODEL_AVAILABLE = (MODEL_ROOT / "model_manifest.json").is_file()


@pytest.mark.skipif(not MODEL_AVAILABLE, reason="disposable OSeCAN model copy is unavailable")
def test_runner_dry_run_writes_completed_summary(tmp_path: Path) -> None:
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
            "--dry-run",
            "--model-root",
            str(MODEL_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output_dir / "run_summaries.json").read_text("utf-8"))
    assert summary["status"] == "completed"
    assert summary["dry_run"] is True
    assert summary["scenario_id"] == "screening_150m_base"
    assert summary["schema_version"] == "clews.scenario.v1"
    assert summary["constraints"]["set_id"] == "screening_v0"
    assert summary["constraints"]["policy_grade"] is False
    assert summary["supply"]["checks"]["tier_sum_equals_rest_all_years"] is True
    assert summary["artifacts"]


# ---------------------------------------------------------------------------
# Hardening regressions: startup, snapshots, shutdown, summaries, concurrency
# ---------------------------------------------------------------------------

SNAPSHOT_RUNNER = """\
import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

time.sleep(0.25)
scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "seen.json").write_text(json.dumps(scenario), encoding="utf-8")
(out / "run_summaries.json").write_text(
    json.dumps({"status": "completed", "message": "snapshot runner completed"}),
    encoding="utf-8",
)
"""

OUTPUT_RUNNER = """\
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
rows = []
for year in range(2021, 2071):
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "PWRHYDB1",
            "category": "dispatch",
            "zone": "B1",
            "year": year,
            "value_pj": 10.0 if year != 2050 else 20.0,
        }
    )
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "PWRBIOB1",
            "category": "dispatch",
            "zone": "B1",
            "year": year,
            "value_pj": 5.0,
        }
    )
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "PWRBCWB1",
            "category": "dispatch",
            "zone": "B1",
            "year": year,
            "value_pj": 4.0,
        }
    )
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "PWRTRNB1",
            "category": "dispatch",
            "zone": "B1",
            "year": year,
            "value_pj": 3.0,
        }
    )
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "SUPBCWT1",
            "category": "supply",
            "zone": "CAN",
            "year": year,
            "value_pj": 30.0,
        }
    )
    rows.append(
        {
            "run": scenario["scenario_id"],
            "technology": "RNWBIO",
            "category": "other",
            "zone": "CAN",
            "year": year,
            "value_pj": 999.0,
        }
    )
with (out / "dispatch_by_zone.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(
        handle,
        fieldnames=["run", "technology", "category", "zone", "year", "value_pj"],
    )
    writer.writeheader()
    writer.writerows(rows)
(out / "run_summaries.json").write_text(
    json.dumps(
        {
            "status": "completed",
            "scenario_id": scenario["scenario_id"],
            "scenario_label": scenario["label"],
            "message": "output runner completed",
            "screening_only": True,
            "policy_grade": False,
            "solver_status": "optimal",
            "objective_musd": 123.5,
            "emissions_total_mt": 0.5,
            "emissions_2050_mt": 0.0,
            "supply": {"national_total_pj_2050": 30.0},
        }
    ),
    encoding="utf-8",
)
print("output runner wrote dispatch", flush=True)
"""

NO_SUMMARY_RUNNER = """\
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
parser.parse_args()
print("runner exited without a summary", flush=True)
"""

CORRUPT_SUMMARY_RUNNER = """\
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "run_summaries.json").write_text("[]", encoding="utf-8")
"""

FAILED_SUMMARY_RUNNER = """\
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "run_summaries.json").write_text(
    json.dumps({"status": "failed", "message": "solver reported boom"}),
    encoding="utf-8",
)
"""

SHUTDOWN_RUNNER = """\
import os
import time

print(os.getpid(), flush=True)
time.sleep(30)
"""

ENV_RUNNER = """\
import argparse
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
payload = {
    "status": "completed",
    "unbuffered": os.environ.get("PYTHONUNBUFFERED"),
    "pythonpath_has_repo": str(Path.cwd()) in os.environ.get("PYTHONPATH", ""),
    "stdin_text": sys.stdin.read(),
}
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "run_summaries.json").write_text(json.dumps(payload), encoding="utf-8")
"""


def test_read_only_startup_and_shutdown_write_nothing(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime, read_only=True) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/scenarios").json() == {"scenarios": []}
        assert client.get("/api/runs").json() == {"runs": []}
        assert not runtime.exists()
    assert not runtime.exists()


def test_enqueue_uses_immutable_scenario_snapshot(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SNAPSHOT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        original = create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": original["scenario_id"]}
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]

        replacement = scenario_payload()
        replacement["label"] = "mutated after enqueue"
        overwritten = client.post(
            "/api/scenarios",
            params={"overwrite": "true"},
            json=replacement,
        )
        assert overwritten.status_code == 200

        job = wait_for_terminal(client, run_id)
        assert job["status"] == "completed"
        snapshot = Path(job["scenario_path"])
        assert snapshot == runtime / "runs" / run_id / "scenario.json"
        assert json.loads(snapshot.read_text("utf-8"))["label"] == original["label"]
        seen = json.loads((Path(job["output_dir"]) / "seen.json").read_text("utf-8"))
        assert seen["label"] == original["label"]


@pytest.mark.parametrize(
    ("source", "expected_message"),
    [
        (NO_SUMMARY_RUNNER, "no valid run_summaries.json"),
        (CORRUPT_SUMMARY_RUNNER, "no valid run_summaries.json"),
        (FAILED_SUMMARY_RUNNER, "solver reported boom"),
    ],
)
def test_exit_zero_requires_a_valid_summary(
    tmp_path: Path, source: str, expected_message: str
) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, source)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert response.status_code == 202
        job = wait_for_terminal(client, response.json()["run_id"])
        assert job["status"] == "failed"
        assert expected_message in job["message"]

        events, _ = RunStore(runtime / "runs").read_events(job["run_id"], 0)
        assert events[-1]["event"] == "failed"
        assert job["event_count"] == events[-1]["seq"]


def test_max_concurrent_runs_keeps_excess_jobs_queued(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SLOW_RUNNER)
    with make_client(runtime, runner=runner, max_concurrent_runs=1) as client:
        create_scenario(client)
        first = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        second = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert first.status_code == second.status_code == 202
        first_id = first.json()["run_id"]
        second_id = second.json()["run_id"]
        assert second.json()["status"] == "queued"

        assert wait_for_terminal(client, first_id)["status"] == "completed"
        assert wait_for_terminal(client, second_id)["status"] == "completed"


def test_shutdown_kills_process_group_and_marks_interrupted(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SHUTDOWN_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f"/api/runs/{run_id}").json()
            if job["status"] == "running" and job.get("pid"):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("runner did not start")
        pid = int(job["pid"])
        assert os.getpgid(pid) == pid

    store = RunStore(runtime / "runs")
    job = store.read(run_id)
    assert job["status"] == "interrupted"
    events, _ = store.read_events(run_id, 0)
    assert events[-1]["event"] == "interrupted"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"runner process {pid} survived shutdown")


def test_events_skip_corrupt_lines_and_reject_non_object_jobs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runtime" / "runs"
    store = RunStore(runs_dir)
    run_id = store.new_run_id()
    store.create(
        run_id=run_id,
        scenario_id="screening_150m_base",
        scenario_path=tmp_path / "scenario.json",
        output_dir=runs_dir / run_id / "output",
        command=(sys.executable, "-c", "pass"),
    )
    events_path = runs_dir / run_id / "events.jsonl"
    payload = (
        b"\n"
        b"not json\n"
        b"[]\n"
        b'{"seq": 1, "ts": "2026-11-20T00:00:00+00:00", "event": "started", '
        b'"level": "info", "message": "ok"}\n'
    )
    events_path.write_bytes(payload)

    events, offset = store.read_events(run_id, 0)
    assert [event["event"] for event in events] == ["started"]
    assert offset == len(payload)

    (runs_dir / run_id / "job.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        store.read(run_id)
    assert store.list_jobs() == []


def test_corrupt_job_does_not_break_startup_repair(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runs_dir = runtime / "runs"
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
    broken = runs_dir / "broken-run"
    broken.mkdir(parents=True)
    (broken / "job.json").write_text("[]", encoding="utf-8")

    with make_client(runtime, runner=(sys.executable, "-c", "pass")) as client:
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "interrupted"
        listing = client.get("/api/runs").json()["runs"]
        assert [item["run_id"] for item in listing] == [run_id]


def test_sse_last_event_id_resumes_after_sequence(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        wait_for_terminal(client, run_id)

        with client.stream(
            "GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"}
        ) as events:
            body = "".join(events.iter_text())
        assert "event: started" not in body
        assert "event: completed" in body


def test_runner_environment_and_stdin(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, ENV_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        job = wait_for_terminal(client, response.json()["run_id"])
        assert job["status"] == "completed"
        summary = json.loads(
            (Path(job["output_dir"]) / "run_summaries.json").read_text("utf-8")
        )
        assert summary["unbuffered"] == "1"
        assert summary["pythonpath_has_repo"] is True
        assert summary["stdin_text"] == ""


def test_shutdown_marks_queued_jobs_interrupted(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SLOW_RUNNER)
    with make_client(runtime, runner=runner, max_concurrent_runs=1) as client:
        create_scenario(client)
        first = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        second = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert second.json()["status"] == "queued"
        first_id = first.json()["run_id"]
        second_id = second.json()["run_id"]

    store = RunStore(runtime / "runs")
    assert store.read(first_id)["status"] == "interrupted"
    queued_job = store.read(second_id)
    assert queued_job["status"] == "interrupted"
    events, _ = store.read_events(second_id, 0)
    assert events[-1]["event"] == "interrupted"
    assert "before the run started" in queued_job["message"]


# ---------------------------------------------------------------------------
# Milestone-1c: constraints, normalized output, CLI runner override
# ---------------------------------------------------------------------------


def test_constraints_endpoint_exposes_both_supported_sets(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    with make_client(runtime, allow_runs=True) as client:
        response = client.get("/api/constraints")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["default_set_id"] == "screening_v0"
        assert payload["schema_version"] == "clews.constraint_sets.v2"
        assert len(payload["sets"]) == 2
        by_id = {item["set_id"]: item for item in payload["sets"]}
        constraint_set = by_id["screening_v0"]
        assert constraint_set["status"] == "screening_only"
        assert constraint_set["policy_grade"] is False
        assert constraint_set["available"] is True
        assert any("reserve-margin" in note for note in constraint_set["notes"])
        policy = by_id["policy_test_v1"]
        assert policy["status"] == "draft"
        assert policy["policy_grade"] is False
        assert policy["available"] is True
        assert policy["screening_only"] is False
        assert policy["policy_grade_reason"]
        assert policy["structural_gaps"]
        defaults = constraint_set["defaults"]
        assert defaults["default_harvest_level_m3_per_year"] == 118_977_339.0
        assert defaults["default_recovery_fraction"] == 0.6
        assert defaults["pwr_bio_cap_fraction"] == 0.05
        assert defaults["nuclear_cap_gw"] == 0.0
        assert defaults["wind_cost_usd_per_gj"] == 3.0
        assert defaults["wind_cap_fraction"] == 0.2
        assert defaults["t3_cap_pj"] == pytest.approx(39.873714)
        assert defaults["t4_cap_pj"] == pytest.approx(64.265464)
        assert defaults["carbon_price_usd_per_tco2e_2026"] == 115.0
        assert defaults["harvest_levels_m3_per_year"]["200"] == 200_000_000.0
        assert defaults["harvest_feasibility"]["levels"]["200"] == "fail"
        assert policy["defaults"] == defaults


def test_run_output_endpoint_normalizes_summary_and_dispatch(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, OUTPUT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"

        output_response = client.get(f"/api/runs/{run_id}/output")
        assert output_response.status_code == 200, output_response.text
        output = output_response.json()
        assert output["run_id"] == run_id
        assert output["scenario_id"] == "screening_150m_base"
        assert output["status"] == "completed"
        assert output["screening_only"] is True
        assert output["policy_grade"] is False
        assert output["row_count"] == 250
        assert output["categories"] == ["dispatch", "supply"]
        assert output["units_by_category"] == {
            "dispatch": "PJ electricity",
            "supply": "PJ fuel",
        }
        assert "other" not in output["categories"]
        assert len(output["technology"]) == 250
        assert len(output["category"]) == 250
        assert len(output["zone"]) == 250
        assert len(output["year"]) == 250
        assert len(output["value_pj"]) == 250
        assert output["metrics"]["objective"] == 123.5
        assert output["metrics"]["emissions_total"] == 0.5
        assert output["metrics"]["national_bcw_2050"] == 30.0
        assert output["metrics"]["pwrbcw_2050"] == 4.0
        assert output["metrics"]["pwrbio_2050"] == 5.0
        assert output["metrics"]["bio_share_2050"] == pytest.approx(5.0 / 29.0)
        assert output["source"] == "output/dispatch_by_zone.csv"
        assert output["summary"]["solver_status"] == "optimal"


def test_run_output_is_409_while_run_is_active(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SLOW_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        early = client.get(f"/api/runs/{run_id}/output")
        assert early.status_code in {409, 425}, early.text
        wait_for_terminal(client, run_id)


def test_run_output_is_404_without_dispatch(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        missing = client.get(f"/api/runs/{run_id}/output")
        assert missing.status_code == 404
        assert "dispatch_by_zone.csv" in missing.json()["detail"]


def test_cli_runner_command_override_preserves_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    import shlex

    import workflows.ui.server as server_module

    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SNAPSHOT_RUNNER)
    captured: dict[str, object] = {}

    def fake_uvicorn_run(application, **kwargs) -> None:
        captured["app"] = application

    monkeypatch.setattr(server_module.uvicorn, "run", fake_uvicorn_run)
    server_module.main(
        [
            "--runtime-dir",
            str(runtime),
            "--allow-runs",
            "--runner-command",
            shlex.join(runner),
        ]
    )
    app = captured["app"]
    assert app.state.settings.runner_command == runner

    with TestClient(app) as client:
        created = create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": created["scenario_id"]}
        )
        assert response.status_code == 202, response.text
        job = wait_for_terminal(client, response.json()["run_id"])
        assert job["status"] == "completed"
        command = list(job["command"])
        assert tuple(command[: len(runner)]) == runner
        assert command[-4:] == [
            "--scenario",
            job["scenario_path"],
            "--out",
            job["output_dir"],
        ]
        seen = json.loads(
            (Path(job["output_dir"]) / "seen.json").read_text(encoding="utf-8")
        )
        assert seen["label"] == created["label"]


def test_run_output_rejects_failed_and_interrupted_runs(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAILED_SUMMARY_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        assert wait_for_terminal(client, run_id)["status"] == "failed"
        output = client.get(f"/api/runs/{run_id}/output")
        assert output.status_code == 409, output.text
        assert "completed" in output.json()["detail"]


def test_run_output_rejects_output_symlink_escape(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, OUTPUT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        job = wait_for_terminal(client, run_id)
        output_dir = Path(job["output_dir"])
        outside = tmp_path / "outside-output"
        outside.mkdir()
        (outside / "run_summaries.json").write_text(
            json.dumps({"status": "completed"}), encoding="utf-8"
        )
        (outside / "dispatch_by_zone.csv").write_text(
            "run,technology,category,zone,year,value_pj\n"
            "x,PWRHYDB1,dispatch,B1,2050,1.0\n",
            encoding="utf-8",
        )
        output_dir.rename(tmp_path / "moved-output")
        output_dir.symlink_to(outside, target_is_directory=True)

        escaped = client.get(f"/api/runs/{run_id}/output")
        assert escaped.status_code == 500, escaped.text
        assert "escapes" in escaped.json()["detail"]
        assert str(tmp_path) not in escaped.json()["detail"]


def test_run_output_rejects_symlinked_dispatch_and_malformed_files(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, OUTPUT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        job = wait_for_terminal(client, run_id)
        output_dir = Path(job["output_dir"])
        dispatch = output_dir / "dispatch_by_zone.csv"
        summary = output_dir / "run_summaries.json"
        summary_text = summary.read_text(encoding="utf-8")

        outside = tmp_path / "outside-dispatch.csv"
        outside.write_text(
            "run,technology,category,zone,year,value_pj\n"
            "x,PWRHYDB1,dispatch,B1,2050,1.0\n",
            encoding="utf-8",
        )
        dispatch.unlink()
        dispatch.symlink_to(outside)
        escaped = client.get(f"/api/runs/{run_id}/output")
        assert escaped.status_code == 500, escaped.text
        assert "escapes" in escaped.json()["detail"]

        dispatch.unlink()
        dispatch.write_text(
            "run,technology,category,zone,year,value_pj\n"
            "x,PWRHYDB1,mystery,B1,2050,1.0\n",
            encoding="utf-8",
        )
        malformed_dispatch = client.get(f"/api/runs/{run_id}/output")
        assert malformed_dispatch.status_code == 500
        assert "unknown category" in malformed_dispatch.json()["detail"]

        dispatch.write_text(
            "run,technology,category,zone,year,value_pj\n"
            "x,PWRHYDB1,dispatch,B1,2050,1.0\n",
            encoding="utf-8",
        )
        summary.write_text("[]", encoding="utf-8")
        malformed_summary = client.get(f"/api/runs/{run_id}/output")
        assert malformed_summary.status_code == 500
        assert "not a JSON object" in malformed_summary.json()["detail"]
        summary.write_text(summary_text, encoding="utf-8")


def test_constraints_failure_is_500_and_read_only_is_200(
    tmp_path: Path, monkeypatch
) -> None:
    import workflows.ui.server as server_module

    runtime = tmp_path / "runtime"
    with make_client(runtime, read_only=True) as client:
        response = client.get("/api/constraints")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload["provenance"]["base_data_commit"], str)
        assert isinstance(payload["provenance"]["model_commit"], str)

    missing = tmp_path / "missing-constraints.json"
    monkeypatch.setattr(server_module, "CONSTRAINTS_PATH", missing)
    with make_client(tmp_path / "runtime-missing", allow_runs=False) as client:
        broken = client.get("/api/constraints")
        assert broken.status_code == 500, broken.text
        assert "canonical constraint set" in broken.json()["detail"]


def test_cli_runner_command_rejects_empty_and_bad_quotes(
    tmp_path: Path, monkeypatch
) -> None:
    import workflows.ui.server as server_module

    def fail_uvicorn(*args, **kwargs) -> None:
        raise AssertionError("uvicorn.run should not be reached")

    monkeypatch.setattr(server_module.uvicorn, "run", fail_uvicorn)
    with pytest.raises(SystemExit, match="must not be empty"):
        server_module.main(
            ["--runtime-dir", str(tmp_path), "--runner-command", ""]
        )
    with pytest.raises(SystemExit, match="not valid shell syntax"):
        server_module.main(
            ["--runtime-dir", str(tmp_path), "--runner-command", "'"]
        )


BLOCKING_RUNNER = """\
import argparse
import json
import signal
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

def handle_term(signum, frame):
    (out / "run_summaries.json").write_text(
        json.dumps({"status": "completed", "message": "late completion after cancel"}),
        encoding="utf-8",
    )
    print("late completion written", flush=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_term)
print("blocking runner ready", flush=True)
while True:
    time.sleep(0.05)
"""


def wait_for_status(
    client: TestClient, run_id: str, status: str, timeout_seconds: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = client.get(f"/api/runs/{run_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach {status}")


def test_cancel_queued_run_never_starts(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SLOW_RUNNER)
    with make_client(runtime, runner=runner, max_concurrent_runs=1) as client:
        create_scenario(client)
        first = client.post("/api/runs", json={"scenario_id": "screening_150m_base"})
        second = client.post("/api/runs", json={"scenario_id": "screening_150m_base"})
        first_id = first.json()["run_id"]
        second_id = second.json()["run_id"]
        assert second.json()["status"] == "queued"

        cancelled = client.post(f"/api/runs/{second_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert wait_for_terminal(client, first_id)["status"] == "completed"
        time.sleep(0.2)
        assert client.get(f"/api/runs/{second_id}").json()["status"] == "cancelled"
        events, _ = RunStore(runtime / "runs").read_events(second_id, 0)
        assert events[-1]["event"] == "cancelled"


def test_cancel_running_run_kills_group_and_appends_terminal_event(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, BLOCKING_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        job = wait_for_status(client, run_id, "running")
        pid = int(job["pid"])

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        job = cancelled.json()
        assert job["status"] == "cancelled"
        assert job["finished_utc"]
        events, _ = RunStore(runtime / "runs").read_events(run_id, 0)
        assert events[-1]["event"] == "cancelled"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"cancelled runner {pid} survived")

        # The monitor must not overwrite the cancelled status with the late
        # completed summary written from the SIGTERM handler.
        time.sleep(0.4)
        final = client.get(f"/api/runs/{run_id}").json()
        assert final["status"] == "cancelled"
        events, _ = RunStore(runtime / "runs").read_events(run_id, 0)
        assert not any(event["event"] == "completed" for event in events)


def test_cancel_terminal_run_is_409(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, FAKE_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 409, cancelled.text
        assert "completed" in cancelled.json()["detail"]


def test_retry_uses_immutable_snapshot_and_enqueues_new_run(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, SNAPSHOT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        created = create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": created["scenario_id"]}
        )
        original_id = response.json()["run_id"]
        assert wait_for_terminal(client, original_id)["status"] == "completed"

        retried = client.post(f"/api/runs/{original_id}/retry")
        assert retried.status_code == 202, retried.text
        retry_job = retried.json()
        assert retry_job["run_id"] != original_id
        assert retry_job["scenario_id"] == created["scenario_id"]
        assert retry_job["retried_from"] == original_id
        assert Path(retry_job["scenario_path"]).is_file()

        final = wait_for_terminal(client, retry_job["run_id"])
        assert final["status"] == "completed"
        seen = json.loads(
            (Path(final["output_dir"]) / "seen.json").read_text(encoding="utf-8")
        )
        assert seen["label"] == created["label"]

        listing = client.get("/api/runs").json()["runs"]
        by_id = {item["run_id"]: item for item in listing}
        assert by_id[original_id]["can_retry"] is True
        assert by_id[original_id]["scenario"]["label"] == created["label"]


def test_retry_active_or_missing_snapshot_is_409(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, BLOCKING_RUNNER)
    with make_client(runtime, runner=runner) as client:
        create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": "screening_150m_base"}
        )
        run_id = response.json()["run_id"]
        wait_for_status(client, run_id, "running")
        assert client.post(f"/api/runs/{run_id}/retry").status_code == 409
        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        Path(cancelled.json()["scenario_path"]).unlink()
        missing = client.post(f"/api/runs/{run_id}/retry")
        assert missing.status_code == 409, missing.text


def test_list_runs_hydrates_metrics_and_output_availability(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runner = write_fake_runner(tmp_path, OUTPUT_RUNNER)
    with make_client(runtime, runner=runner) as client:
        created = create_scenario(client)
        response = client.post(
            "/api/runs", json={"scenario_id": created["scenario_id"]}
        )
        run_id = response.json()["run_id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        item = client.get("/api/runs").json()["runs"][0]
        assert item["status"] == "completed"
        assert item["output_available"] is True
        assert item["can_cancel"] is False
        assert item["can_retry"] is True
        assert item["metrics"]["objective"] == 123.5
        assert item["scenario"]["label"] == created["label"]
        assert item["scenario"]["harvest_level_m3_per_year"] == 150_000_000.0


def test_sse_drains_terminal_burst_larger_than_two_chunks(tmp_path: Path) -> None:
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
    message = "terminal burst " + ("x" * 120)
    for _ in range(6000):
        store.append_event(run_id, event="log", level="info", message=message)
    store.finish(run_id, status="completed", message="burst completed")

    with make_client(runtime) as client, client.stream(
        "GET", f"/api/runs/{run_id}/events"
    ) as events:
        body = "".join(events.iter_text())
    assert len(body.encode("utf-8")) > 2 * 256 * 1024
    assert body.count("event: log") == 6000
    assert body.count("event: completed") == 1
