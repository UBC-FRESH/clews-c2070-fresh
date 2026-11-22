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
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

APP_FILES = ("index.html", "app.js", "api.js", "styles.css")
GUARD_FILE = "publication-mode.js"
MANIFEST_FILE = "manifest.json"
PUBLICATION_FILE = "PUBLICATION.json"
MANIFEST_ANCHOR = (
    '  <script defer src="vendor/maplibre-gl.js"></script>\n'
    '  <script defer src="api.js"></script>\n'
)
PUBLICATION_ANCHOR = (
    '  <script defer src="vendor/maplibre-gl.js"></script>\n'
    '  <script defer src="' + GUARD_FILE + '"></script>\n'
    '  <script defer src="api.js"></script>\n'
)
DATA_MANIFEST = Path("public/data/manifest.json")
ECONOMICS_MANIFEST = Path("public/data/economics/manifest.json")
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


def build_index(source_index: str) -> str:
    if GUARD_FILE in source_index:
        fail("source index.html already contains the publication guard")
    if source_index.count(MANIFEST_ANCHOR) != 1:
        fail("source index.html script anchor is missing or ambiguous")
    return source_index.replace(MANIFEST_ANCHOR, PUBLICATION_ANCHOR, 1)


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

    index_source = (source / "index.html").read_text(encoding="utf-8")
    (destination / "index.html").write_text(build_index(index_source), encoding="utf-8")
    (destination / GUARD_FILE).write_text(
        publication_guard(args.release, args.tag, args.source_commit),
        encoding="utf-8",
    )
    verify_copied_bytes(source, destination)

    data_manifest, data_records = verify_manifest(destination / "public/data", destination / DATA_MANIFEST)
    economics_manifest, economics_records = verify_manifest(
        destination / "public/data", destination / ECONOMICS_MANIFEST
    )
    economics_paths = {record["path"] for record in economics_records}
    if not set(ECONOMICS_FILES).issubset(economics_paths):
        fail("economics manifest does not cover summary.json, detail.csv, and model_inputs.zip")

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
        "headline": headline,
        "qc_spread": qc_reference["cost_spread_after_transfers_cad_m3"],
    }, indent=2))


if __name__ == "__main__":
    main()
