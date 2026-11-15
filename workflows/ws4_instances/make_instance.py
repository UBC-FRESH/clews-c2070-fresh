"""Scaffold a standard WS4 TSA instance directory from a compiled dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

TEMPLATES = Path(__file__).resolve().parent / "templates"
DATA_GITATTRIBUTES = """# Dataset tables are binary payloads; annex them explicitly.
dataset/*.parquet annex.largefiles=anything

# Large run outputs are text but should not bloat the Git history.
schedules/*.csv annex.largefiles=largerthan=1MB
runs/area_control/*.csv annex.largefiles=largerthan=1MB
"""

class InstanceScaffoldError(RuntimeError):
    """Raised when an instance cannot be scaffolded safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_tables(dataset: Path) -> list[Path]:
    manifest_path = dataset / "manifest.json"
    if not manifest_path.is_file():
        raise InstanceScaffoldError(f"dataset has no manifest.json: {dataset}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise InstanceScaffoldError(f"{manifest_path}: manifest has no tables mapping")
    paths = [manifest_path]
    for table, filename in tables.items():
        path = dataset / str(filename)
        if not path.is_file():
            raise InstanceScaffoldError(f"manifest table {table!r} is missing: {path}")
        paths.append(path)
    return paths


def _render(text: str, replacements: dict[str, str]) -> str:
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    return text


def _ws4_version() -> str:
    try:
        from importlib.metadata import version

        return version("ws4")
    except Exception:  # pragma: no cover - metadata is optional outside the WS4 venv
        return "unavailable"


def scaffold_instance(
    tsa_number: str,
    name: str,
    dataset: Path,
    out: Path,
    *,
    force: bool = False,
    femic_bundle: str = "",
    recipe: str = "",
) -> Path:
    """Create ``out`` with dataset, config templates, and provenance placeholders."""
    dataset = Path(dataset).resolve()
    out = Path(out)
    if not dataset.is_dir():
        raise InstanceScaffoldError(f"dataset directory does not exist: {dataset}")
    if out.exists() and any(out.iterdir()) and not force:
        raise InstanceScaffoldError(
            f"output directory is not empty: {out} (use --force to overwrite files)"
        )
    if not TEMPLATES.is_dir():
        raise InstanceScaffoldError(f"template directory is missing: {TEMPLATES}")

    dataset_files = _dataset_tables(dataset)
    out.mkdir(parents=True, exist_ok=True)
    dataset_dir = out / "dataset"
    config_dir = out / "config"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    for path in dataset_files:
        shutil.copy2(path, dataset_dir / path.name)
    (out / ".gitattributes").write_text(DATA_GITATTRIBUTES, encoding="utf-8")

    manifest_sha = _sha256(dataset / "manifest.json")
    created = datetime.now(UTC).replace(microsecond=0).isoformat()
    replacements = {
        "{{TSA_NUMBER}}": str(tsa_number),
        "{{TSA_NAME}}": name,
        "{{DATASET_PATH}}": str(dataset),
        "{{DATASET_SHA256}}": manifest_sha,
        "{{CREATED_UTC}}": created,
        "{{WS4_VERSION}}": _ws4_version(),
    }

    readme_template = TEMPLATES / "instance_README.md"
    (out / "README.md").write_text(
        _render(readme_template.read_text(encoding="utf-8"), replacements),
        encoding="utf-8",
    )
    for source, destination in (
        ("area_control.yaml", config_dir / "area_control.yaml"),
        ("targets.yaml", config_dir / "targets.yaml"),
        ("run_profile.yaml", config_dir / "run_profile.yaml"),
    ):
        destination.write_text(
            _render((TEMPLATES / source).read_text(encoding="utf-8"), replacements),
            encoding="utf-8",
        )

    instance = {
        "tsa_number": str(tsa_number),
        "tsa_name": name,
        "dataset_path": str(dataset),
        "dataset_local": "dataset",
        "dataset_sha256": manifest_sha,
        "dataset_manifest_sha256": manifest_sha,
        "dataset_file_count": len(dataset_files),
        "created_utc": created,
        "ws4_version": replacements["{{WS4_VERSION}}"],
        "area_control_config": "config/area_control.yaml",
        "targets": "config/targets.yaml",
        "schedule": "schedules/area_control.csv",
        "femic": {
            "source_bundle": femic_bundle,
            "recipe": recipe,
            "fragments": "",
            "build_command": "",
            "rebuild_status": "not rebuilt in this instance",
        },
    }
    (config_dir / "instance.yaml").write_text(
        yaml.safe_dump(instance, sort_keys=False), encoding="utf-8"
    )
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsa-number", required=True, help="BC TSA number, e.g. 24")
    parser.add_argument("--name", required=True, help="TSA name, e.g. 'Prince George'")
    parser.add_argument("--dataset", required=True, type=Path, help="compiled WS4 dataset")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="instance directory (default: data/derived/ws4/tsa<NUMBER>)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing scaffold")
    parser.add_argument("--femic-bundle", default="", help="optional FEMIC bundle path")
    parser.add_argument("--recipe", default="", help="optional recipe or ForestModelXML path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = args.out or (Path("data/derived/ws4") / f"tsa{args.tsa_number}")
    try:
        scaffold_instance(
            args.tsa_number,
            args.name,
            args.dataset,
            out,
            force=args.force,
            femic_bundle=args.femic_bundle,
            recipe=args.recipe,
        )
    except InstanceScaffoldError as error:
        print(f"make_instance: {error}")
        return 2
    print(f"make_instance: scaffolded {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
