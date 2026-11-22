"""Quebec Schneider (2013) stem-taper adapter for the national contract.

The implemented equation is the deterministic tree-level model from
Fortin, Schneider and Saucier (2013), as distributed in the LGPL-3.0
``CFSForestTools`` Java library (``schneider2013``).  Only the fixed-effect
tree model is used: no plot or tree random effects, no stochastic residual
draws, and no hybrid-model covariates (drainage, potential vegetation,
climatic sub-domain, stand density).  That keeps the adapter runnable from a
tree list that carries only species, DBH and height.

The model predicts squared under-bark diameter (mm2) as

``d2 = alpha * D_cm^2 * 100 * z * x^(2 - exponent)``

where ``z = (H - h) / (H - 1.3)``, ``x = h / 1.3`` and ``exponent`` is a
species-specific linear expression in z, log(z), exp(h/H), h/H, log(h/H),
DBH (mm) and H.  ``PEG`` uses the published nonlinear exponent instead of the
linear expression.  Coefficients are the published parameter estimates, not
refitted values.

This is a Quebec taper.  Applying it to a tree outside Quebec is a documented
cross-jurisdiction transfer and must not be reported as a local taper.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_STUMP_HEIGHT_M",
    "Schneider2013Taper",
    "SchneiderTaperRegistry",
    "TaperRegistryError",
    "load_taper_registry",
]

DEFAULT_STUMP_HEIGHT_M = 0.3
DEFAULT_INTEGRATION_INTERVALS = 400

#: Allowed term names in the registry CSV.  The names mirror the published
#: model effects, not arbitrary curve-fitting labels.
TERM_NAMES: tuple[str, ...] = (
    "core",
    "logcore",
    "expr",
    "r",
    "lnr",
    "dbhmm",
    "height",
    "rcore",
    "peg_b1",
    "peg_b2",
    "peg_b3",
)

SOURCE = (
    "Fortin, M., Schneider, R., Saucier, J.-P. 2013. Volume and Error Variance "
    "Estimation Using Integrated Stem Taper Models. Forest Science 59(3):345-358. "
    "doi:10.5849/forsci.11-146; fixed-effect tree model parameters as distributed "
    "in CFSForestTools (LGPL-3.0)."
)
LICENSE = "LGPL-3.0 (CFSForestTools parameter distribution); equation published"


class TaperRegistryError(ValueError):
    """Raised when a taper key or tree domain is not covered."""


@dataclass(frozen=True, slots=True)
class Schneider2013Taper:
    """One fixed-effect Quebec taper, keyed by species code."""

    jurisdiction: str
    species: str
    common_name: str
    alpha: float
    terms: tuple[tuple[str, float], ...]
    intercept: float
    dbh_min_cm: float
    dbh_max_cm: float
    height_min_m: float
    height_max_m: float
    equation: str = "schneider2013_tree_fixed"
    authority: str = "Fortin, Schneider and Saucier (2013)"
    source: str = SOURCE
    license: str = LICENSE
    evidence: str = "published_table"
    version: str = "cfsforesttools-schneider2013-v1"

    def __post_init__(self) -> None:
        if self.evidence != "published_table":
            raise TaperRegistryError("Schneider 2013 adapter is published_table")
        if not self.species or not self.jurisdiction:
            raise TaperRegistryError("jurisdiction and species are required")
        if not math.isfinite(self.alpha) or self.alpha <= 0.0:
            raise TaperRegistryError("alpha must be finite and positive")
        if not math.isfinite(self.intercept):
            raise TaperRegistryError("intercept must be finite")
        for name, coefficient in self.terms:
            if name not in TERM_NAMES:
                raise TaperRegistryError(f"unsupported taper term {name!r}")
            if not math.isfinite(coefficient):
                raise TaperRegistryError(f"term {name!r} must be finite")
        if not (0.0 < self.dbh_min_cm < self.dbh_max_cm):
            raise TaperRegistryError("invalid DBH domain")
        if not (1.3 < self.height_min_m < self.height_max_m):
            raise TaperRegistryError("invalid height domain")

    @property
    def taper_id(self) -> str:
        return f"{self.jurisdiction}-SCHNEIDER2013-{self.species}"

    def _validate_tree(self, dbh_cm: float, height_m: float) -> tuple[float, float]:
        dbh = float(dbh_cm)
        height = float(height_m)
        if not math.isfinite(dbh) or dbh <= 0.0:
            raise TaperRegistryError(f"dbh_cm must be finite and positive, got {dbh_cm!r}")
        if not math.isfinite(height) or height <= 1.3:
            raise TaperRegistryError(f"height_m must exceed 1.3 m, got {height_m!r}")
        if not self.dbh_min_cm <= dbh <= self.dbh_max_cm:
            raise TaperRegistryError(
                f"{self.species}: DBH {dbh:.1f} cm outside fitted domain "
                f"[{self.dbh_min_cm:.1f}, {self.dbh_max_cm:.1f}]"
            )
        if not self.height_min_m <= height <= self.height_max_m:
            raise TaperRegistryError(
                f"{self.species}: height {height:.1f} m outside fitted domain "
                f"[{self.height_min_m:.1f}, {self.height_max_m:.1f}]"
            )
        return dbh, height

    def _exponent(self, dbh_cm: float, height_m: float, h_m: float) -> float:
        z = (height_m - h_m) / (height_m - 1.3)
        r = h_m / height_m
        if self.species == "PEG":
            # Published nonlinear PEG exponent: -exp(-b1 * r^b2) + b3.
            (_, b1), (_, b2), (_, b3) = self.terms
            return -math.exp(-b1 * (r**b2)) + b3
        values = {
            "core": z,
            "logcore": math.log(z),
            "expr": math.exp(r),
            "r": r,
            "lnr": math.log(r),
            "dbhmm": dbh_cm * 10.0,
            "height": height_m,
            "rcore": r * z,
        }
        return self.intercept + sum(coefficient * values[name] for name, coefficient in self.terms)

    def diameter_inside_bark_cm(self, dbh_cm: float, height_m: float, h_m: float) -> float:
        """Under-bark diameter at ``h_m`` above ground, in centimetres."""
        dbh, height = self._validate_tree(dbh_cm, height_m)
        h = float(h_m)
        if not math.isfinite(h) or h < 0.0 or h > height:
            raise TaperRegistryError(f"height section {h_m!r} is outside [0, {height:.2f}]")
        if h >= height - 1e-9:
            return 0.0
        z = (height - h) / (height - 1.3)
        if z <= 0.0:
            return 0.0
        x = h / 1.3
        if x <= 0.0:
            return 0.0
        exponent = self._exponent(dbh, height, h)
        squared_mm2 = self.alpha * dbh * dbh * 100.0 * z * (x ** (2.0 - exponent))
        if not math.isfinite(squared_mm2) or squared_mm2 <= 0.0:
            return 0.0
        return math.sqrt(squared_mm2) / 10.0

    def volume_between_m3(
        self,
        dbh_cm: float,
        height_m: float,
        start_m: float,
        end_m: float,
        *,
        intervals: int = 64,
    ) -> float:
        """Inside-bark volume between two heights, by Simpson integration."""
        dbh, height = self._validate_tree(dbh_cm, height_m)
        start = max(0.0, float(start_m))
        end = min(height, float(end_m))
        if end <= start:
            return 0.0
        if intervals < 2:
            raise ValueError("intervals must be at least 2")
        if intervals % 2:
            intervals += 1
        step = (end - start) / intervals
        total = 0.0
        for index in range(intervals + 1):
            h = start + index * step
            diameter = self.diameter_inside_bark_cm(dbh, height, h)
            weight = 1.0 if index in (0, intervals) else (4.0 if index % 2 else 2.0)
            total += weight * diameter * diameter
        # diameter is cm; cm2 -> m2 is 1e-4.
        return math.pi / 4.0 * (step / 3.0) * total * 1e-4

    def total_volume_m3(
        self,
        dbh_cm: float,
        height_m: float,
        *,
        stump_height_m: float = DEFAULT_STUMP_HEIGHT_M,
        intervals: int = DEFAULT_INTEGRATION_INTERVALS,
    ) -> float:
        return self.volume_between_m3(
            dbh_cm, height_m, stump_height_m, height_m, intervals=intervals
        )

    def manifest_record(self) -> dict[str, object]:
        return {
            "taper_id": self.taper_id,
            "jurisdiction": self.jurisdiction,
            "species": self.species,
            "common_name": self.common_name,
            "equation": self.equation,
            "alpha": self.alpha,
            "terms": [{"term": name, "coefficient": value} for name, value in self.terms],
            "intercept": self.intercept,
            "dbh_domain_cm": [self.dbh_min_cm, self.dbh_max_cm],
            "height_domain_m": [self.height_min_m, self.height_max_m],
            "authority": self.authority,
            "source": self.source,
            "license": self.license,
            "evidence": self.evidence,
            "version": self.version,
        }


class SchneiderTaperRegistry:
    """Explicit dispatcher keyed by ``(jurisdiction, species)``."""

    def __init__(self, models: Iterable[Schneider2013Taper]) -> None:
        self._models: dict[tuple[str, str], Schneider2013Taper] = {}
        for model in models:
            key = (model.jurisdiction.upper(), model.species.upper())
            if key in self._models:
                raise TaperRegistryError(f"duplicate taper key {key}")
            self._models[key] = model

    def resolve(self, jurisdiction: str, species: str) -> Schneider2013Taper:
        key = (str(jurisdiction).strip().upper(), str(species).strip().upper())
        model = self._models.get(key)
        if model is None:
            supported = ", ".join(f"{j}/{s}" for j, s in sorted(self._models))
            raise TaperRegistryError(
                f"no taper registered for {key[0]}/{key[1]}; registered keys: {supported}"
            )
        return model

    @property
    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._models))

    def models(self) -> tuple[Schneider2013Taper, ...]:
        return tuple(self._models[key] for key in sorted(self._models))

    def manifest_records(self) -> list[dict[str, object]]:
        return [self._models[key].manifest_record() for key in sorted(self._models)]


def _parse_terms(text: str) -> tuple[tuple[str, float], ...]:
    terms: list[tuple[str, float]] = []
    for chunk in str(text).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if name not in TERM_NAMES:
            raise TaperRegistryError(f"unsupported term {name!r} in taper registry")
        terms.append((name, float(value)))
    if not terms:
        raise TaperRegistryError("taper registry row has no terms")
    return tuple(terms)


def load_taper_registry(path: Path) -> SchneiderTaperRegistry:
    """Load the versioned taper registry CSV."""
    registry_path = Path(path)
    if not registry_path.exists():
        raise FileNotFoundError(f"taper registry not found: {registry_path}")
    models: list[Schneider2013Taper] = []
    with registry_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            models.append(
                Schneider2013Taper(
                    jurisdiction=str(row["jurisdiction"]).strip().upper(),
                    species=str(row["species"]).strip().upper(),
                    common_name=str(row["common_name"]).strip(),
                    alpha=float(row["alpha"]),
                    terms=_parse_terms(row["terms"]),
                    intercept=float(row["intercept"]),
                    dbh_min_cm=float(row["dbh_min_cm"]),
                    dbh_max_cm=float(row["dbh_max_cm"]),
                    height_min_m=float(row["height_min_m"]),
                    height_max_m=float(row["height_max_m"]),
                    equation=str(row.get("equation") or "schneider2013_tree_fixed"),
                    authority=str(row.get("authority") or "Fortin, Schneider and Saucier (2013)"),
                    source=(
                        f"{row.get('authority') or 'Fortin, Schneider and Saucier (2013)'!s}; "
                        f"parameters: {row.get('source') or 'CFSForestTools schneider2013'!s}"
                    ),
                    license=str(row.get("license") or LICENSE),
                    evidence=str(row.get("evidence") or "published_table"),
                    version=str(row.get("version") or "cfsforesttools-schneider2013-v1"),
                )
            )
    if not models:
        raise TaperRegistryError(f"taper registry is empty: {registry_path}")
    return SchneiderTaperRegistry(models)


def registry_records(registry: SchneiderTaperRegistry) -> list[Mapping[str, object]]:
    """Flat records suitable for a CSV taper registry."""
    records: list[Mapping[str, object]] = []
    for model in registry.models():
        records.append(
            {
                "taper_id": model.taper_id,
                "jurisdiction": model.jurisdiction,
                "species": model.species,
                "common_name": model.common_name,
                "equation": model.equation,
                "alpha": model.alpha,
                "terms": ";".join(f"{name}={value}" for name, value in model.terms),
                "intercept": model.intercept,
                "dbh_min_cm": model.dbh_min_cm,
                "dbh_max_cm": model.dbh_max_cm,
                "height_min_m": model.height_min_m,
                "height_max_m": model.height_max_m,
                "authority": model.authority,
                "source": model.source,
                "license": model.license,
                "evidence": model.evidence,
                "version": model.version,
            }
        )
    return records
