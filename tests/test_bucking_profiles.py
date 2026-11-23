"""Contract tests for named bucking profiles.

The default profile must not change when the separate TIPSY 4.5 profile is
loaded.  The TIPSY profile geometry is checked against the published nominal
lengths and the price scenario must declare the missing FAN$IER numeric table
instead of inventing sort relativities.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nemora.workflows.bucking import DEFAULT_BUCKING_PROFILE

from workflows.national.bucking.profiles import (
    ProfileError,
    available_profile_names,
    load_bucking_profile,
    load_profile_document,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "tipsy45_profile.yaml"


def test_default_profile_is_unchanged() -> None:
    spec = load_bucking_profile("default")
    assert spec.profile is DEFAULT_BUCKING_PROFILE
    assert spec.profile.log_lengths_m == (5.0, 7.5, 10.0)


def test_tipsy45_profile_matches_published_lengths() -> None:
    spec = load_bucking_profile("tipsy45")
    assert spec.profile.log_lengths_m == (13.0, 11.0, 8.0, 6.3, 5.1, 5.0, 2.5)
    assert spec.profile.min_log_length_m == 2.5
    assert spec.profile.min_top_diameter_cm == 12.5
    assert spec.name in available_profile_names()

    # Loading tipsy45 must not mutate the process-wide default.
    assert DEFAULT_BUCKING_PROFILE.log_lengths_m == (5.0, 7.5, 10.0)


def test_tipsy45_price_scenario_does_not_invent_relativities() -> None:
    manifest = load_bucking_profile("tipsy45").manifest()
    scenario = manifest["price_scenario"]
    assert scenario["fansier_numeric_defaults"] == "not_published"
    assert scenario["published_sort_relativities"] is None
    assert manifest["geometry_basis"] == "published_tipsy45_industrial_sort_lengths"


def test_profile_document_records_source_and_no_free_relativities() -> None:
    document = load_profile_document(PROFILE_PATH)
    assert document["profile"] == "tipsy45"
    assert document["log_lengths_m"] == [13.0, 11.0, 8.0, 6.3, 5.1, 5.0, 2.5]
    assert document["price_scenario"]["published_sort_relativities"] is None


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ProfileError, match="unknown bucking profile"):
        load_bucking_profile("not-a-profile")


def test_tampered_lengths_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from workflows.national.bucking import profiles

    tampered = tmp_path / "tipsy45_profile.yaml"
    text = PROFILE_PATH.read_text(encoding="utf-8")
    tampered.write_text(
        text.replace("  - 2.5\n", "  - 2.5\n  - 7.5\n"), encoding="utf-8"
    )
    monkeypatch.setattr(profiles, "_PROFILE_PATH", tampered)
    with pytest.raises(ProfileError, match="must exactly match"):
        load_bucking_profile("tipsy45")
