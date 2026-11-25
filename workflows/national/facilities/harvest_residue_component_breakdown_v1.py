"""Build the 119 Mm3 harvest-residue component breakdown v1.

The committed facility product chain carries gross harvest residue as stem and
branch+foliage ODT.  This artifact splits branch+foliage into branch and
foliage using each province's E1 measured or E2c open-data source ratio, then
converts every component to energy at 18.5 GJ/ODT.

Tops are not separately modelled.  Stem residue is the taper total from a
0.3 m stump to the tree tip minus recovered logs, so it already includes stump,
top and unmerchantable sections.  No tops number is invented.

The 0.35/0.60/0.75 recovery values are uniform arithmetic scenarios applied to
gross residue.  No environmental, operational, technical, economic or
accessibility recovery constraint is modelled, and 0.60 is a reference
scenario, not a calibrated recovery factor.

Labels: ``model_estimate=true``, ``observed=false``, ``policy_grade=false``,
``national_claim=false``, ``official_osecan_changed=false`` and
``ui_changed=false``.  A ``D`` in either the parent or data repository git
status halts the build.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/harvest_residue_component_breakdown_v1"
)
CHAIN_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
CHAIN_CSV = CHAIN_DIR / "product_chain_by_province.csv"
E1_DIR = DATA_ROOT / "derived/national/bucking/four_province_bucking_pilot_v0"
E1_CSV = E1_DIR / "province_summary.csv"
E2C_DIR = DATA_ROOT / "derived/national/bucking/open_data_imputation_v3"
E2C_CSV = E2C_DIR / "province_summary.csv"
MODEL_CARD = REPO_ROOT / "docs/model-card-national-facility-chain.md"

ARTIFACT = "harvest_residue_component_breakdown_v1"
ARTIFACT_VERSION = "1.0.0"
REPORT_NAME = "HARVEST_RESIDUE_COMPONENT_BREAKDOWN_V1.md"
MODULE_PATH = (
    "workflows.national.facilities.harvest_residue_component_breakdown_v1"
)
TEST_PATH = "tests/test_harvest_residue_component_breakdown_v1.py"

GJ_PER_ODT = 18.5
PJ_PER_GJ = 1e-6
RECOVERY_FACTORS: tuple[float, ...] = (0.35, 0.60, 0.75)
REFERENCE_RECOVERY_FACTOR = 0.60

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
E1_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
E2C_PROVINCES: tuple[str, ...] = (
    "AB",
    "SK",
    "MB",
    "ON",
    "NS",
    "PE",
    "NL",
    "NT",
    "NU",
)
TIER_BY_PROVINCE: dict[str, str] = {
    **{province: "measured_bucking_pilot" for province in E1_PROVINCES},
    **{
        province: "E2c_open_inventory_density_calibrated"
        for province in E2C_PROVINCES
    },
}

EXPECTED_NATIONAL_TOTAL_PJ = 444.410766
EXPECTED_BRANCH_FOLIAGE_PJ = 316.579
EXPECTED_RECOVERABLE_060_PJ = 266.646
MODEL_CARD_TOLERANCE_PJ = 0.001
COMPONENT_CONSERVATION_TOLERANCE_ODT = 1e-4
NATIONAL_TOTAL_TOLERANCE_PJ = 5e-7
DISPLAY_TOLERANCE_PJ = 1e-3

TOPS_NOTE = (
    "Tops are not separately modelled; stem residue is the taper total "
    "(0.3 m stump to tip) minus recovered logs and includes stump, top and "
    "unmerchantable sections."
)
RECOVERY_NOTE = (
    "0.35/0.60/0.75 are uniform arithmetic scenarios applied to gross total "
    "residue. No environmental, operational, technical, economic or "
    "accessibility recovery constraint is modelled; 0.60 is a reference "
    "scenario, not a calibrated recovery factor."
)

COMPONENT_FIELDS: tuple[str, ...] = (
    "province",
    "province_name",
    "tier",
    "stem_odt",
    "branch_odt",
    "foliage_odt",
    "total_odt",
    "stem_pj",
    "branch_pj",
    "foliage_pj",
    "total_pj",
    "branch_foliage_pj",
    "stem_share",
    "branch_share",
    "foliage_share",
    "branch_foliage_share",
    "branch_share_of_branch_foliage",
    "branch_foliage_split_source",
    "tops_separately_modeled",
    "tops_note",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
    "official_osecan_changed",
    "ui_changed",
)
PROVENANCE_FIELDS: tuple[str, ...] = (
    "source_id",
    "role",
    "name",
    "publisher",
    "year",
    "licence",
    "licence_url",
    "url",
    "path",
    "sha256",
    "retrieved_utc",
    "used_for",
    "notes",
)
INPUT_PATHS: tuple[Path, ...] = (CHAIN_CSV, E1_CSV, E2C_CSV, MODEL_CARD)


class ComponentBreakdownError(RuntimeError):
    """Raised when the component breakdown inputs or arithmetic fail."""


@dataclass(frozen=True)
class ChainRow:
    province: str
    province_name: str
    tier: str
    stem_odt: float
    branch_foliage_odt: float
    total_odt: float


@dataclass(frozen=True)
class BranchRatio:
    province: str
    branch_odt: float
    foliage_odt: float
    source: str

    @property
    def branch_share(self) -> float:
        return self.branch_odt / (self.branch_odt + self.foliage_odt)


@dataclass(frozen=True)
class ComponentRow:
    province: str
    province_name: str
    tier: str
    stem_odt: float
    branch_odt: float
    foliage_odt: float
    total_odt: float
    branch_share_of_branch_foliage: float
    branch_foliage_split_source: str

    @property
    def stem_pj(self) -> float:
        return _to_pj(self.stem_odt)

    @property
    def branch_pj(self) -> float:
        return _to_pj(self.branch_odt)

    @property
    def foliage_pj(self) -> float:
        return _to_pj(self.foliage_odt)

    @property
    def total_pj(self) -> float:
        return _to_pj(self.total_odt)

    @property
    def branch_foliage_pj(self) -> float:
        return _to_pj(self.branch_odt + self.foliage_odt)


def _to_pj(odt: float) -> float:
    return odt * GJ_PER_ODT * PJ_PER_GJ


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ComponentBreakdownError(f"missing {label}: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ComponentBreakdownError(f"empty {label}: {path}")
    return rows


def _write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _number(row: Mapping[str, str], field: str, source: Path) -> float:
    raw = row.get(field, "").strip()
    if not raw:
        raise ComponentBreakdownError(f"{source}: missing field {field}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ComponentBreakdownError(
            f"{source}: non-numeric {field}={raw!r}"
        ) from exc
    if not math.isfinite(value) or value < 0.0:
        raise ComponentBreakdownError(
            f"{source}: invalid {field}={raw!r}"
        )
    return value


def _load_chain() -> dict[str, ChainRow]:
    rows = _read_csv(CHAIN_CSV, "product chain")
    chain: dict[str, ChainRow] = {}
    for row in rows:
        province = row.get("province", "").strip()
        if not province:
            raise ComponentBreakdownError(f"{CHAIN_CSV}: empty province")
        if province in chain:
            raise ComponentBreakdownError(
                f"{CHAIN_CSV}: duplicate province {province}"
            )
        tier = row.get("coefficient_tier", "").strip()
        expected_tier = TIER_BY_PROVINCE.get(province)
        if expected_tier is None:
            raise ComponentBreakdownError(
                f"{CHAIN_CSV}: unexpected province {province}"
            )
        if tier != expected_tier:
            raise ComponentBreakdownError(
                f"{CHAIN_CSV}: {province} tier {tier!r} != "
                f"{expected_tier!r}"
            )
        chain[province] = ChainRow(
            province=province,
            province_name=row.get("province_name", "").strip() or province,
            tier=tier,
            stem_odt=_number(
                row, "harvest_residue_stem_gross_odt", CHAIN_CSV
            ),
            branch_foliage_odt=_number(
                row, "harvest_residue_branch_foliage_odt", CHAIN_CSV
            ),
            total_odt=_number(
                row, "harvest_residue_total_gross_odt", CHAIN_CSV
            ),
        )
    missing = sorted(set(PROVINCES) - set(chain))
    if missing:
        raise ComponentBreakdownError(
            f"{CHAIN_CSV}: missing provinces {missing}"
        )
    return chain


def _load_branch_ratios() -> dict[str, BranchRatio]:
    ratios: dict[str, BranchRatio] = {}
    sources = (
        (E1_CSV, E1_PROVINCES, "four_province_bucking_pilot_v0"),
        (E2C_CSV, E2C_PROVINCES, "open_data_imputation_v3"),
    )
    for path, expected_provinces, source in sources:
        rows = _read_csv(path, "branch/foliage source")
        found: set[str] = set()
        for row in rows:
            province = row.get("province", "").strip()
            if province not in expected_provinces:
                raise ComponentBreakdownError(
                    f"{path}: unexpected province {province!r}"
                )
            if province in found:
                raise ComponentBreakdownError(
                    f"{path}: duplicate province {province}"
                )
            branch = _number(row, "branch_odt_expanded", path)
            foliage = _number(row, "foliage_odt_expanded", path)
            if branch + foliage <= 0.0:
                raise ComponentBreakdownError(
                    f"{path}: {province} branch+foliage is zero"
                )
            found.add(province)
            ratios[province] = BranchRatio(
                province=province,
                branch_odt=branch,
                foliage_odt=foliage,
                source=source,
            )
        missing = sorted(set(expected_provinces) - found)
        if missing:
            raise ComponentBreakdownError(
                f"{path}: missing provinces {missing}"
            )
    if set(ratios) != set(PROVINCES):
        raise ComponentBreakdownError(
            "branch/foliage source coverage does not match the 13 provinces"
        )
    return ratios


def _parse_model_card_gross_pj() -> dict[str, float]:
    if not MODEL_CARD.is_file():
        raise ComponentBreakdownError(f"missing model card: {MODEL_CARD}")
    in_section = False
    values: dict[str, float] = {}
    for line in MODEL_CARD.read_text(encoding="utf-8").splitlines():
        if line.startswith("### 5.3 "):
            in_section = True
            continue
        if in_section and line.startswith("### "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] not in PROVINCES:
            continue
        try:
            values[cells[0]] = float(cells[2])
        except ValueError as exc:
            raise ComponentBreakdownError(
                f"{MODEL_CARD}: non-numeric gross PJ for {cells[0]}"
            ) from exc
    missing = sorted(set(PROVINCES) - set(values))
    if missing:
        raise ComponentBreakdownError(
            f"{MODEL_CARD}: §5.3 missing provinces {missing}"
        )
    return values


def _component_rows(
    chain: Mapping[str, ChainRow],
    ratios: Mapping[str, BranchRatio],
) -> list[ComponentRow]:
    rows: list[ComponentRow] = []
    for province in PROVINCES:
        chain_row = chain[province]
        ratio = ratios[province]
        branch_odt = chain_row.branch_foliage_odt * ratio.branch_share
        foliage_odt = chain_row.branch_foliage_odt - branch_odt
        rows.append(
            ComponentRow(
                province=province,
                province_name=chain_row.province_name,
                tier=chain_row.tier,
                stem_odt=chain_row.stem_odt,
                branch_odt=branch_odt,
                foliage_odt=foliage_odt,
                total_odt=chain_row.total_odt,
                branch_share_of_branch_foliage=ratio.branch_share,
                branch_foliage_split_source=ratio.source,
            )
        )
    return rows


def _national_totals(rows: Sequence[ComponentRow]) -> dict[str, float]:
    stem_odt = math.fsum(row.stem_odt for row in rows)
    branch_odt = math.fsum(row.branch_odt for row in rows)
    foliage_odt = math.fsum(row.foliage_odt for row in rows)
    total_odt = math.fsum(row.total_odt for row in rows)
    stem_pj = _to_pj(stem_odt)
    branch_pj = _to_pj(branch_odt)
    foliage_pj = _to_pj(foliage_odt)
    total_pj = _to_pj(total_odt)
    return {
        "stem_odt": stem_odt,
        "branch_odt": branch_odt,
        "foliage_odt": foliage_odt,
        "total_odt": total_odt,
        "stem_pj": stem_pj,
        "branch_pj": branch_pj,
        "foliage_pj": foliage_pj,
        "total_pj": total_pj,
        "branch_foliage_pj": _to_pj(branch_odt + foliage_odt),
        "stem_share": stem_pj / total_pj,
        "branch_share": branch_pj / total_pj,
        "foliage_share": foliage_pj / total_pj,
        "branch_foliage_share": (branch_pj + foliage_pj) / total_pj,
    }


def _recoverable_pj(total_pj: float) -> dict[str, float]:
    return {
        f"{factor:.2f}": factor * total_pj for factor in RECOVERY_FACTORS
    }


def _model_card_cross_check(
    rows: Sequence[ComponentRow],
    model_card_gross_pj: Mapping[str, float],
) -> dict[str, Any]:
    provinces: list[dict[str, Any]] = []
    deltas: list[float] = []
    for row in rows:
        expected = model_card_gross_pj[row.province]
        delta = row.total_pj - expected
        deltas.append(abs(delta))
        provinces.append(
            {
                "province": row.province,
                "model_card_harvest_gross_pj": expected,
                "component_total_pj": row.total_pj,
                "delta_pj": delta,
                "within_tolerance": abs(delta) <= MODEL_CARD_TOLERANCE_PJ,
            }
        )
    return {
        "source": "docs/model-card-national-facility-chain.md §5.3",
        "tolerance_pj": MODEL_CARD_TOLERANCE_PJ,
        "max_abs_delta_pj": max(deltas),
        "provinces": provinces,
        "all_within_tolerance": all(
            entry["within_tolerance"] for entry in provinces
        ),
    }


def _validate(
    rows: Sequence[ComponentRow],
    chain: Mapping[str, ChainRow],
    totals: Mapping[str, float],
    cross_check: Mapping[str, Any],
) -> dict[str, Any]:
    conservation_errors = {
        row.province: (
            row.stem_odt + row.branch_odt + row.foliage_odt - row.total_odt
        )
        for row in rows
    }
    recoverable = _recoverable_pj(totals["total_pj"])
    checks = {
        "row_count_13": len(rows) == len(PROVINCES),
        "province_set_matches_chain": {row.province for row in rows}
        == set(chain),
        "tier_mapping_matches": all(
            row.tier == TIER_BY_PROVINCE[row.province] for row in rows
        ),
        "component_conservation_all_provinces": all(
            abs(error) <= COMPONENT_CONSERVATION_TOLERANCE_ODT
            for error in conservation_errors.values()
        ),
        "national_total_matches_expected": abs(
            totals["total_pj"] - EXPECTED_NATIONAL_TOTAL_PJ
        )
        <= NATIONAL_TOTAL_TOLERANCE_PJ,
        "national_branch_foliage_matches_expected": abs(
            totals["branch_foliage_pj"] - EXPECTED_BRANCH_FOLIAGE_PJ
        )
        <= DISPLAY_TOLERANCE_PJ,
        "national_recoverable_060_matches_expected": abs(
            recoverable["0.60"] - EXPECTED_RECOVERABLE_060_PJ
        )
        <= DISPLAY_TOLERANCE_PJ,
        "model_card_5_3_cross_check": bool(
            cross_check["all_within_tolerance"]
        ),
        "shares_sum_to_one": abs(
            totals["stem_share"]
            + totals["branch_share"]
            + totals["foliage_share"]
            - 1.0
        )
        <= 1e-12,
        "labels_complete": bool(rows),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise ComponentBreakdownError(
            "validation failed: " + ", ".join(failures)
        )
    return {
        "checks": checks,
        "all_passed": True,
        "failures": [],
        "max_component_conservation_error_odt": max(
            abs(error) for error in conservation_errors.values()
        ),
        "national_total_pj_unrounded": totals["total_pj"],
    }


def _labels() -> dict[str, bool]:
    return {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
    }


def _component_csv_rows(
    rows: Sequence[ComponentRow],
    labels: Mapping[str, bool],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        total_odt = row.total_odt
        has_mass = total_odt > 0.0
        branch_foliage_odt = row.branch_odt + row.foliage_odt
        output.append(
            {
                "province": row.province,
                "province_name": row.province_name,
                "tier": row.tier,
                "stem_odt": f"{row.stem_odt:.6f}",
                "branch_odt": f"{row.branch_odt:.6f}",
                "foliage_odt": f"{row.foliage_odt:.6f}",
                "total_odt": f"{row.total_odt:.6f}",
                "stem_pj": f"{row.stem_pj:.6f}",
                "branch_pj": f"{row.branch_pj:.6f}",
                "foliage_pj": f"{row.foliage_pj:.6f}",
                "total_pj": f"{row.total_pj:.6f}",
                "branch_foliage_pj": f"{row.branch_foliage_pj:.6f}",
                "stem_share": (
                    f"{row.stem_pj / row.total_pj:.9f}" if has_mass else ""
                ),
                "branch_share": (
                    f"{row.branch_pj / row.total_pj:.9f}" if has_mass else ""
                ),
                "foliage_share": (
                    f"{row.foliage_pj / row.total_pj:.9f}"
                    if has_mass
                    else ""
                ),
                "branch_foliage_share": (
                    f"{branch_foliage_odt / total_odt:.9f}"
                    if has_mass
                    else ""
                ),
                "branch_share_of_branch_foliage": (
                    f"{row.branch_share_of_branch_foliage:.9f}"
                ),
                "branch_foliage_split_source": row.branch_foliage_split_source,
                "tops_separately_modeled": "false",
                "tops_note": TOPS_NOTE,
                "model_estimate": _bool(labels["model_estimate"]),
                "observed": _bool(labels["observed"]),
                "policy_grade": _bool(labels["policy_grade"]),
                "national_claim": _bool(labels["national_claim"]),
                "official_osecan_changed": _bool(
                    labels["official_osecan_changed"]
                ),
                "ui_changed": _bool(labels["ui_changed"]),
            }
        )
    return output


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _provenance_rows(
    generated_utc: str,
    input_hashes: Mapping[str, str],
) -> list[dict[str, str]]:
    chain_path = CHAIN_CSV.relative_to(REPO_ROOT).as_posix()
    e1_path = E1_CSV.relative_to(REPO_ROOT).as_posix()
    e2c_path = E2C_CSV.relative_to(REPO_ROOT).as_posix()
    card_path = MODEL_CARD.relative_to(REPO_ROOT).as_posix()
    data_url = "https://github.com/UBC-FRESH/clews-c2070-fresh-data"
    project_licence = "Project-derived; see source metadata"
    return [
        {
            "source_id": "national_product_chain_v5",
            "role": "input",
            "name": "National product chain v5 province table",
            "publisher": "UBC FRESH (project-derived)",
            "year": "2026",
            "licence": project_licence,
            "licence_url": "",
            "url": f"{data_url}/blob/main/{chain_path[len('data/'):]}",
            "path": chain_path,
            "sha256": input_hashes[chain_path],
            "retrieved_utc": generated_utc,
            "used_for": (
                "Stem gross ODT, branch+foliage gross ODT and total gross ODT "
                "by province at the 119 Mm3 case."
            ),
            "notes": "Read-only committed chain v5 artifact; no value changed.",
        },
        {
            "source_id": "four_province_bucking_pilot_v0",
            "role": "input",
            "name": (
                "Four-province measured bucking pilot province summary"
            ),
            "publisher": "UBC FRESH (project-derived)",
            "year": "2026",
            "licence": project_licence,
            "licence_url": "",
            "url": f"{data_url}/blob/main/{e1_path[len('data/'):]}",
            "path": e1_path,
            "sha256": input_hashes[e1_path],
            "retrieved_utc": generated_utc,
            "used_for": (
                "Measured branch/foliage split ratio for BC, QC, NB and YT."
            ),
            "notes": (
                "E1 measured tree lists; branch and foliage dry mass comes "
                "from published CFS equations, not weighed components."
            ),
        },
        {
            "source_id": "open_data_imputation_v3",
            "role": "input",
            "name": "E2c open-data imputation province summary",
            "publisher": "UBC FRESH (project-derived)",
            "year": "2026",
            "licence": project_licence,
            "licence_url": "",
            "url": f"{data_url}/blob/main/{e2c_path[len('data/'):]}",
            "path": e2c_path,
            "sha256": input_hashes[e2c_path],
            "retrieved_utc": generated_utc,
            "used_for": (
                "Synthetic 36-tree branch/foliage split ratio for AB, SK, MB, "
                "ON, NS, PE, NL and NT; NU is zero-harvest."
            ),
            "notes": (
                "E2c ratios come from open-data imputation with donor "
                "geometry and published CFS branch/foliage equations."
            ),
        },
        {
            "source_id": "model_card_national_facility_chain",
            "role": "cross_check",
            "name": (
                "Model card - National facility chain v5.1, §5.3 "
                "provincial gross harvest PJ"
            ),
            "publisher": "UBC FRESH (project-derived)",
            "year": "2026",
            "licence": project_licence,
            "licence_url": "",
            "url": (
                "https://github.com/UBC-FRESH/clews-c2070-fresh/blob/main/"
                f"{card_path}"
            ),
            "path": card_path,
            "sha256": input_hashes[card_path],
            "retrieved_utc": generated_utc,
            "used_for": (
                "Province-level gross harvest PJ cross-check at 0.001 PJ."
            ),
            "notes": "Documentation reference only; no value is copied.",
        },
        {
            "source_id": "cfs_branch_foliage_equations",
            "role": "context",
            "name": (
                "CFS Biomass and Nutrients tree-level DBH + height equations "
                "(Lambert, Ung and Raulier 2005; Ung, Bernier and Guo 2008)"
            ),
            "publisher": "Canadian Forest Service",
            "year": "2005;2008",
            "licence": "Open Government Licence - Canada",
            "licence_url": (
                "https://open.canada.ca/en/open-government-licence-canada"
            ),
            "url": "",
            "path": "",
            "sha256": "",
            "retrieved_utc": generated_utc,
            "used_for": (
                "Upstream basis for the E1/E2c branch and foliage dry-mass "
                "ratios; not re-applied in this artifact."
            ),
            "notes": (
                "Context only; the committed E1 and E2c summaries are the "
                "direct inputs."
            ),
        },
        {
            "source_id": "component_breakdown_arithmetic",
            "role": "derived_parameter",
            "name": "Component split, conversion and recovery arithmetic",
            "publisher": "This model",
            "year": "2026",
            "licence": (
                "CC0 1.0 Universal public-domain dedication"
            ),
            "licence_url": (
                "https://creativecommons.org/publicdomain/zero/1.0/"
            ),
            "url": "",
            "path": "",
            "sha256": "",
            "retrieved_utc": generated_utc,
            "used_for": (
                "Branch share of branch+foliage, 18.5 GJ/ODT conversion, and "
                "0.35/0.60/0.75 uniform recovery scenarios."
            ),
            "notes": RECOVERY_NOTE,
        },
    ]


def _display(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _report(
    rows: Sequence[ComponentRow],
    totals: Mapping[str, float],
    recoverable: Mapping[str, float],
    cross_check: Mapping[str, Any],
    validation: Mapping[str, Any],
    input_hashes: Mapping[str, str],
    labels: Mapping[str, bool],
) -> str:
    lines = [
        "# Harvest residue component breakdown v1 (119 Mm³)",
        "",
        "## 1. Scope",
        "",
        "This provenance artifact splits the committed 119 Mm³ gross harvest",
        "residue into stem, branch and foliage components. It adds no new",
        "supply, recovery, siting, cost or policy result. Every row is a",
        "`model_estimate`; `observed=false`; `policy_grade=false`;",
        "`national_claim=false`; `official_osecan_changed=false`;",
        "`ui_changed=false`.",
        "",
        "Inputs:",
        "",
        f"- `{CHAIN_CSV.relative_to(REPO_ROOT).as_posix()}`",
        f"- `{E1_CSV.relative_to(REPO_ROOT).as_posix()}`",
        f"- `{E2C_CSV.relative_to(REPO_ROOT).as_posix()}`",
        f"- `{MODEL_CARD.relative_to(REPO_ROOT).as_posix()}` §5.3",
        "",
        "## 2. Method",
        "",
        "For province `p`, with chain stem ODT `S_p`, chain branch+foliage",
        "ODT `B_p`, chain total ODT `T_p`, and source ratio",
        "`r_p = branch_odt_expanded / (branch_odt_expanded +",
        "foliage_odt_expanded)`:",
        "",
        "```",
        "branch_odt_p  = B_p * r_p",
        "foliage_odt_p = B_p - branch_odt_p",
        "stem_pj_p     = S_p * 18.5 / 1e6",
        "branch_pj_p   = branch_odt_p * 18.5 / 1e6",
        "foliage_pj_p  = foliage_odt_p * 18.5 / 1e6",
        "total_pj_p    = T_p * 18.5 / 1e6",
        "recoverable_pj(f) = f * total_pj_p",
        "```",
        "",
        "E1 measured ratios apply to BC, QC, NB and YT. E2c open-data",
        "imputation ratios apply to AB, SK, MB, ON, NS, PE, NL and NT. NU has",
        "zero modelled harvest.",
        "",
        "Tops are not separately modelled. Stem residue is the taper total",
        "from a 0.3 m stump to the tree tip minus recovered logs, so it",
        "already includes stump, top and unmerchantable sections. No tops",
        "number is invented.",
        "",
        "## 3. National totals",
        "",
        "| Component | ODT | PJ | Share of gross |",
        "|---|---:|---:|---:|",
        (
            f"| Stem | {_display(totals['stem_odt'], 3)} | "
            f"{_display(totals['stem_pj'])} | "
            f"{_display(100.0 * totals['stem_share'], 2)}% |"
        ),
        (
            f"| Branch | {_display(totals['branch_odt'], 3)} | "
            f"{_display(totals['branch_pj'])} | "
            f"{_display(100.0 * totals['branch_share'], 2)}% |"
        ),
        (
            f"| Foliage | {_display(totals['foliage_odt'], 3)} | "
            f"{_display(totals['foliage_pj'])} | "
            f"{_display(100.0 * totals['foliage_share'], 2)}% |"
        ),
        (
            f"| **Total** | **{_display(totals['total_odt'], 3)}** | "
            f"**{_display(totals['total_pj'])}** | **100.00%** |"
        ),
        (
            "| Branch+foliage | "
            f"{_display(totals['branch_odt'] + totals['foliage_odt'], 3)} | "
            f"{_display(totals['branch_foliage_pj'])} | "
            f"{_display(100.0 * totals['branch_foliage_share'], 2)}% |"
        ),
        "",
        f"Gross harvest residue is **{_display(totals['total_pj'])} PJ**",
        f"({totals['total_pj']:.6f} PJ unrounded); the display value is",
        f"**{totals['total_pj']:.3f} PJ**. Branch+foliage is",
        f"**{totals['branch_foliage_pj']:.3f} PJ**.",
        "",
        "Uniform recovery scenarios applied to gross total residue:",
        "",
        "| Factor | Recoverable PJ |",
        "|---:|---:|",
    ]
    for factor in RECOVERY_FACTORS:
        key = f"{factor:.2f}"
        lines.append(
            f"| {factor:.2f} | **{_display(recoverable[key])}** |"
        )
    lines.extend(
        [
            "",
            "The recovery denominator is gross total residue",
            f"({_display(totals['total_pj'])} PJ;",
            f"{_display(totals['total_odt'], 3)} ODT).",
            "",
            RECOVERY_NOTE,
            "",
            "## 4. Provincial breakdown",
            "",
            (
                "| Province | Tier | Stem PJ | Branch PJ | Foliage PJ | "
                "Total PJ | Branch+foliage PJ |"
            ),
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.province} | {row.tier} | {row.stem_pj:.3f} | "
            f"{row.branch_pj:.3f} | {row.foliage_pj:.3f} | "
            f"{row.total_pj:.3f} | {row.branch_foliage_pj:.3f} |"
        )
    lines.extend(
        [
            "",
            "The full ODT, PJ, share and source-ratio fields are in",
            "`component_by_province.csv`.",
            "",
            "## 5. Validation",
            "",
            "| Check | Status |",
            "|---|---|",
        ]
    )
    for name, passed in validation["checks"].items():
        lines.append(f"| `{name}` | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "Per-province stem + branch + foliage equals the chain total",
            "within",
            f"{validation['max_component_conservation_error_odt']:.2e} ODT.",
            "The national total is",
            f"{validation['national_total_pj_unrounded']:.6f} PJ.",
            "The maximum absolute province delta against model-card §5.3 is",
            f"{cross_check['max_abs_delta_pj']:.6f} PJ at a",
            f"{cross_check['tolerance_pj']:.3f} PJ tolerance.",
            "",
            "## 6. Caveats and non-claims",
            "",
            "- E2c ratios come from 36-tree synthetic open-data imputation",
            "  lists, not measured provincial components. E1 ratios are",
            "  measured-tree-list values, but branch and foliage dry mass is",
            "  computed from published CFS equations rather than weighed.",
            "- The branch/foliage split is a ratio transfer from the source",
            "  province summary. It is not a new inventory or allometric",
            "  estimate.",
            "- Tops are not separately modelled and no tops number is",
            "  reported. Stem residue includes stump, top and unmerchantable",
            "  sections by construction.",
            "- No environmental, operational, technical, economic or",
            "  accessibility recovery constraint is modelled. The",
            "  0.35/0.60/0.75 values are uniform arithmetic scenarios, and",
            "  0.60 is not calibrated.",
            "- No facility, transport, cost, landbase, tenure, legal, policy",
            "  or national supply claim is made.",
            "- No UI change and no official OSeCAN change is made.",
            "",
            "## 7. Reproduce",
            "",
            "```bash",
            f".venv/bin/python -m {MODULE_PATH}",
            f".venv/bin/pytest -q {TEST_PATH}",
            "```",
            "",
            "Input SHA-256:",
            "",
            "| Input | SHA-256 |",
            "|---|---|",
        ]
    )
    for path, digest in sorted(input_hashes.items()):
        lines.append(f"| `{path}` | `{digest}` |")
    lines.append("")
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    deletion_checker: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Build the component breakdown and return its summary payload."""

    if deletion_checker is None:
        deletion_checker = assert_no_unexpected_deletions
    deletion_checker()
    generated_utc = _utc_now()
    chain = _load_chain()
    ratios = _load_branch_ratios()
    model_card_gross_pj = _parse_model_card_gross_pj()
    rows = _component_rows(chain, ratios)
    totals = _national_totals(rows)
    recoverable = _recoverable_pj(totals["total_pj"])
    cross_check = _model_card_cross_check(rows, model_card_gross_pj)
    validation = _validate(rows, chain, totals, cross_check)
    labels = _labels()
    input_hashes = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in INPUT_PATHS
    }

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target / "component_by_province.csv",
        COMPONENT_FIELDS,
        _component_csv_rows(rows, labels),
    )
    summary = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "scope": (
            "119 Mm3 gross harvest residue split into stem, branch and "
            "foliage; provenance only"
        ),
        "method": {
            "stem": "chain harvest_residue_stem_gross_odt",
            "branch_foliage": "chain harvest_residue_branch_foliage_odt",
            "branch_split": (
                "province branch_odt_expanded / (branch_odt_expanded + "
                "foliage_odt_expanded); foliage is the remainder"
            ),
            "conversion": "PJ = ODT * 18.5 / 1e6",
            "tops": (
                "not separately modelled; stem residue is taper total minus "
                "recovered logs and includes stump, top and unmerchantable "
                "sections"
            ),
        },
        "national_totals": {
            **totals,
            "total_pj_display": round(totals["total_pj"], 3),
            "branch_foliage_pj_display": round(
                totals["branch_foliage_pj"], 3
            ),
        },
        "recovery": {
            "basis": "uniform_fraction_of_gross_total_residue",
            "denominator": "harvest_residue_total_gross_odt",
            "denominator_odt": totals["total_odt"],
            "denominator_pj": totals["total_pj"],
            "reference_fraction": REFERENCE_RECOVERY_FACTOR,
            "recoverable_pj": recoverable,
            "recoverable_pj_display": {
                key: round(value, 3)
                for key, value in recoverable.items()
            },
            "calibrated": False,
            "environmental_or_technical_constraint_modeled": False,
            "notes": RECOVERY_NOTE,
        },
        "model_card_cross_check": cross_check,
        "validation": validation,
        "inputs": input_hashes,
        "labels": labels,
        "caveats": [
            (
                "E2c branch/foliage ratios come from 36-tree synthetic "
                "open-data imputation lists, not measured provincial "
                "components."
            ),
            (
                "E1 branch/foliage ratios use measured tree lists, but dry "
                "mass comes from published CFS equations, not weighed "
                "components."
            ),
            (
                "Tops are not separately modelled; no tops number is "
                "reported. Stem residue includes stump, top and "
                "unmerchantable sections."
            ),
            (
                "No environmental, operational, technical, economic or "
                "accessibility recovery constraint is modelled."
            ),
            "0.60 is a reference recovery scenario, not calibrated.",
            "No UI or official OSeCAN change is made.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    _write_json(target / "summary.json", summary)
    _write_csv(
        target / "provenance.csv",
        PROVENANCE_FIELDS,
        _provenance_rows(generated_utc, input_hashes),
    )
    report = _report(
        rows,
        totals,
        recoverable,
        cross_check,
        validation,
        input_hashes,
        labels,
    )
    (target / REPORT_NAME).write_text(report + "\n", encoding="utf-8")
    outputs = {
        name: {
            "bytes": (target / name).stat().st_size,
            "sha256": _sha256(target / name),
        }
        for name in (
            "component_by_province.csv",
            "summary.json",
            "provenance.csv",
            REPORT_NAME,
        )
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "complete",
        "generated_utc": generated_utc,
        "inputs": input_hashes,
        "outputs": outputs,
        "labels": labels,
        "validation": {
            "all_passed": validation["all_passed"],
            "checks": validation["checks"],
        },
    }
    _write_json(target / "manifest.json", manifest)
    return summary


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ComponentBreakdownError(
            f"cannot inspect git status in {repository}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
    *,
    status_reader: Callable[[Path], str] = _git_status,
) -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in repositories:
        for line in parse_deletion_lines(status_reader(repository)):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise ComponentBreakdownError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "national_total_pj": summary["national_totals"][
                    "total_pj"
                ],
                "branch_foliage_pj": summary["national_totals"][
                    "branch_foliage_pj"
                ],
                "recoverable_0_60_pj": summary["recovery"][
                    "recoverable_pj"
                ]["0.60"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
