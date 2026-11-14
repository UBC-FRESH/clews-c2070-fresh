"""Unified v0 plot schema for the FVS-CA calibration extract.

The schema is deliberately narrow: it carries enough information to rebuild
per-visit stand summaries, individual-tree growth intervals, and survival
observations, while preserving the source's own identifiers and expansion
factors.  Nothing in this module invents a cross-source plot join.

Column order below is the on-disk order.  ``trees.csv.gz`` and
``intervals.csv.gz`` are streamed source by source so the build never needs
the full national tree table in memory.
"""

from __future__ import annotations

SITE_COLUMNS: tuple[str, ...] = (
    "source",
    "site_id",
    "province",
    "eco_zone",
    "eco_subzone",
    "eco_variant",
    "site_index",
    "site_index_species",
    "plot_design",
    "plot_area_ha",
    "baf",
    "lat",
    "lon",
    "coord_accuracy",
    "first_year",
    "last_year",
    "n_visits",
    "n_trees",
)

VISIT_COLUMNS: tuple[str, ...] = (
    "source",
    "site_id",
    "visit_id",
    "year",
    "n_trees",
    "ba_m2ha",
    "tph",
    "qmd_cm",
    "top_height_m",
    "volume_m3ha",
    "site_index",
    "age",
)

TREE_COLUMNS: tuple[str, ...] = (
    "source",
    "site_id",
    "visit_id",
    "tree_id",
    "species_code",
    "species_group",
    "dbh_cm",
    "height_m",
    "status",
    "crown_class",
    "expansion_factor",
    "damage_code",
    "is_remeasured",
)

INTERVAL_COLUMNS: tuple[str, ...] = (
    "source",
    "site_id",
    "tree_id",
    "year0",
    "year1",
    "years",
    "dbh0_cm",
    "dbh1_cm",
    "status_end",
    "dead",
    "known",
    "exposure_years",
)

CROSSWALK_COLUMNS: tuple[str, ...] = (
    "source",
    "species_code",
    "species_group",
    "notes",
    "fvs_jsp_code_todo",
)

#: Internal tree columns used to build visits/intervals but not written.
INTERNAL_TREE_COLUMNS: tuple[str, ...] = (
    "_year",
    "_visit_order",
    "_ba_m2ha",
    "_volume_m3ha",
    "_tally",
    "_live",
)

#: Internal visit columns used to build sites but not written.
INTERNAL_VISIT_COLUMNS: tuple[str, ...] = (
    "_visit_order",
    "_plot_area_ha",
    "_baf",
    "_plot_design",
    "_site_index_species",
)

#: Validation thresholds.  These match fresh-fvsbc's calibration defaults.
MIN_INTERVAL_YEARS: int = 2
MAX_INTERVAL_YEARS: int = 40
MIN_YEAR: int = 1900
MAX_YEAR: int = 2027
MIN_DBH_CM: float = 0.0
MAX_DBH_CM: float = 500.0
MIN_HEIGHT_M: float = 0.0
MAX_HEIGHT_M: float = 100.0

STATUS_VALUES: tuple[str, ...] = ("live", "dead", "missing")
SPECIES_GROUPS: tuple[str, ...] = ("conifer", "deciduous", "unknown")

SOURCE_LABELS: tuple[str, ...] = (
    "faib_psp",
    "faib_non_psp",
    "magplot",
    "magplot_wfs",
    "qc_pep",
    "nl_fal",
)
