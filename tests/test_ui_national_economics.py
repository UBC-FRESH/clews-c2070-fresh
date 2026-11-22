"""Contract tests for the read-only national economics panel."""

from __future__ import annotations

import csv
import functools
import hashlib
import http.server
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_ui as exporter

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "ui/public/data/economics"
SOURCE = ROOT / "data/derived/national/harvest_basis/national_economics_v2_1"
TIER_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/national_tier_coverage"
    / "national_tier_coverage.csv"
)
def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_national() -> dict:
    return json.loads((BUNDLE / "national.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_export_has_13_jurisdictions_and_both_national_diagnostics() -> None:
    payload = _load_national()
    diagnostic = payload["national_diagnostic"]

    assert payload["schema_version"] == "economics/national/1"
    assert payload["model_id"] == "national_economics_v2_1"
    assert payload["coverage"]["province_count"] == 13
    assert payload["coverage"]["jurisdiction_rows"] == 13
    assert len(payload["jurisdictions"]) == 13
    assert len({row["province"] for row in payload["jurisdictions"]}) == 13

    assert diagnostic["donor_flat"]["scenario_role"] == "base"
    assert diagnostic["donor_flat"]["low_cad_m3"] == pytest.approx(-94.7046)
    assert diagnostic["donor_flat"]["base_cad_m3"] == pytest.approx(33.1365)
    assert diagnostic["donor_flat"]["high_cad_m3"] == pytest.approx(81.3168)
    assert diagnostic["stumpage_scaled"]["scenario_role"] == "sensitivity"
    assert diagnostic["stumpage_scaled"]["low_cad_m3"] == pytest.approx(-130.0389)
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] == pytest.approx(-16.1220)
    assert diagnostic["stumpage_scaled"]["high_cad_m3"] == pytest.approx(38.4085)
    assert diagnostic["allocation_volume_m3"] == pytest.approx(118_977_339.1)
    assert diagnostic["combined_national_net_value"] is False
    assert payload["decision"]["base_scenario"] == "donor_flat"
    assert payload["decision"]["sensitivity_scenario"] == "stumpage_scaled"
    assert payload["decision"]["stumpage_scaled_preferred"] is False


def test_export_recomputes_every_margin_from_the_committed_csvs() -> None:
    payload = _load_national()
    summary = {
        (row["price_scenario"], row["province"]): row
        for row in _read_csv(SOURCE / "national_margin_summary.csv")
        if row["row_type"] == "province"
    }
    for jurisdiction in payload["jurisdictions"]:
        province = jurisdiction["province"]
        for scenario in ("donor_flat", "stumpage_scaled"):
            source = summary[(scenario, province)]
            values = jurisdiction[scenario]
            assert values["low_cad_m3"] == pytest.approx(
                float(source["margin_low_cad_m3"])
            )
            assert values["base_cad_m3"] == pytest.approx(
                float(source["margin_base_cad_m3"])
            )
            assert values["high_cad_m3"] == pytest.approx(
                float(source["margin_high_cad_m3"])
            )
            assert values["price_tier"] == source["price_tier"]
            assert values["cost_tier"] == source["cost_tier"]

    comparison = {
        row["province"]: row
        for row in _read_csv(SOURCE / "scenario_comparison.csv")
        if row["province"] != "NATIONAL"
    }
    for jurisdiction in payload["jurisdictions"]:
        province = jurisdiction["province"]
        assert jurisdiction["scaled_minus_donor_flat_cad_m3"] == pytest.approx(
            float(comparison[province]["scaled_minus_donor_flat_cad_m3"])
        )
    national_comparison = next(
        row
        for row in _read_csv(SOURCE / "scenario_comparison.csv")
        if row["province"] == "NATIONAL"
    )
    assert payload["national_comparison"][
        "scaled_minus_donor_flat_cad_m3"
    ] == pytest.approx(
        float(national_comparison["scaled_minus_donor_flat_cad_m3"])
    )


def test_tier_coverage_shares_are_volume_weighted_from_the_audit_csv() -> None:
    payload = _load_national()
    coverage = payload["tier_coverage"]
    rows = _read_csv(TIER_SOURCE)
    totals: dict[str, float] = {}
    total = 0.0
    for row in rows:
        volume = float(row["harvest_volume_m3"])
        totals[row["tier"]] = totals.get(row["tier"], 0.0) + volume
        total += volume
    shares = coverage["volume_weighted_shares"]

    assert coverage["cell_count"] == 65
    assert coverage["cell_counts"] == {
        "reported_average": 3,
        "proxy_model": 2,
        "donor_proxy": 60,
        "assumption": 0,
    }
    assert shares["reported_average"] == pytest.approx(
        totals["reported_average"] / total, abs=1e-8
    )
    assert shares["proxy_model"] == pytest.approx(
        totals["proxy_model"] / total, abs=1e-8
    )
    assert shares["donor_proxy"] == pytest.approx(
        totals["donor_proxy"] / total, abs=1e-8
    )
    assert shares["reported_average"] == pytest.approx(0.17924672)
    assert shares["proxy_model"] == pytest.approx(0.13440737)
    assert shares["donor_proxy"] == pytest.approx(0.68634592)
    assert sum(shares.values()) == pytest.approx(1.0)
    pct = coverage["volume_weighted_pct"]
    assert pct["reported_average"] == pytest.approx(17.9247, abs=1e-4)
    assert pct["proxy_model"] == pytest.approx(13.4407, abs=1e-4)
    assert pct["donor_proxy"] == pytest.approx(68.6346, abs=1e-4)


def test_labels_are_explicit_and_never_claim_observation_or_policy_grade() -> None:
    payload = _load_national()
    labels = payload["labels"]
    assert labels["evidence"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["combined_national_net_value"] is False
    assert labels["national_diagnostic_label"] == "model_estimate_not_policy"
    assert labels["forest_lp_input"] is False
    assert labels["public_osecan_input"] is False
    assert payload["national_diagnostic"]["label"] == "model_estimate_not_policy"

    text = json.dumps(payload)
    assert '"observed": true' not in text
    assert '"policy_grade": true' not in text
    assert '"combined_national_net_value": true' not in text
    assert '"stumpage_scaled_preferred": true' not in text
    assert '"stumpage_scaled_observed": true' not in text
    assert '"stumpage_scaled_policy_grade": true' not in text
    assert "preferred scenario produces" not in text.lower()


def test_no_base_sensitivity_value_swap_or_stale_national_value() -> None:
    payload = _load_national()
    diagnostic = payload["national_diagnostic"]
    assert diagnostic["donor_flat"]["scenario_role"] == "base"
    assert diagnostic["stumpage_scaled"]["scenario_role"] == "sensitivity"
    assert diagnostic["donor_flat"]["base_cad_m3"] > 0
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] < 0
    assert diagnostic["donor_flat"]["base_cad_m3"] != pytest.approx(
        diagnostic["stumpage_scaled"]["base_cad_m3"]
    )
    assert diagnostic["donor_flat"]["base_cad_m3"] != pytest.approx(
        payload["national_comparison"]["scaled_minus_donor_flat_cad_m3"]
    )
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] != pytest.approx(
        33.1365
    )
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] != pytest.approx(
        payload["national_comparison"]["scaled_minus_donor_flat_cad_m3"]
    )
    scenario_ids = {scenario["id"] for scenario in payload["scenarios"]}
    assert scenario_ids == {"donor_flat", "stumpage_scaled"}
    assert "v1_flat" not in scenario_ids
    assert payload["national_comparison"]["comparison_status"] == (
        "method_change_not_observed_market_change"
    )
    assert payload["national_comparison"][
        "dues_comparable_to_delivered_price"
    ] is False


def test_export_rebuild_matches_the_committed_payload(tmp_path: Path) -> None:
    payload, manifest = exporter.build_export(check_deletions=False)
    out = tmp_path / "national.json"
    manifest_out = tmp_path / "national.manifest.json"
    exporter.write_export(payload, manifest, out, manifest_out)

    assert out.read_bytes() == (BUNDLE / "national.json").read_bytes()
    generated = manifest["generated"][0]
    assert generated["bytes"] == (BUNDLE / "national.json").stat().st_size
    assert generated["sha256"] == _sha256(BUNDLE / "national.json")
    assert json.loads(manifest_out.read_text(encoding="utf-8"))[
        "validation"
    ]["ok"] is True


def test_manifest_hashes_payload_and_every_source() -> None:
    manifest = json.loads(
        (BUNDLE / "national.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "economics/national-manifest/1"
    assert manifest["validation"]["ok"] is True
    assert manifest["validation"]["jurisdiction_count"] == 13
    assert manifest["validation"]["base_national_margin_cad_m3"] == pytest.approx(
        33.1365
    )
    assert manifest["validation"][
        "sensitivity_national_margin_cad_m3"
    ] == pytest.approx(-16.1220)
    assert manifest["validation"]["observed_claim"] is False
    assert manifest["validation"]["policy_grade_claim"] is False
    assert manifest["validation"]["combined_national_net_value"] is False
    assert manifest["validation"]["forest_lp_input"] is False
    assert manifest["validation"]["public_osecan_input"] is False

    generated = manifest["generated"][0]
    assert generated == manifest["files"][0]
    assert generated["path"] == "economics/national.json"
    assert generated["bytes"] == (BUNDLE / "national.json").stat().st_size
    assert generated["sha256"] == _sha256(BUNDLE / "national.json")

    paths = {item["path"] for item in manifest["sources"]}
    assert paths == {
        "data/derived/national/harvest_basis/national_economics_v2_1/national_margin_estimates.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/national_margin_summary.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/scenario_comparison.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/tier_coverage.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/summary.json",
        "data/derived/national/harvest_basis/national_tier_coverage/national_tier_coverage.csv",
    }
    for item in manifest["sources"]:
        path = ROOT / item["path"]
        assert item["bytes"] == path.stat().st_size
        assert item["sha256"] == _sha256(path)


def test_ui_panel_is_read_only_and_has_only_scenario_and_download_controls() -> None:
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    panel = html.split('id="panel-economics"', 1)[1].split(
        'id="panel-osecan"', 1
    )[0]
    national = panel.split('id="national-economics"', 1)[1]

    assert 'id="national-economics"' in panel
    assert 'id="national-scenario"' in national
    assert national.count("<select") == 1
    assert "<button" not in national
    assert "<form" not in national
    assert 'id="national-chart"' in national
    assert 'id="national-table"' in national
    assert 'id="national-downloads"' in national
    assert national.count("<a ") == 0  # links are rendered from validated JSON
    for forbidden in (
        "osecan-queue",
        "osecan-harvest",
        "run-mode",
        "forest-lp",
        "edit-",
    ):
        assert forbidden not in national

    assert "loadJSON('economics/national.json')" in app_js
    assert "loadJSON('economics/national.manifest.json')" in app_js
    assert "function validateNational" in app_js
    assert "function renderNational" in app_js
    assert "no combined national net value" in app_js
    assert "not Forest LP/OSeCAN input" in app_js
    assert "observed=false" in app_js
    assert "policy_grade=false" in app_js


def test_static_payload_and_manifest_load_over_http() -> None:
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    handler = functools.partial(QuietHandler, directory=str(ROOT / "ui"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(
            base + "/public/data/economics/national.json", timeout=10
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(
            base + "/public/data/economics/national.manifest.json", timeout=10
        ) as response:
            assert response.status == 200
            manifest = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["coverage"]["jurisdiction_rows"] == 13
    assert manifest["generated"][0]["path"] == "economics/national.json"
    assert manifest["generated"][0]["sha256"] == _sha256(
        BUNDLE / "national.json"
    )


def test_unexpected_git_deletions_are_fail_closed() -> None:
    assert exporter._deletion_lines(" D data/foo.csv") == [" D data/foo.csv"]
    assert exporter._deletion_lines("D  data/foo.csv") == ["D  data/foo.csv"]
    assert exporter._deletion_lines("?? data/new.csv") == []
    assert exporter._deletion_lines(" M ui/app.js") == []


def test_app_js_syntax() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", str(ROOT / "ui/app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
