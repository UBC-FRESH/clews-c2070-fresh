#!/usr/bin/env python3
"""Validate NFI license determination YAML files against required schema.

This script validates that all NFI license determination files contain
the required fields for each source entry.

Required fields per source:
- source_id
- license_type
- model_use
- redistribution
- validation_use
- dua_required
- contact
- status

Usage:
    python scripts/validate_nfi_license_determination.py [--file PATH]
    python scripts/validate_nfi_license_determination.py --all
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = (
    "source_id",
    "license_type",
    "model_use",
    "redistribution",
    "validation_use",
    "dua_required",
    "contact",
    "status",
)

VALID_LICENSE_TYPES = {
    "custom_dua",
    "ogld_canada",
    "bc_open_data",
    "ab_open_data",
    "on_open_data",
    "qc_open_data",
    "other_open",
    "proprietary",
}

VALID_MODEL_USE = {"permitted", "permitted_with_conditions", "prohibited"}
VALID_REDISTRIBUTION = {"permitted", "permitted_with_attribution", "prohibited"}
VALID_VALIDATION_USE = {"permitted", "permitted_with_dua", "prohibited"}
VALID_STATUS = {"pending", "under_review", "accepted", "rejected", "blocked", "fallback_active"}

LICENSE_DETERMINATION_DIR = Path(
    "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/metadata/license-determinations"
)

EXPECTED_SOURCE_IDS = {
    "nfi_ground",
    "magplot",
    "bc_vri",
    "ab_psp",
    "on_fri",
    "qc_mrnf",
}


# ---------------------------------------------------------------------------
# Typed data structures (Parse, Don't Validate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a single source entry."""
    source_id: str
    valid: bool
    errors: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class FileValidationResult:
    """Result of validating a complete YAML file."""
    file_path: Path
    valid: bool
    source_results: list[ValidationResult]
    file_errors: list[str]


# ---------------------------------------------------------------------------
# Pure validation functions (Atomic Predictability)
# ---------------------------------------------------------------------------

def validate_contact(contact: Any, source_id: str) -> list[str]:
    """Validate contact object has required sub-fields."""
    errors = []
    if not isinstance(contact, dict):
        errors.append(f"{source_id}: contact must be a mapping, got {type(contact).__name__}")
        return errors

    required_contact_fields = ("name", "email")
    for field in required_contact_fields:
        if field not in contact:
            errors.append(f"{source_id}: contact missing required field '{field}'")
        elif not contact[field] or not str(contact[field]).strip():
            errors.append(f"{source_id}: contact.{field} is empty")

    return errors


def validate_source_entry(entry: dict[str, Any]) -> ValidationResult:
    """Validate a single source entry against the required schema."""
    source_id = entry.get("source_id", "UNKNOWN")
    errors = []
    warnings = []

    # Check required fields present
    for field in REQUIRED_FIELDS:
        if field not in entry:
            errors.append(f"{source_id}: missing required field '{field}'")

    # If missing required fields, can't validate further
    if errors:
        return ValidationResult(source_id=source_id, valid=False, errors=errors, warnings=warnings)

    # Validate license_type
    license_type = entry["license_type"]
    if license_type not in VALID_LICENSE_TYPES:
        warnings.append(
            f"{source_id}: license_type '{license_type}' not in known types: {sorted(VALID_LICENSE_TYPES)}"
        )

    # Validate model_use
    model_use = entry["model_use"]
    if model_use not in VALID_MODEL_USE:
        errors.append(
            f"{source_id}: model_use '{model_use}' invalid; must be one of {sorted(VALID_MODEL_USE)}"
        )

    # Validate redistribution
    redistribution = entry["redistribution"]
    if redistribution not in VALID_REDISTRIBUTION:
        errors.append(
            f"{source_id}: redistribution '{redistribution}' invalid; must be one of {sorted(VALID_REDISTRIBUTION)}"
        )

    # Validate validation_use
    validation_use = entry["validation_use"]
    if validation_use not in VALID_VALIDATION_USE:
        errors.append(
            f"{source_id}: validation_use '{validation_use}' invalid; must be one of {sorted(VALID_VALIDATION_USE)}"
        )

    # Validate dua_required (boolean)
    dua_required = entry["dua_required"]
    if not isinstance(dua_required, bool):
        errors.append(f"{source_id}: dua_required must be boolean, got {type(dua_required).__name__}")

    # Validate contact
    errors.extend(validate_contact(entry["contact"], source_id))

    # Validate status
    status = entry["status"]
    if status not in VALID_STATUS:
        warnings.append(
            f"{source_id}: status '{status}' not in known statuses: {sorted(VALID_STATUS)}"
        )

    # Cross-field validation: if dua_required is True, validation_use should be permitted_with_dua
    if dua_required and validation_use != "permitted_with_dua":
        warnings.append(
            f"{source_id}: dua_required is true but validation_use is '{validation_use}'; "
            f"expected 'permitted_with_dua'"
        )

    # Cross-field validation: if license_type is custom_dua, dua_required should be True
    if license_type == "custom_dua" and not dua_required:
        warnings.append(
            f"{source_id}: license_type is 'custom_dua' but dua_required is false"
        )

    valid = len(errors) == 0
    return ValidationResult(source_id=source_id, valid=valid, errors=errors, warnings=warnings)


def validate_file(file_path: Path) -> FileValidationResult:
    """Validate a single YAML license determination file."""
    file_errors = []
    source_results = []

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        file_errors.append(f"YAML parse error: {e}")
        return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)
    except Exception as e:
        file_errors.append(f"File read error: {e}")
        return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)

    if not data:
        file_errors.append("File is empty or contains only null")
        return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)

    # Only validate source entries for nfi-access.yaml (the license determination registry)
    # Checkpoint and fallback strategy files have different schemas
    if file_path.name == "nfi-access.yaml":
        if "sources" not in data:
            file_errors.append("Missing top-level 'sources' key")
            return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)

        sources = data["sources"]
        if not isinstance(sources, list):
            file_errors.append("'sources' must be a list")
            return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)

        if not sources:
            file_errors.append("'sources' list is empty")
            return FileValidationResult(file_path=file_path, valid=False, source_results=[], file_errors=file_errors)

        # Validate each source entry
        seen_ids = set()
        for entry in sources:
            if not isinstance(entry, dict):
                file_errors.append(f"Source entry must be a mapping, got {type(entry).__name__}")
                continue

            result = validate_source_entry(entry)
            source_results.append(result)

            # Check for duplicate source_ids
            if result.source_id in seen_ids:
                file_errors.append(f"Duplicate source_id: {result.source_id}")
            seen_ids.add(result.source_id)

        # Check all expected sources are present
        missing = EXPECTED_SOURCE_IDS - seen_ids
        if missing:
            file_errors.append(f"Missing expected source_ids: {sorted(missing)}")
    else:
        # For checkpoint/fallback files, just verify they're valid YAML with expected top-level keys
        expected_keys = {
            "nfi-checkpoint-m3.yaml": {"checkpoint", "criteria", "decisions"},
            "nfi-checkpoint-m6.yaml": {"checkpoint", "criteria", "decisions"},
            "nfi-checkpoint-m12.yaml": {"checkpoint", "criteria", "decisions"},
            "nfi-fallback-strategy.yaml": {"fallback_tiers", "activation_protocol", "risk_mitigation"},
        }
        if file_path.name in expected_keys:
            missing_keys = expected_keys[file_path.name] - set(data.keys())
            if missing_keys:
                file_errors.append(f"Missing expected top-level keys: {sorted(missing_keys)}")
        # No source validation for these files

    file_valid = len(file_errors) == 0 and all(r.valid for r in source_results)
    return FileValidationResult(file_path=file_path, valid=file_valid, source_results=source_results, file_errors=file_errors)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def find_license_files() -> list[Path]:
    """Find all license determination YAML files."""
    if not LICENSE_DETERMINATION_DIR.exists():
        return []

    return sorted(LICENSE_DETERMINATION_DIR.glob("*.yaml"))


def main() -> int:
    """Main entry point. Returns exit code."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate NFI license determination YAML files")
    parser.add_argument("--file", type=Path, help="Validate a specific file")
    parser.add_argument("--all", action="store_true", help="Validate all files in license-determinations directory")
    args = parser.parse_args()

    if args.file:
        files = [args.file]
    elif args.all:
        files = find_license_files()
        if not files:
            print(f"No YAML files found in {LICENSE_DETERMINATION_DIR}", file=sys.stderr)
            return 1
    else:
        # Default: validate nfi-access.yaml
        default_file = LICENSE_DETERMINATION_DIR / "nfi-access.yaml"
        if default_file.exists():
            files = [default_file]
        else:
            files = find_license_files()

    print(f"Validating {len(files)} file(s)...\n")

    all_valid = True
    total_sources = 0
    total_errors = 0
    total_warnings = 0

    for file_path in files:
        print(f"=== {file_path.name} ===")
        result = validate_file(file_path)

        if result.file_errors:
            for err in result.file_errors:
                print(f"  FILE ERROR: {err}")
            all_valid = False

        for src_result in result.source_results:
            total_sources += 1
            total_errors += len(src_result.errors)
            total_warnings += len(src_result.warnings)

            if src_result.valid:
                print(f"  ✓ {src_result.source_id}: VALID")
            else:
                print(f"  ✗ {src_result.source_id}: INVALID")
                all_valid = False

            for err in src_result.errors:
                print(f"    ERROR: {err}")
            for warn in src_result.warnings:
                print(f"    WARNING: {warn}")

        print()

    # Summary
    print("=== SUMMARY ===")
    print(f"Files validated: {len(files)}")
    print(f"Sources validated: {total_sources}")
    print(f"Errors: {total_errors}")
    print(f"Warnings: {total_warnings}")
    print(f"Overall: {'PASS' if all_valid else 'FAIL'}")

    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())