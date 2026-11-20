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
