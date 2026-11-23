"""Contract tests for the BC interior TIPSY 4.5 industrial sort definitions."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from workflows.national.bucking.bucking import load_grade_rules

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
PUBLISHED_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "tipsy45_industrial_sorts.csv"
)

#: TIPSY 4.5 publishes the deciduous length cell as "--" (no sort-specific
#: minimum).  The TIPSY 4.5 Log Tables page states logs are 2.5 m or longer.
PUBLISHED_DECIDUOUS_BASE_LENGTH_M = 2.5


def _published() -> pd.DataFrame:
    return pd.read_csv(PUBLISHED_PATH)


def test_rule_codes_exactly_match_published_tipsy45_table() -> None:
    rules = load_grade_rules(RULES_PATH)
    published = _published()

    assert len(published) == 20
    assert len(rules.rules) == 20
    assert {rule.sort_code for rule in rules.rules} == set(published["sort_code"])
    assert len({rule.priority for rule in rules.rules}) == len(rules.rules)


def test_rule_dimensions_and_groups_match_published_tipsy45_table() -> None:
    rules = {rule.sort_code: rule for rule in load_grade_rules(RULES_PATH).rules}
    published = _published()

    for row in published.itertuples(index=False):
        rule = rules[row.sort_code]
        assert rule.min_top_cm == float(row.min_top_cm), row.sort_code
        assert rule.tipsy_group == row.group, row.sort_code

        published_length = row.min_length_m
        if isinstance(published_length, str) or math.isnan(float(published_length)):
            assert rule.min_length_m == PUBLISHED_DECIDUOUS_BASE_LENGTH_M, row.sort_code
            assert "2.5 m" in str(row.min_length_note), row.sort_code
            continue
        assert rule.min_length_m == float(published_length), row.sort_code


def test_published_output_code_aliases_are_explicit() -> None:
    published = _published().set_index("sort_code")
    rules = {rule.sort_code: rule for rule in load_grade_rules(RULES_PATH).rules}

    # The published 4.5 table names the 5.0 m pulp sort D125L50, while the
    # TIPSY 4.5/4.7 output column is Industrial_Logs_D125L5.
    assert published.loc["D125L50", "output_code"] == "D125L5"
    assert "D125L50" in rules
    assert "D125L5" not in rules

    # `tipsy_group` records the published processing group.  `product` stays a
    # repository price class because the public price deck has no separate
    # chip'n'saw, gang, or deciduous sawlog/pulp price.  In particular D125L51
    # is Pulp in TIPSY but remains priced as sawlog; changing the price class
    # without a published pulp price for every market group would be a price
    # assumption, not a sort-definition alignment.
    assert rules["D125L51"].tipsy_group == "Pulp"
    assert rules["D125L51"].product == "sawlog"
    assert rules["D125L50"].product == "pulpwood"
    assert rules["D125L63"].tipsy_group == "Chip'n'saw"
    assert rules["D305"].tipsy_group == "Deciduous Sawlog/Peeler"
    assert rules["D152"].tipsy_group == "Deciduous Pulp"


def test_no_rule_is_outside_the_published_set() -> None:
    published_codes = set(_published()["sort_code"])
    rule_codes = {rule.sort_code for rule in load_grade_rules(RULES_PATH).rules}

    assert not rule_codes.difference(published_codes)
