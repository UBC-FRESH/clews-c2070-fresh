"""Contract tests for the evidence-first read-only national economics panel."""

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
from collections import defaultdict
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
QUEUE_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/national_tier_coverage"
    / "source_replacement_queue.csv"
)
STUMPAGE_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/national_economics_v2"
    / "provincial_stumpage_references.csv"
)
ALLOCATION_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_national() -> dict:
    return json.loads((BUNDLE / "national.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_export_defaults_to_evidence_coverage_and_has_13_jurisdictions() -> None:
    payload = _load_national()
    coverage = payload["evidence_coverage"]
    shares = coverage["volume_weighted"]

    assert payload["schema_version"] == "economics/national/2"
    assert payload["model_id"] == "national_economics_v2_1"
    assert payload["default_view"] == "evidence_coverage"
    assert payload["role"] == "evidence_coverage_with_national_diagnostic"
    assert payload["section_order"][0] == "evidence_coverage"
    assert payload["coverage"]["province_count"] == 13
    assert payload["coverage"]["jurisdiction_rows"] == 13
    assert len(payload["jurisdictions"]) == 13
    assert len({row["province"] for row in payload["jurisdictions"]}) == 13
    assert coverage["jurisdiction_count"] == 13
    assert coverage["cell_count"] == 65
    assert coverage["component_count"] == 5
    assert coverage["not_decision_grade"] is True

    assert shares["direct_reported"]["share"] == pytest.approx(0.17924672)
    assert shares["proxy"]["share"] == pytest.approx(0.13440737)
    assert shares["donor"]["share"] == pytest.approx(0.68634592)
    assert shares["assumption"]["share"] == pytest.approx(0.0)
    assert shares["direct_reported"]["pct"] == pytest.approx(17.9247, abs=1e-4)
    assert shares["proxy"]["pct"] == pytest.approx(13.4407, abs=1e-4)
    assert shares["donor"]["pct"] == pytest.approx(68.6346, abs=1e-4)
    assert coverage["direct_cell_count"] == 3
    assert coverage["proxy_cell_count"] == 2
    assert coverage["donor_cell_count"] == 60
    assert coverage["assumption_cell_count"] == 0
    assert coverage["direct_jurisdiction_count"] == 2
    assert coverage["direct_jurisdictions"] == ["BC", "QC"]
    assert coverage["donor_only_count"] == 11
    assert coverage["donor_price_count"] == 12
    assert "not decision-grade" in coverage["warning"].lower()

    diagnostic = payload["national_diagnostic"]
    assert diagnostic["display_role"] == "diagnostic_not_headline"
    assert diagnostic["display_position"] == (
        "after_evidence_coverage_and_province_ledgers"
    )
    assert diagnostic["not_decision_grade"] is True
    assert diagnostic["ranking_performed"] is False
    assert diagnostic["donor_flat"]["scenario_role"] == "base"
    assert diagnostic["stumpage_scaled"]["scenario_role"] == "sensitivity"
    assert diagnostic["donor_flat"]["base_cad_m3"] == pytest.approx(33.1365)
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] == pytest.approx(-16.1220)


def test_province_rows_expose_the_evidence_first_columns() -> None:
    payload = _load_national()
    summary = {
        (row["price_scenario"], row["province"]): row
        for row in _read_csv(SOURCE / "national_margin_summary.csv")
        if row["row_type"] == "province"
    }
    coverage = payload["evidence_coverage"]
    rows = payload["jurisdictions"]

    assert [row["province"] for row in rows] == payload["coverage"]["provinces"]
    assert [row["province"] for row in rows] == [
        "BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "NL", "PE", "YT", "NT", "NU"
    ]
    assert coverage["donor_only_jurisdictions"] == [
        row["province"] for row in rows if row["donor_only"]
    ]

    for row in rows:
        assert row["price_tier"] in {
            "reported_average", "donor_proxy", "stumpage_scaled_proxy"
        }
        assert row["cost_tier"]
        assert row["price_source"]["title"]
        assert isinstance(row["price_source"]["is_direct"], bool)
        assert row["price_source"]["is_direct"] is (
            row["price_tier"] == "reported_average"
        )
        assert row["stumpage_reference"]["is_delivered_price"] is False
        assert row["replacement"]["priority"] in {"P1", "P2", "P3", "P4"}
        assert row["ranked"] is False
        assert "rank" not in row
        assert row["donor_flat"]["price_tier"] == row["price_tier"]
        assert row["donor_flat"]["cost_tier"] == row["cost_tier"]
        for scenario in ("donor_flat", "stumpage_scaled"):
            source = summary[(scenario, row["province"])]
            assert row[scenario]["low_cad_m3"] == pytest.approx(
                float(source["margin_low_cad_m3"])
            )
            assert row[scenario]["base_cad_m3"] == pytest.approx(
                float(source["margin_base_cad_m3"])
            )
            assert row[scenario]["high_cad_m3"] == pytest.approx(
                float(source["margin_high_cad_m3"])
            )

    bc = next(row for row in rows if row["province"] == "BC")
    assert bc["price_source"]["is_direct"] is True
    assert bc["price_tier"] == "reported_average"
    assert bc["donor_only"] is False
    assert bc["status_label"] == "partial direct"
    assert bc["evidence_mix"]["reported_average"] == 2
    assert bc["evidence_mix"]["proxy_model"] == 2
    assert bc["evidence_mix"]["donor_proxy"] == 1

    for row in rows:
        if row["province"] == "BC":
            continue
        assert row["price_source"]["is_direct"] is False
        assert row["price_source"]["donor_province"] == "BC"
        if row["province"] != "QC":
            assert row["donor_only"] is True
            assert row["status_label"] == "provisional donor"
            assert all(
                value == 0
                for tier, value in row["evidence_mix"].items()
                if tier in {"reported_average", "proxy_model"}
            )

    qc = next(row for row in rows if row["province"] == "QC")
    assert qc["donor_only"] is False
    assert qc["price_source"]["is_direct"] is False
    assert qc["evidence_mix"]["reported_average"] == 1
    assert qc["status_label"] == "partial direct"


def test_stumpage_references_are_8_rates_and_5_explicit_fallbacks() -> None:
    payload = _load_national()
    audit = payload["stumpage_references"]
    assert audit["rate_count"] == 8
    assert audit["fallback_count"] == 5
    assert audit["rate_provinces"] == ["BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS"]
    assert audit["fallback_provinces"] == ["NL", "PE", "YT", "NT", "NU"]
    assert audit["rates_are_charges_not_delivered_prices"] is True

    source = {row["province"]: row for row in _read_csv(STUMPAGE_SOURCE)}
    assert len(source) == 13
    has_rate = 0
    fallback = 0
    for row in payload["jurisdictions"]:
        reference = row["stumpage_reference"]
        raw = source[row["province"]]
        if raw["is_stumpage_or_dues_rate"] == "true":
            has_rate += 1
            assert reference["has_rate"] is True
            assert reference["rate_low_cad_m3"] == pytest.approx(
                float(raw["rate_low_cad_m3"])
            )
            assert reference["rate_base_cad_m3"] == pytest.approx(
                float(raw["rate_base_cad_m3"])
            )
            assert reference["rate_high_cad_m3"] == pytest.approx(
                float(raw["rate_high_cad_m3"])
            )
            assert reference["year"] == raw["source_publication_date"]
            assert reference["source_title"] == raw["source_title"]
            assert reference["rate_type"] == raw["rate_type"]
            assert reference["is_delivered_price"] is False
            assert reference["is_observed_revenue"] is False
        else:
            fallback += 1
            assert reference["has_rate"] is False
            assert reference["rate_base_cad_m3"] is None
            assert reference["fallback_donor"] == "BC"
            assert reference["source_title"] == ""
            assert raw["fallback_donor"] == "BC"
    assert (has_rate, fallback) == (8, 5)

    bc = next(row for row in payload["jurisdictions"] if row["province"] == "BC")
    assert bc["stumpage_reference"]["rate_base_cad_m3"] == pytest.approx(34.325)
    ab = next(row for row in payload["jurisdictions"] if row["province"] == "AB")
    assert ab["stumpage_reference"]["rate_base_cad_m3"] == pytest.approx(5.53)
    assert ab["stumpage_reference"]["year"] == "2026-11"
    sk = next(row for row in payload["jurisdictions"] if row["province"] == "SK")
    assert sk["stumpage_reference"]["rate_base_cad_m3"] == pytest.approx(15.65)
    nu = next(row for row in payload["jurisdictions"] if row["province"] == "NU")
    assert nu["stumpage_reference"]["has_rate"] is False
    assert nu["stumpage_reference"]["fallback_donor"] == "BC"


def test_replacement_priority_matches_the_queue_and_does_not_rank_margins() -> None:
    payload = _load_national()
    queue_rows = _read_csv(QUEUE_SOURCE)
    by_province: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in queue_rows:
        by_province[row["province"]].append(row)
    assert len(queue_rows) == 60
    assert len(by_province) == 13

    for row in payload["jurisdictions"]:
        province = row["province"]
        expected = by_province[province]
        expected_priority = min(
            (item["priority"] for item in expected),
            key=lambda value: int(value[1:]),
        )
        assert row["replacement"]["priority"] == expected_priority
        assert row["replacement"]["open_cell_count"] == len(expected)
        assert len(row["replacement"]["cells"]) == len(expected)
        assert row["replacement"]["queue_status"] == (
            "open_required_source_not_invented"
        )

    assert payload["ranking"] == {
        "performed": False,
        "ordering": "canonical_jurisdiction_order",
        "donor_only_excluded": True,
        "reason": (
            "Donor-only jurisdictions are provisional evidence rows. The UI "
            "does not sort or rank margins across jurisdictions."
        ),
    }
    assert payload["replacement_queue"]["row_count"] == 60
    assert payload["replacement_queue"]["priority_counts"] == {
        "P1": 15, "P2": 15, "P3": 25, "P4": 5
    }
    assert payload["labels"]["ranking_performed"] is False
    assert payload["national_diagnostic"]["ranking_performed"] is False

    # The table order is canonical, not margin-sorted.
    margins = [row["donor_flat"]["base_cad_m3"] for row in payload["jurisdictions"]]
    assert margins != sorted(margins)
    assert margins != sorted(margins, reverse=True)


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

    allocation = {
        row["province"]: float(row["harvest_m3"])
        for row in _read_csv(ALLOCATION_SOURCE)
    }
    for jurisdiction in payload["jurisdictions"]:
        assert jurisdiction["allocation_volume_m3"] == pytest.approx(
            allocation[jurisdiction["province"]], abs=0.1
        )
    assert payload["national_diagnostic"]["allocation_volume_m3"] == pytest.approx(
        118_977_339.1
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


def test_labels_are_explicit_and_never_claim_observation_or_policy_grade() -> None:
    payload = _load_national()
    labels = payload["labels"]
    assert labels["evidence"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["not_decision_grade"] is True
    assert labels["ranking_performed"] is False
    assert labels["combined_national_net_value"] is False
    assert labels["national_diagnostic_label"] == "model_estimate_not_policy"
    assert labels["forest_lp_input"] is False
    assert labels["public_osecan_input"] is False
    assert payload["national_diagnostic"]["label"] == "model_estimate_not_policy"
    assert payload["national_diagnostic"]["not_decision_grade"] is True

    text = json.dumps(payload)
    assert '"observed": true' not in text
    assert '"policy_grade": true' not in text
    assert '"combined_national_net_value": true' not in text
    assert '"stumpage_scaled_preferred"' not in text
    assert '"stumpage_scaled_observed": true' not in text
    assert '"stumpage_scaled_policy_grade": true' not in text
    assert "preferred" not in text.lower()


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
    assert manifest["schema_version"] == "economics/national-manifest/2"
    validation = manifest["validation"]
    assert validation["ok"] is True
    assert validation["default_view"] == "evidence_coverage"
    assert validation["jurisdiction_count"] == 13
    assert validation["direct_reported_share"] == pytest.approx(0.17924672)
    assert validation["proxy_share"] == pytest.approx(0.13440737)
    assert validation["donor_share"] == pytest.approx(0.68634592)
    assert validation["direct_cell_count"] == 3
    assert validation["proxy_cell_count"] == 2
    assert validation["donor_cell_count"] == 60
    assert validation["direct_jurisdiction_count"] == 2
    assert validation["donor_only_count"] == 11
    assert validation["donor_price_count"] == 12
    assert validation["stumpage_rate_count"] == 8
    assert validation["stumpage_fallback_count"] == 5
    assert validation["replacement_queue_rows"] == 60
    assert validation["ranking_performed"] is False
    assert validation["not_decision_grade"] is True
    assert validation["observed_claim"] is False
    assert validation["policy_grade_claim"] is False
    assert validation["combined_national_net_value"] is False
    assert validation["forest_lp_input"] is False
    assert validation["public_osecan_input"] is False

    generated = manifest["generated"][0]
    assert generated == manifest["files"][0]
    assert generated["path"] == "economics/national.json"
    assert generated["bytes"] == (BUNDLE / "national.json").stat().st_size
    assert generated["sha256"] == _sha256(BUNDLE / "national.json")

    expected_sources = {
        "data/derived/national/harvest_basis/national_economics_v2_1/national_margin_estimates.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/national_margin_summary.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/scenario_comparison.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/tier_coverage.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/summary.json",
        "data/derived/national/harvest_basis/national_tier_coverage/national_tier_coverage.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/assumptions_donor_register.csv",
        "data/derived/national/harvest_basis/national_economics_v2_1/sources.csv",
        "data/derived/national/harvest_basis/national_economics_v2/provincial_stumpage_references.csv",
        "data/derived/national/harvest_basis/national_tier_coverage/source_replacement_queue.csv",
        "data/derived/national/harvest_basis/provincial_allocation/119m.csv",
        "data/derived/national/harvest_basis/national_tier_coverage/national_tier_summary.json",
    }
    assert {item["path"] for item in manifest["sources"]} == expected_sources
    for item in manifest["sources"]:
        path = ROOT / item["path"]
        assert item["bytes"] == path.stat().st_size
        assert item["sha256"] == _sha256(path)
        assert len(item["sha256"]) == 64


def test_ui_panel_is_evidence_first_and_read_only() -> None:
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    panel = html.split('id="panel-economics"', 1)[1].split(
        'id="panel-osecan"', 1
    )[0]
    national = panel.split('id="national-economics"', 1)[1]

    assert 'id="national-economics"' in panel
    assert 'id="national-warning"' in national
    assert 'id="national-coverage"' in national
    assert 'id="national-table"' in national
    assert 'id="national-drilldown"' in national
    assert 'id="national-margin-diagnostic"' in national
    assert 'id="national-downloads"' in national
    assert national.count("<button") == 0
    assert national.count("<form") == 0
    assert national.count("<select") == 0
    assert national.count("<a ") == 0  # links are rendered from validated JSON
    assert "national-scenario" not in national
    for forbidden in (
        "osecan-queue",
        "osecan-harvest",
        "run-mode",
        "forest-lp",
        "edit-",
    ):
        assert forbidden not in national

    assert national.index('id="national-coverage"') < national.index(
        'id="national-table"'
    )
    assert national.index('id="national-table"') < national.index(
        'id="national-drilldown"'
    )
    assert national.index('id="national-drilldown"') < national.index(
        'id="national-margin-diagnostic"'
    )
    assert "not decision-grade" in national.lower()
    assert "provisional donor" in national.lower()

    assert "loadJSON('economics/national.json')" in app_js
    assert "loadJSON('economics/national.manifest.json')" in app_js
    assert "function validateNational" in app_js
    assert "function renderNational" in app_js
    assert "function renderNationalCoverage" in app_js
    assert "function renderNationalTable" in app_js
    assert "function renderNationalDrilldown" in app_js
    assert "function renderNationalDiagnostic" in app_js
    assert "not decision-grade" in app_js.lower()
    assert "provisional donor" in app_js.lower()
    assert "no combined national net value" in app_js
    assert "not Forest LP/OSeCAN input" in app_js
    assert "observed=false" in app_js
    assert "policy_grade=false" in app_js
    assert "ranking.performed === false" in app_js
    assert "national-chart" not in app_js
    assert "national-scenario" not in app_js

    render_block = app_js.split("function renderNational()", 1)[1].split(
        "/* ---------------- Inputs ---------------- */", 1
    )[0]
    assert render_block.index("renderNationalCoverage()") < render_block.index(
        "renderNationalTable()"
    )
    assert render_block.index("renderNationalTable()") < render_block.index(
        "renderNationalDrilldown()"
    )
    assert render_block.index("renderNationalDrilldown()") < render_block.index(
        "renderNationalDiagnostic()"
    )


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
    assert payload["default_view"] == "evidence_coverage"
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
