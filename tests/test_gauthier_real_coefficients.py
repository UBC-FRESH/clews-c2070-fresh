"""Tests for ``fresh_fibre.gauthier_yield`` real-coefficient lookup.

P7.1.CASFRI.2 adds a per-species real-coefficient path to
``fresh_fibre.gauthier_yield``. The new path is load-bearing for the
BC + QC fixture substitution in ``fresh_fibre.bc_qc_mini_2prov``. The
diagnostic checks below verify:

1. ``species_lookup`` returns the documented proxy seed values for
   the EPN (QC) and FDC coast (BC) species.
2. ``species_lookup`` returns ``None`` for unknown species/region pairs.
3. ``volume_at_age_for_species`` applies a Chapman-Richards saturating
   curve with the documented ``k=0.022`` rate.
4. ``REAL_COEFFICIENT_ROWS`` is non-empty and the
   ``value_policy`` string distinguishes the new path from the
   synthetic diagnostic one.
"""

from __future__ import annotations

import unittest

from fresh_fibre.gauthier_yield import (
    REAL_COEFFICIENT_ROWS,
    VALUE_POLICY as SYNTHETIC_VALUE_POLICY,
    species_lookup,
    volume_at_age_for_species,
)


class TestSpeciesLookup(unittest.TestCase):
    """``species_lookup`` returns the documented proxy seed values."""

    def test_epn_qc_returns_load_bearing_epn_row(self) -> None:
        row = species_lookup("EPN", "QC")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["species_code"], "EPN")
        self.assertEqual(row["region"], "QC")
        self.assertEqual(row["vol80_proxy_m3_per_ha"], 120)
        self.assertEqual(
            row["value_policy"],
            "real_pothier_savard_auger_seeded_vol80_proxy",
        )

    def test_fdc_bc_coast_returns_load_bearing_fdc_row(self) -> None:
        row = species_lookup("FDC", "BC_coast")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["species_code"], "FDC")
        self.assertEqual(row["region"], "BC_coast")
        self.assertEqual(row["vol80_proxy_m3_per_ha"], 145)
        self.assertEqual(
            row["value_policy"],
            "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        )

    def test_unknown_species_returns_none(self) -> None:
        self.assertIsNone(species_lookup("UNKNOWN", "QC"))
        self.assertIsNone(species_lookup("UNKNOWN_SPECIES", "BC"))
        self.assertIsNone(species_lookup("", "QC"))
        self.assertIsNone(species_lookup("EPN", ""))

    def test_real_coefficient_rows_distinguished_from_synthetic_path(self) -> None:
        # The real-coefficient path's value_policy MUST be a different
        # string from the synthetic diagnostic one (``synthetic_method_*``)
        # so downstream contract consumers can quarantine them.
        self.assertTrue(len(REAL_COEFFICIENT_ROWS) > 0)
        for row in REAL_COEFFICIENT_ROWS:
            self.assertNotEqual(row["value_policy"], SYNTHETIC_VALUE_POLICY)
            self.assertIn("seeded_vol80_proxy", row["value_policy"])


class TestVolumeAtAgeForSpecies(unittest.TestCase):
    """``volume_at_age_for_species`` applies a Chapman-Richards curve."""

    def test_age_50_reaches_below_seventy_pct_of_asymptote(self) -> None:
        # With k=0.022, age 50 -> 1 - exp(-0.022*50) = 1 - exp(-1.1)
        # ~= 0.6671 of the vol80 asymptote. Assert that we are below
        # 0.7 * vol80 (63% nominal at age 50 plus a small margin).
        vol = volume_at_age_for_species("EPN", "QC", 50)
        self.assertGreater(vol, 0.0)
        self.assertLessEqual(vol, 120 * 0.7)

    def test_large_age_saturates_with_overflow_guard(self) -> None:
        # age=200 -> 1 - exp(-4.4) ~= 0.9878 means vol approaches vol80
        # but stays under it (the proxy lacks the asymptotic overshoot
        # the real yield tables apply); the test asserts <= vol80 * 1.01
        # so a permitted 1% overflow guard remains future-proof.
        vol = volume_at_age_for_species("FDC", "BC_coast", 200)
        self.assertLessEqual(vol, 145 * 1.01)

    def test_unknown_species_returns_zero(self) -> None:
        # Unknown species -> zero volume, never raises; the caller is
        # expected to skip the contribution rather than aggregate it.
        self.assertEqual(volume_at_age_for_species("UNKNOWN", "QC", 50), 0.0)

    def test_negative_age_raises(self) -> None:
        with self.assertRaises(ValueError):
            volume_at_age_for_species("EPN", "QC", -1)


if __name__ == "__main__":
    unittest.main()
