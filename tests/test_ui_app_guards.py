"""Source-level guards for the live-run UI state machine.

The browser QA pass exercises these paths end to end. These assertions keep the
run-id guards and form locks from silently disappearing during refactors.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = (REPO_ROOT / "ui" / "app.js").read_text(encoding="utf-8")
API_JS = (REPO_ROOT / "ui" / "api.js").read_text(encoding="utf-8")


def test_ui_locks_creation_and_osecan_queue_during_active_run() -> None:
    assert "if (state.live.busy) return null;\n    if (state.live.activeRun)" in APP_JS
    assert "if (create) create.disabled = disabled || activeRun;" in APP_JS
    assert (
        "osecanQueue.disabled = state.live.busy || mode !== 'live' || activeRun;"
        in APP_JS
    )


def test_ui_run_callbacks_are_scoped_to_the_active_run() -> None:
    assert "function activeRunMatches(runId)" in APP_JS
    assert "if (!activeRunMatches(runId)) return;" in APP_JS
    assert "function clearPollTimer()" in APP_JS
    assert "setFormStatus('Final status is not available yet; resuming status polling…', true);" in APP_JS
    assert "startPolling(runId);" in APP_JS


def test_ui_output_source_uses_run_relative_fallback() -> None:
    assert "output.source || ('run ' + job.run_id" in APP_JS
    assert "source: output.source ||" in APP_JS or "sources: [output.source" in APP_JS


def test_api_health_schema_and_sequence_dedupe_are_guarded() -> None:
    assert "health.status !== 'ok'" in API_JS
    assert "typeof health.schema_version !== 'string'" in API_JS
    assert "payload.seq <= lastSequence" in API_JS
    assert "options.onOpen" in API_JS


def test_api_exposes_run_hydration_cancel_retry_and_cancelled_sse() -> None:
    assert "async function listRuns()" in API_JS
    assert "async function cancelRun(runId)" in API_JS
    assert "async function retryRun(runId)" in API_JS
    assert "'cancelled'" in API_JS
    assert "listRuns: listRuns" in API_JS
    assert "cancelRun: cancelRun" in API_JS
    assert "retryRun: retryRun" in API_JS


def test_ui_hydrates_runs_and_offers_cancel_retry() -> None:
    assert "async function hydrateRuns()" in APP_JS
    assert "client.listRuns()" in APP_JS
    assert "async function cancelRunById(runId)" in APP_JS
    assert "async function retryRunById(runId)" in APP_JS
    assert "runHistory" in APP_JS
    assert "state.live.sessionRuns = state.live.sessionRuns.filter(function (item) { return item.runId !== job.run_id; });" in APP_JS
    assert "'cancelled'" in APP_JS


def test_ui_log_rendering_is_bounded_and_throttled() -> None:
    assert "state.live.logLines.length > 250" in APP_JS
    assert "state.live.logRenderTimer = window.setTimeout" in APP_JS
    assert "progress" not in APP_JS.lower() or "no progress percentage is estimated" in APP_JS


def test_ui_fleet_correction_banner_and_counts() -> None:
    assert "EXPECTED_SCENARIO_COUNT = 51" in APP_JS
    assert "EXPECTED_FAMILY_COUNT = 17" in APP_JS
    assert "EXPECTED_ELIGIBLE_COUNT = 1" in APP_JS
    assert "FLEET_SCENARIO_ID = 'fleet_capacity__screening_119m_fleet'" in APP_JS
    assert "FLEET_LADDER_FAMILY = 'fleet_screening_ladder'" in APP_JS
    assert "FLEET_BANNER_TEXT = 'pre-fleet model — not comparable'" in APP_JS
    assert "badge--fleet" in APP_JS and "badge--prefleet" in APP_JS
    assert "sc.flags.fleet_corrected &&" in APP_JS
    assert "!sc.flags.superseded_for_fleet" in APP_JS


def test_ui_harvest_ladder_caveat_and_flags() -> None:
    assert (
        "Harvest is exogenous and free in this model; higher harvest lowers cost and emissions "
        "because no harvest cost/THLB/AAC constraint is modeled. This is a sensitivity ladder, "
        "not a policy frontier." in APP_JS
    )
    assert "sc.flags.pareto_eligible" in APP_JS
    assert "sc.flags.harvest_exogenous" in APP_JS
    assert "harvest_exogenous_caveat === HARVEST_CAVEAT" in APP_JS
    assert "200 Mm³ is not admissible under the documented AAC/NFD screen" in APP_JS
    assert "FEASIBILITY_CAVEAT" in APP_JS
    assert "sensitivity ladder, not a policy frontier" in APP_JS
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert html.count("200 Mm³ is not admissible") == 3
    assert "harvest-caveat--feasibility" in html


def test_ui_policy_selector_and_feasibility_badges() -> None:
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="run-constraint-set"' in html
    assert 'value="policy_test_v1"' in html
    assert "unavailable in static mode" in html
    assert 'id="run-constraint-badge"' in html
    assert "badge--feas-pass" in APP_JS
    assert "badge--feas-conditional" in APP_JS
    assert "badge--feas-fail" in APP_JS
    assert "constraintSetById" in APP_JS
    assert "constraints: { set_id: selectedSet }" in APP_JS
    assert "max_normal_harvest_m3" in APP_JS or "MAX_NORMAL_HARVEST_M3" in APP_JS
