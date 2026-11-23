#!/usr/bin/env python3
"""Build a static, read-only UI publication bundle from the live ui/ app.

The bundle is a byte-for-byte copy of every app asset, vendor library, and data
payload except index.html, which gains exactly one script tag for the static
publication guard. The guard is written for the release and loaded before
api.js/app.js, and it rejects every same-origin /api request.

The build is fail-closed: it refuses to overwrite an existing destination,
refuses a git worktree containing deleted tracked files, and rejects a source
index.html whose script order does not match the known publication anchor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

APP_FILES = ("index.html", "app.js", "api.js", "styles.css")
GUARD_FILE = "publication-mode.js"
VERSIONED_ASSETS = ("app.js", "styles.css")
MANIFEST_FILE = "manifest.json"
PUBLICATION_FILE = "PUBLICATION.json"
DATA_MANIFEST = Path("public/data/manifest.json")
ECONOMICS_MANIFEST = Path("public/data/economics/manifest.json")
NATIONAL_MANIFEST = Path("public/data/economics/national.manifest.json")
NATIONAL_FILE = Path("public/data/economics/national.json")
FACILITY_MANIFEST = Path("public/data/facilities/manifest.json")
FACILITY_FILE = Path("public/data/facilities/facilities.json")
FACILITY_SCENARIOS = ("100m", "119m", "150m")
ECONOMICS_FILES = (
    "economics/summary.json",
    "economics/detail.csv",
    "economics/model_inputs.zip",
)


def fail(message: str) -> None:
    raise SystemExit("build_static_publication: " + message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(root: Path, relative: Path) -> dict:
    path = root / relative
    if not path.is_file():
        fail(f"missing payload file: {relative}")
    return {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def bundle_hash(root: Path, records: list[dict]) -> str:
    lines = sorted(f"{item['sha256']}  {item['path']}" for item in records)
    return sha256_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path}: {error}")


def verify_manifest(root: Path, manifest_path: Path) -> tuple[dict, list[dict]]:
    payload = load_json(manifest_path)
    records = []
    for item in payload["files"]:
        record = file_record(root, Path(item["path"]))
        if record["bytes"] != item["bytes"] or record["sha256"] != item["sha256"]:
            fail(f"manifest mismatch for {item['path']}")
        records.append(record)
    return payload, records


def verify_generated_payload(root: Path, manifest_path: Path) -> tuple[dict, dict]:
    payload = load_json(manifest_path)
    generated = payload.get("generated")
    if not isinstance(generated, list) or len(generated) != 1:
        fail(f"{manifest_path.name} must declare exactly one generated payload")
    record = file_record(root, Path(generated[0]["path"]))
    if record["bytes"] != generated[0].get("bytes") or record["sha256"] != generated[0].get("sha256"):
        fail(f"manifest mismatch for {generated[0]['path']}")
    return payload, record


def git_status(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def assert_no_unexpected_deletions(repo: Path) -> None:
    deletions = [line for line in git_status(repo) if line[:2].strip().startswith("D")]
    if deletions:
        fail("unexpected deleted tracked file(s): " + "; ".join(deletions))


def publication_guard(release: str, tag: str, commit: str) -> str:
    return f"""/* Forest Ledger {release} - static publication guard.
   This copy is a static demonstration. It must never reach a live runner, even
   if someone serves it behind an API-capable origin. All /api requests fail
   locally so the app selects its "Static mode - no backend" path. */

(function () {{
  'use strict';

  window.CLEWS_PUBLICATION = {{
    static_only: true,
    release: '{release}',
    tag: '{tag}',
    commit: '{commit}'
  }};

  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!nativeFetch) return;

  function isLiveApi(input) {{
    var raw = typeof input === 'string' ? input : (input && input.url) || '';
    if (!raw) return false;
    try {{
      var url = new URL(raw, window.location.href);
      return url.origin === window.location.origin && /^\\/api(?:\\/|$)/.test(url.pathname);
    }} catch (error) {{
      return false;
    }}
  }}

  window.fetch = function (input, init) {{
    if (isLiveApi(input)) {{
      return Promise.reject(new TypeError('static publication: live API disabled'));
    }}
    return nativeFetch(input, init);
  }};
}}());
"""


def asset_version(path: Path) -> str:
    return sha256_file(path)[:12]


def asset_reference(source_index: str, name: str) -> str | None:
    pattern = re.compile(r'(?:src|href)="' + re.escape(name) + r'(?:\?v=([0-9a-f]{12}))?"')
    matches = pattern.findall(source_index)
    if len(matches) != 1:
        fail(f"index.html must reference {name} exactly once")
    return matches[0] or None


def build_index(source_index: str, guard_hash: str, source: Path) -> str:
    for name in VERSIONED_ASSETS:
        version = asset_reference(source_index, name)
        expected = asset_version(source / name)
        if version != expected:
            fail(f"stale cache key for {name}: index has {version or 'none'}, expected {expected}")

    guard_version = asset_reference(source_index, GUARD_FILE)
    if guard_version is None:
        fail(f"{GUARD_FILE} must carry a content-hash query in index.html")

    guard_at = source_index.find(f'src="{GUARD_FILE}')
    api_at = source_index.find('src="api.js"')
    app_at = source_index.find('src="app.js')
    if not (0 <= guard_at < api_at < app_at):
        fail(f"{GUARD_FILE} must load before api.js and app.js")

    guard_tag = re.compile(r'(src="' + re.escape(GUARD_FILE) + r')\?v=[0-9a-f]{12}"')
    built = guard_tag.sub(r'\1?v=' + guard_hash + '"', source_index, count=1)
    if built == source_index:
        fail(f"failed to version {GUARD_FILE} with its generated content hash")
    return built


def copy_payload(source: Path, destination: Path) -> None:
    for name in APP_FILES:
        shutil.copy2(source / name, destination / name)
    shutil.copytree(source / "vendor", destination / "vendor")
    shutil.copytree(source / "public", destination / "public")


def verify_copied_bytes(source: Path, destination: Path) -> None:
    for name in APP_FILES:
        if name == "index.html":
            continue
        if sha256_file(source / name) != sha256_file(destination / name):
            fail(f"copy differs from source: {name}")
    for source_file in sorted((source / "vendor").rglob("*")):
        if source_file.is_file():
            copied = destination / source_file.relative_to(source)
            if sha256_file(source_file) != sha256_file(copied):
                fail(f"copy differs from source: {source_file.relative_to(source)}")
    for source_file in sorted((source / "public").rglob("*")):
        if source_file.is_file():
            copied = destination / source_file.relative_to(source)
            if sha256_file(source_file) != sha256_file(copied):
                fail(f"copy differs from source: {source_file.relative_to(source)}")


def payload_manifest(destination: Path) -> dict:
    excluded = {MANIFEST_FILE, PUBLICATION_FILE}
    relative_paths = sorted(
        path.relative_to(destination)
        for path in destination.rglob("*")
        if path.is_file() and path.relative_to(destination).as_posix() not in excluded
    )
    records = [file_record(destination, relative) for relative in relative_paths]
    return {
        "schema_version": "clews.ui_payload.v1",
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "files": records,
        "total_bytes": sum(item["bytes"] for item in records),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source", type=Path, default=Path("ui"))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    source = (repo / args.source).resolve()
    destination = (repo / args.destination).resolve()

    if destination.exists():
        fail(f"destination already exists: {destination}")
    if source == destination or destination in source.parents:
        fail("destination must not replace or contain the source app")
    if not (source / "index.html").is_file() or not (source / "public/data").is_dir():
        fail(f"source is not a complete ui app: {source}")

    assert_no_unexpected_deletions(repo)

    destination.mkdir(parents=True)
    copy_payload(source, destination)

    guard_content = publication_guard(args.release, args.tag, args.source_commit)
    guard_hash = sha256_bytes(guard_content.encode("utf-8"))[:12]
    index_source = (source / "index.html").read_text(encoding="utf-8")
    (destination / "index.html").write_text(build_index(index_source, guard_hash, source), encoding="utf-8")
    (destination / GUARD_FILE).write_text(guard_content, encoding="utf-8")
    verify_copied_bytes(source, destination)

    data_manifest, data_records = verify_manifest(destination / "public/data", destination / DATA_MANIFEST)
    economics_manifest, economics_records = verify_manifest(
        destination / "public/data", destination / ECONOMICS_MANIFEST
    )
    economics_paths = {record["path"] for record in economics_records}
    if not set(ECONOMICS_FILES).issubset(economics_paths):
        fail("economics manifest does not cover summary.json, detail.csv, and model_inputs.zip")

    national_manifest, national_record = verify_generated_payload(
        destination / "public/data", destination / NATIONAL_MANIFEST
    )
    national = load_json(destination / NATIONAL_FILE)
    diagnostic = national["national_diagnostic"]
    if national.get("schema_version") != "economics/national/3":
        fail("national economics schema mismatch")
    if national.get("model_id") != "national_economics_v2_3":
        fail("national economics model id mismatch")
    if national.get("artifact_version") != "2.3.0":
        fail("national economics artifact version mismatch")
    if national["coverage"]["jurisdiction_rows"] != 13 or len(national["jurisdictions"]) != 13:
        fail("national economics must cover 13 jurisdictions")
    shares = national["evidence_coverage"]["volume_weighted"]
    if abs(shares["direct_reported"]["share"] - 0.33601842) > 5e-9:
        fail("national direct/reported price share mismatch")
    if abs(shares["donor"]["share"] - 0.66398158) > 5e-9:
        fail("national donor price share mismatch")
    cost_shares = national["evidence_coverage"]["cost_evidence"]["volume_weighted"]
    if abs(cost_shares["component_stack"]["share"] - 0.54477285) > 5e-9:
        fail("national component-stack cost share mismatch")
    if abs(cost_shares["industry_aggregate_proxy"]["share"] - 0.45522715) > 5e-9:
        fail("national aggregate-proxy cost share mismatch")
    if cost_shares["donor_fallback"]["jurisdictions"] != ["NU"]:
        fail("national donor-fallback cost must be NU only")
    if abs(diagnostic["donor_flat"]["base_cad_m3"] - 43.0187) > 1e-6:
        fail("national base margin mismatch")
    if abs(diagnostic["stumpage_scaled"]["base_cad_m3"] + 5.7444) > 1e-6:
        fail("national sensitivity margin mismatch")
    national_labels = national["labels"]
    if national_labels.get("observed") is not False or national_labels.get("policy_grade") is not False:
        fail("national economics labels must stay model-only")
    if national_labels.get("combined_national_net_value") is not False:
        fail("national economics must not claim a combined national net value")
    if national_labels.get("forest_lp_input") is not False or national_labels.get("public_osecan_input") is not False:
        fail("national economics must stay outside the Forest LP and public OSeCAN")

    net_value = national.get("forest_lp_net_value_sensitivity")
    if not isinstance(net_value, dict):
        fail("national economics must include the Forest LP net-value sensitivity")
    if net_value.get("model_id") != "forest_lp_net_value_sensitivity_v1":
        fail("Forest LP net-value sensitivity model id mismatch")
    net_labels = net_value.get("labels") or {}
    if net_labels.get("observed") is not False or net_labels.get("policy_grade") is not False:
        fail("Forest LP net-value sensitivity labels must stay model-only")
    if net_labels.get("lp_objective") != "not_used":
        fail("Forest LP net-value sensitivity must not use the LP objective")
    if net_labels.get("forest_lp_input") is not False or net_labels.get("public_osecan_input") is not False:
        fail("Forest LP net-value sensitivity must stay outside the LP and public OSeCAN")
    net_coverage = net_value.get("coverage") or {}
    if net_coverage.get("allocation_rows") != 117 or net_coverage.get("group_rows") != 18:
        fail("Forest LP net-value sensitivity coverage mismatch")
    net_rows = net_value.get("rows") or []
    documented = [row for row in net_rows if row.get("cap_case") == "documented_ceiling"]
    infeasible = [row for row in net_rows if row.get("feasible") is False]
    if len(net_rows) != 18 or len(documented) != 6 or len(infeasible) != 4:
        fail("Forest LP net-value sensitivity row counts mismatch")
    for row in infeasible:
        if row.get("net_value_low_m_cad") is not None or row.get("net_value_base_m_cad") is not None:
            fail("infeasible Forest LP net-value rows must stay blank, not zero")
        if row.get("allocation_case") != "150m":
            fail("only the 150m intensity rows may be infeasible")
    if {row["allocation_case"] for row in documented} != {"100m", "119m", "150m"}:
        fail("Forest LP net-value documented cases must be 100/119/150m")
    if {row["scenario_role"] for row in documented} != {"base", "sensitivity"}:
        fail("Forest LP net-value documented scenarios must be base/sensitivity")

    allocation_lp = net_value.get("economic_allocation_sensitivity")
    if not isinstance(allocation_lp, dict):
        fail("Forest LP net-value section must include the allocation LP sensitivity")
    if allocation_lp.get("model_id") != "provincial_allocation_net_value_lp_v1":
        fail("allocation LP net-value model id mismatch")
    if allocation_lp.get("schema_version") != (
        "national/provincial-allocation-net-value-lp/1"
    ):
        fail("allocation LP net-value schema mismatch")
    allocation_labels = allocation_lp.get("labels") or {}
    if allocation_labels.get("evidence") != "model_estimate":
        fail("allocation LP net-value must stay a model estimate")
    if allocation_labels.get("lp_objective") != "net_value_sensitivity":
        fail("allocation LP net-value objective label mismatch")
    if allocation_labels.get("canonical_changed") is not False:
        fail("allocation LP net-value must not change the canonical allocation")
    if allocation_labels.get("observed") is not False or allocation_labels.get(
        "policy_grade"
    ) is not False:
        fail("allocation LP net-value labels must stay model-only")
    if allocation_labels.get("public_osecan_input") is not False:
        fail("allocation LP net-value must stay outside public OSeCAN")
    if not isinstance(allocation_lp.get("donor_price_caveat"), str):
        fail("allocation LP net-value donor-price caveat is missing")
    allocation_coverage = allocation_lp.get("coverage") or {}
    if allocation_coverage.get("documented_ceiling_rows") != 6:
        fail("allocation LP net-value documented row count mismatch")
    if allocation_coverage.get("province_shift_rows") != 13:
        fail("allocation LP net-value province shift count mismatch")
    if allocation_coverage.get("province_shift_level") != "119m":
        fail("allocation LP net-value province shifts must use 119m")
    allocation_rows = allocation_lp.get("rows") or []
    allocation_shifts = allocation_lp.get("province_shifts") or []
    if len(allocation_rows) != 6 or len(allocation_shifts) != 13:
        fail("allocation LP net-value rows or province shifts are incomplete")
    for row in allocation_rows:
        if row.get("level") not in {"100m", "119m", "150m"}:
            fail("allocation LP net-value unexpected level")
        if row.get("scenario") not in {"donor_flat", "stumpage_scaled"}:
            fail("allocation LP net-value unexpected scenario")
        values = (
            row.get("baseline_m_cad"),
            row.get("optimized_m_cad"),
            row.get("delta_m_cad"),
        )
        if not all(isinstance(value, (int, float)) for value in values):
            fail("allocation LP net-value row is blank")
        if abs(row["delta_m_cad"] - (row["optimized_m_cad"] - row["baseline_m_cad"])) > 1e-3:
            fail("allocation LP net-value delta does not reconcile")

    break_even_fields = (
        "break_even_cad_m3",
        "donor_margin",
        "scaled_margin",
        "donor_gap",
        "scaled_gap",
        "classification",
        "price_sensitivity_rank",
    )
    break_even_rows = [
        row
        for row in national["jurisdictions"]
        if all(field in row for field in break_even_fields)
    ]
    if len(break_even_rows) != 13:
        fail("national economics must carry break-even fields for 13 provinces")
    if {row["price_sensitivity_rank"] for row in break_even_rows} != set(range(1, 14)):
        fail("national break-even price-sensitivity ranks must cover 1..13")
    for row in break_even_rows:
        if row["classification"] not in {
            "positive_margin",
            "negative_margin",
            "price_limited",
            "cost_limited",
        }:
            fail("national break-even classification is unknown")
        if abs(row["donor_gap"] + row["donor_margin"]) > 1e-4:
            fail("national break-even donor gap is not the negated margin")
        if abs(row["scaled_gap"] + row["scaled_margin"]) > 1e-4:
            fail("national break-even scaled gap is not the negated margin")

    facility_manifest, facility_record = verify_generated_payload(
        destination / "public/data", destination / FACILITY_MANIFEST
    )
    facilities = load_json(destination / FACILITY_FILE)
    if facilities.get("schema_version") != "facilities/1":
        fail("facilities schema mismatch")
    if facilities.get("artifact") != "ui_facilities_v1":
        fail("facilities artifact mismatch")
    facility_coverage = facilities.get("coverage") or {}
    facility_rows = facilities.get("facilities") or []
    if facility_coverage.get("record_count") != 689 or len(facility_rows) != 689:
        fail("facilities must cover 689 records")
    if facility_coverage.get("mapped_count") != 608:
        fail("facilities must map 608 records")
    if facility_coverage.get("unlocated_count") != 81:
        fail("facilities must leave 81 records unlocated")
    mapped_rows = [row for row in facility_rows if row.get("mapped")]
    if len(mapped_rows) != 608:
        fail("facility mapped flags do not sum to 608")
    if any(row.get("lat") is None or row.get("lon") is None for row in mapped_rows):
        fail("mapped facility is missing coordinates")
    facility_labels = facilities.get("labels") or {}
    if facility_labels.get("evidence") != "model_estimate":
        fail("facilities must stay a model estimate")
    if any(facility_labels.get(key) is not False for key in ("observed", "policy_grade", "official_osecan")):
        fail("facility labels must stay model-only and outside official OSeCAN")
    if facility_labels.get("ranking") != "none" or facility_labels.get("read_only") is not True:
        fail("facilities must stay unranked and read-only")
    facility_scenarios = facilities.get("scenarios") or []
    if [scenario.get("id") for scenario in facility_scenarios] != list(FACILITY_SCENARIOS):
        fail("facility scenarios must be 100m/119m/150m")
    if facilities.get("default_scenario") != "119m":
        fail("facility default scenario must be 119m")
    if facility_manifest.get("validation", {}).get("ok") is not True:
        fail("facility manifest validation is not ok")
    if facility_manifest.get("labels") != facility_labels:
        fail("facility manifest labels diverge from the payload")

    national_chain = facilities.get("national_chain") or {}
    if national_chain.get("artifact") != "national_facility_chain_scenarios_v7":
        fail("facilities national chain artifact mismatch")
    if national_chain.get("artifact_version") != "7.0.0":
        fail("facilities national chain artifact version mismatch")
    chain_labels = national_chain.get("labels") or {}
    if chain_labels != {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }:
        fail("national chain labels must stay model-only and unclaimed")
    chain_rows = national_chain.get("rows") or []
    if [row.get("id") for row in chain_rows] != ["100m", "119m", "150m"]:
        fail("national chain rows must be 100m/119m/150m")
    chain_columns = national_chain.get("columns") or []
    if len(chain_columns) != 6:
        fail("national chain must carry six summary columns")
    if any(column.get("field") not in chain_rows[0] for column in chain_columns):
        fail("national chain column references a missing field")
    chain_fields = (
        "harvest_m3",
        "solid_m3",
        "pulp_odt",
        "process_pj",
        "harvest_gross_pj",
        "harvest_recoverable_pj",
        "total_gross_pj",
    )
    for row in chain_rows:
        if any(not isinstance(row.get(field), (int, float)) or row[field] < 0 for field in chain_fields):
            fail(f"national chain {row.get('id')} has an invalid numeric field")
        if abs(row["total_gross_pj"] - (row["process_pj"] + row["harvest_gross_pj"])) > 1e-6:
            fail(f"national chain total gross does not conserve for {row['id']}")
    expected_chain = {
        "100m": (31.693, 10.137, 419.238, 373.244, 223.946, 792.482),
        "119m": (37.674, 12.066, 498.751, 444.411, 266.646, 943.162),
        "150m": (46.137, 15.551, 626.865, 573.125, 343.875, 1199.991),
    }
    for row in chain_rows:
        rounded = (
            round(row["solid_m3"] / 1e6, 3),
            round(row["pulp_odt"] / 1e6, 3),
            round(row["process_pj"], 3),
            round(row["harvest_gross_pj"], 3),
            round(row["harvest_recoverable_pj"], 3),
            round(row["total_gross_pj"], 3),
        )
        if rounded != expected_chain[row["id"]]:
            fail(f"national chain values changed for {row['id']}")
    chain_reference = national_chain.get("reference") or {}
    if chain_reference.get("scenario") != "119m":
        fail("national chain reference scenario must be 119m")
    if chain_reference.get("process_availability_fraction") != 0.5:
        fail("national chain process availability reference must be 0.50")
    if chain_reference.get("harvest_recovery_fraction") != 0.6:
        fail("national chain harvest recovery reference must be 0.60")
    if [tier.get("stream") for tier in chain_reference.get("tiers") or []] != ["process", "harvest"]:
        fail("national chain reference badges must cover process and harvest")
    chain_provenance = national_chain.get("coefficient_provenance") or {}
    if chain_provenance.get("donor_product_coefficients") != 0:
        fail("national chain must not apply donor product coefficients")
    if (chain_provenance.get("e1") or {}).get("count") != 4:
        fail("national chain E1 province count mismatch")
    if (chain_provenance.get("e2c") or {}).get("count") != 9:
        fail("national chain E2c province count mismatch")

    scenarios = load_json(destination / "public/data/scenarios.json")
    families = {scenario["family"] for scenario in scenarios["scenarios"]}
    pareto_eligible = sum(1 for scenario in scenarios["scenarios"] if scenario.get("pareto_eligible"))
    economics = load_json(destination / "public/data/economics/summary.json")
    headline = economics["headline"]
    qc_reference = economics["qc_reference"]

    manifest = payload_manifest(destination)
    manifest_path = destination / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    artifacts = {}
    for name in ("index.html", "app.js", "api.js", "styles.css", GUARD_FILE):
        record = file_record(destination, Path(name))
        artifacts[name.replace(".", "_").replace("-", "_")] = record
    for name, relative in (
        ("data_manifest", DATA_MANIFEST),
        ("economics_manifest", ECONOMICS_MANIFEST),
        ("national_manifest", NATIONAL_MANIFEST),
        ("national_json", NATIONAL_FILE),
    ):
        artifacts[name] = file_record(destination, relative)

    publication = {
        "schema_version": "clews.ui_publication.v1",
        "release": {
            "label": args.label,
            "tag": args.tag,
            "tag_type": "annotated",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source_branch": args.source_branch,
            "source_commit": args.source_commit,
        },
        "publication": {
            "path": destination.relative_to(repo).as_posix(),
            "type": "static_demo",
            "static_only": True,
            "backend": "none",
            "live_solver": False,
            "official_c2070": False,
            "energy_policy_advice": False,
            "entrypoint": "index.html",
            "publication_guard": GUARD_FILE,
        },
        "counts": {
            "scenarios": len(scenarios["scenarios"]),
            "families": len(families),
            "pareto_eligible": pareto_eligible,
            "dispatch_outputs": scenarios.get("output_count", len(scenarios["scenarios"])),
            "data_manifest_files": len(data_records),
            "data_directory_files": sum(1 for path in (destination / "public/data").rglob("*") if path.is_file()),
            "economics_rows": headline["rows"],
            "national_jurisdictions": len(national["jurisdictions"]),
            "facilities": facility_coverage["record_count"],
            "facilities_mapped": facility_coverage["mapped_count"],
            "facility_scenarios": len(facility_scenarios),
            "national_chain_rows": len(chain_rows),
            "national_base_margin_cad_m3": diagnostic["donor_flat"]["base_cad_m3"],
            "national_sensitivity_margin_cad_m3": diagnostic["stumpage_scaled"]["base_cad_m3"],
            "forest_lp_net_value_allocation_rows": net_coverage["allocation_rows"],
            "forest_lp_net_value_groups": net_coverage["group_rows"],
            "forest_lp_net_value_documented_rows": len(documented),
            "forest_lp_net_value_infeasible_rows": len(infeasible),
            "allocation_net_value_lp_rows": len(allocation_rows),
            "allocation_net_value_lp_province_shifts": len(allocation_shifts),
            "province_break_even_rows": len(break_even_rows),
            "payload_manifest_files": len(manifest["files"]),
        },
        "economics": {
            "headline": headline,
            "qc_reference": {
                "reference_id": qc_reference["reference_id"],
                "geography": qc_reference["geography"],
                "cost_spread_after_transfers_cad_m3": qc_reference["cost_spread_after_transfers_cad_m3"],
                "net_value_status": qc_reference["net_value_status"],
                "policy_grade": qc_reference["policy_grade"],
            },
            "labels": economics["labels"],
            "downloads": economics["downloads"],
        },
        "national_economics": {
            "schema_version": national["schema_version"],
            "model_id": national["model_id"],
            "artifact_version": national.get("artifact_version"),
            "coverage": national["coverage"],
            "national_diagnostic": {
                "donor_flat": diagnostic["donor_flat"],
                "stumpage_scaled": diagnostic["stumpage_scaled"],
            },
            "labels": national_labels,
            "payload": national_record,
            "manifest": {
                "path": NATIONAL_MANIFEST.relative_to("public/data").as_posix(),
                "bytes": (destination / NATIONAL_MANIFEST).stat().st_size,
                "sha256": sha256_file(destination / NATIONAL_MANIFEST),
                "sources": len(national_manifest["sources"]),
            },
            "forest_lp_net_value_sensitivity": {
                "model_id": net_value["model_id"],
                "artifact_version": net_value.get("artifact_version"),
                "coverage": net_coverage,
                "documented_ceiling_row_count": len(documented),
                "infeasible_row_count": len(infeasible),
                "labels": net_labels,
                "economic_allocation_sensitivity": {
                    "model_id": allocation_lp["model_id"],
                    "artifact_version": allocation_lp.get("artifact_version"),
                    "coverage": allocation_coverage,
                    "labels": allocation_labels,
                    "donor_price_caveat": allocation_lp["donor_price_caveat"],
                },
            },
        },
        "facilities": {
            "schema_version": facilities["schema_version"],
            "artifact": facilities["artifact"],
            "coverage": facility_coverage,
            "default_scenario": facilities["default_scenario"],
            "scenarios": [
                {
                    "id": scenario["id"],
                    "harvest_m3": scenario["harvest_m3"],
                    "assigned_m3": scenario["assigned_m3"],
                    "facilities_used": scenario["facilities_used"],
                    "capacity_utilization": scenario["capacity_utilization"],
                }
                for scenario in facility_scenarios
            ],
            "national_chain": {
                "artifact": national_chain["artifact"],
                "artifact_version": national_chain["artifact_version"],
                "rows": [
                    {
                        "id": row["id"],
                        "label": row.get("label"),
                        "harvest_m3": row["harvest_m3"],
                        "solid_m3": row["solid_m3"],
                        "pulp_odt": row["pulp_odt"],
                        "process_pj": row["process_pj"],
                        "harvest_gross_pj": row["harvest_gross_pj"],
                        "harvest_recoverable_pj": row["harvest_recoverable_pj"],
                        "total_gross_pj": row["total_gross_pj"],
                    }
                    for row in chain_rows
                ],
                "columns": chain_columns,
                "reference": chain_reference,
                "labels": chain_labels,
                "coefficient_provenance": chain_provenance,
            },
            "labels": facility_labels,
            "payload": facility_record,
            "manifest": {
                "path": FACILITY_MANIFEST.relative_to("public/data").as_posix(),
                "bytes": (destination / FACILITY_MANIFEST).stat().st_size,
                "sha256": sha256_file(destination / FACILITY_MANIFEST),
                "sources": len(facility_manifest["sources"]),
            },
        },
        "data_bundle": {
            "manifest_path": DATA_MANIFEST.as_posix(),
            "manifest_sha256": sha256_file(destination / DATA_MANIFEST),
            "manifest_bytes": (destination / DATA_MANIFEST).stat().st_size,
            "files": len(data_records),
            "bytes": sum(item["bytes"] for item in data_records),
            "bundle_sha256": bundle_hash(destination / "public/data", data_records),
            "algorithm": 'sha256 of sorted "<sha256>  <path>" lines over data-manifest files',
        },
        "economics_bundle": {
            "manifest_path": ECONOMICS_MANIFEST.as_posix(),
            "manifest_sha256": sha256_file(destination / ECONOMICS_MANIFEST),
            "files": len(economics_records),
            "bytes": sum(item["bytes"] for item in economics_records),
            "bundle_sha256": bundle_hash(destination / "public/data", economics_records),
            "algorithm": 'sha256 of sorted "<sha256>  <path>" lines over economics-manifest files',
        },
        "manifest": {
            "path": MANIFEST_FILE,
            "bytes": manifest_path.stat().st_size,
            "sha256": sha256_file(manifest_path),
            "files": len(manifest["files"]),
            "total_bytes": manifest["total_bytes"],
            "excluded": [PUBLICATION_FILE, MANIFEST_FILE],
        },
        "static_mode": {
            "guard": GUARD_FILE,
            "guard_loaded_before": ["api.js", "app.js"],
            "badge": "Static mode \u00b7 no backend",
            "api_requests_blocked": True,
            "exports_available": True,
            "create_disabled": True,
            "queue_disabled": True,
        },
        "artifacts": artifacts,
        "validation": {},
    }
    (destination / PUBLICATION_FILE).write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")

    assert_no_unexpected_deletions(repo)
    print(json.dumps({
        "destination": destination.relative_to(repo).as_posix(),
        "payload_files": len(manifest["files"]),
        "payload_bytes": manifest["total_bytes"],
        "data_files": len(data_records),
        "economics_files": len(economics_records),
        "national_manifest": NATIONAL_MANIFEST.as_posix(),
        "national_jurisdictions": len(national["jurisdictions"]),
        "facilities": facility_coverage["record_count"],
        "facilities_mapped": facility_coverage["mapped_count"],
        "facility_scenarios": [scenario["id"] for scenario in facility_scenarios],
        "national_base_margin_cad_m3": diagnostic["donor_flat"]["base_cad_m3"],
        "national_sensitivity_margin_cad_m3": diagnostic["stumpage_scaled"]["base_cad_m3"],
        "headline": headline,
        "qc_spread": qc_reference["cost_spread_after_transfers_cad_m3"],
    }, indent=2))


if __name__ == "__main__":
    main()
