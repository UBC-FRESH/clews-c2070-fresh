"""Named bucking profiles kept separate from the repository default.

The default profile is ``nemora.workflows.bucking.DEFAULT_BUCKING_PROFILE``
(``log_lengths_m=(5.0, 7.5, 10.0)``).  This module adds a TIPSY 4.5-compatible
profile without mutating that default.  The profile geometry and its price
scenario are data files beside the dimension rules so a run can be audited
without reading Python constants.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from nemora.workflows.bucking import DEFAULT_BUCKING_PROFILE, BuckingProfile

__all__ = [
    "DEFAULT_PROFILE_NAME",
    "ProfileError",
    "ProfileSpec",
    "available_profile_names",
    "load_bucking_profile",
    "load_profile_document",
]

DEFAULT_PROFILE_NAME = "default"
TIPSY45_PROFILE_NAME = "tipsy45"
_PROFILE_DIR = Path(__file__).resolve().parent / "rules"
_PROFILE_PATH = _PROFILE_DIR / "tipsy45_profile.yaml"

#: Exact published nominal lengths for the TIPSY 4.5 industrial sorts.
TIPSY45_LOG_LENGTHS_M: tuple[float, ...] = (13.0, 11.0, 8.0, 6.3, 5.1, 5.0, 2.5)
TIPSY45_MIN_LOG_LENGTH_M = 2.5
TIPSY45_MIN_TOP_DIAMETER_CM = 12.5


class ProfileError(RuntimeError):
    """Raised when a named profile is unknown or violates its contract."""


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    """A trusted profile plus the provenance needed in a run manifest."""

    name: str
    profile: BuckingProfile
    document: Mapping[str, Any]
    source_path: Path
    source_sha256: str

    def manifest(self) -> dict[str, Any]:
        geometry = {
            "log_lengths_m": list(self.profile.log_lengths_m),
            "min_top_diameter_cm": self.profile.min_top_diameter_cm,
            "min_log_length_m": self.profile.min_log_length_m,
            "stump_height_m": self.profile.stump_height_m,
            "section_m": self.profile.section_m,
            "allow_partial_top": self.profile.allow_partial_top,
        }
        return {
            "name": self.name,
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "geometry": geometry,
            "geometry_basis": self.document.get("geometry_basis"),
            "price_basis": self.document.get("price_basis"),
            "price_scenario": self.document.get("price_scenario"),
        }


def available_profile_names() -> tuple[str, ...]:
    return (DEFAULT_PROFILE_NAME, TIPSY45_PROFILE_NAME)


def load_profile_document(path: Path | None = None) -> dict[str, Any]:
    """Load the TIPSY 4.5 profile document and fail on malformed data."""
    profile_path = Path(path) if path is not None else _PROFILE_PATH
    if not profile_path.exists():
        raise ProfileError(f"bucking profile not found: {profile_path}")
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProfileError(f"bucking profile must be a mapping: {profile_path}")
    return payload


def _require_positive_number(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfileError(f"profile {name} must be numeric: {value!r}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ProfileError(f"profile {name} must be positive and finite: {value!r}")
    return number


def _tipsy45_profile(document: Mapping[str, Any], source_path: Path) -> ProfileSpec:
    lengths = document.get("log_lengths_m")
    if not isinstance(lengths, list):
        raise ProfileError("tipsy45 profile requires a log_lengths_m list")
    parsed_lengths = tuple(_require_positive_number("log_lengths_m", value) for value in lengths)
    if parsed_lengths != TIPSY45_LOG_LENGTHS_M:
        raise ProfileError(
            "tipsy45 profile log_lengths_m must exactly match the published "
            f"TIPSY 4.5 nominal lengths {TIPSY45_LOG_LENGTHS_M}; got {parsed_lengths}"
        )
    min_top = _require_positive_number(
        "min_top_diameter_cm", document.get("min_top_diameter_cm")
    )
    if min_top != TIPSY45_MIN_TOP_DIAMETER_CM:
        raise ProfileError(
            f"tipsy45 min_top_diameter_cm must be {TIPSY45_MIN_TOP_DIAMETER_CM}"
        )
    min_length = _require_positive_number(
        "min_log_length_m", document.get("min_log_length_m")
    )
    if min_length != TIPSY45_MIN_LOG_LENGTH_M:
        raise ProfileError(
            f"tipsy45 min_log_length_m must be {TIPSY45_MIN_LOG_LENGTH_M}"
        )

    price_scenario = document.get("price_scenario")
    if not isinstance(price_scenario, Mapping):
        raise ProfileError("tipsy45 profile requires a price_scenario mapping")
    if price_scenario.get("fansier_numeric_defaults") != "not_published":
        raise ProfileError(
            "tipsy45 profile must record that FAN$IER numeric default log prices "
            "are not published"
        )
    if price_scenario.get("published_sort_relativities") is not None:
        raise ProfileError(
            "tipsy45 profile must not carry unpublished sort relativities; "
            "declare published_sort_relativities: null"
        )

    profile = BuckingProfile(
        log_lengths_m=parsed_lengths,
        min_top_diameter_cm=min_top,
        min_log_length_m=min_length,
        stump_height_m=_require_positive_number(
            "stump_height_m", document.get("stump_height_m")
        ),
        section_m=_require_positive_number("section_m", document.get("section_m")),
        allow_partial_top=bool(document.get("allow_partial_top", False)),
    )
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return ProfileSpec(
        name=TIPSY45_PROFILE_NAME,
        profile=profile,
        document=dict(document),
        source_path=source_path,
        source_sha256=digest,
    )


def _default_spec() -> ProfileSpec:
    document = {
        "profile": DEFAULT_PROFILE_NAME,
        "status": "repository_default",
        "geometry_basis": "nemora DEFAULT_BUCKING_PROFILE",
        "price_basis": "published_product_class_prices",
        "price_scenario": {
            "name": "published_product_class_only",
            "evidence": "published_table",
            "fansier_numeric_defaults": "not_applicable_default_profile",
            "published_sort_relativities": None,
        },
    }
    return ProfileSpec(
        name=DEFAULT_PROFILE_NAME,
        profile=DEFAULT_BUCKING_PROFILE,
        document=document,
        source_path=Path("nemora.workflows.bucking.DEFAULT_BUCKING_PROFILE"),
        source_sha256="",
    )


def load_bucking_profile(name: str = DEFAULT_PROFILE_NAME) -> ProfileSpec:
    """Return a trusted named profile; unknown names fail closed."""
    key = str(name).strip().lower()
    if key == DEFAULT_PROFILE_NAME:
        return _default_spec()
    if key == TIPSY45_PROFILE_NAME:
        document = load_profile_document()
        if str(document.get("profile", "")).strip().lower() != TIPSY45_PROFILE_NAME:
            raise ProfileError(
                f"tipsy45 profile document names {document.get('profile')!r}"
            )
        return _tipsy45_profile(document, _PROFILE_PATH)
    raise ProfileError(
        f"unknown bucking profile {name!r}; expected one of "
        f"{', '.join(available_profile_names())}"
    )
