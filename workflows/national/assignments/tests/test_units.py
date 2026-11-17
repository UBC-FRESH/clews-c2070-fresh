"""Tests for the Stage A declared unit contract."""

from __future__ import annotations

import math
import unittest

from workflows.national.assignments import units


class UnitConversionTests(unittest.TestCase):
    def test_m3_odt_round_trip_at_default_density(self) -> None:
        self.assertAlmostEqual(units.convert(2.0, "m3", "odt"), 0.9, places=12)
        self.assertAlmostEqual(units.convert(0.9, "odt", "m3"), 2.0, places=12)
        self.assertAlmostEqual(
            units.to_m3_green(1.0, "bdmt"), 1.0 / 0.45, places=12
        )
        self.assertAlmostEqual(
            units.from_m3_green(1.0 / 0.45, "bdmt"), 1.0, places=12
        )

    def test_per_grade_density_override(self) -> None:
        book = units.DensityBook(
            default_odt_m3=0.45,
            by_grade={"RESIDUE": 0.50},
            by_species={"SW": 0.36},
        )
        self.assertEqual(book.resolve(grade_id="RESIDUE")[0], 0.50)
        self.assertEqual(book.resolve(species="SW")[0], 0.36)
        self.assertEqual(book.resolve(grade_id="SAWLOG", species="SW")[0], 0.36)
        self.assertEqual(book.resolve()[0], 0.45)
        context = units.ConversionContext.from_density_book(
            book, grade_id="RESIDUE"
        )
        self.assertAlmostEqual(
            units.convert(1.0, "odt", "m3", context=context), 2.0, places=12
        )

    def test_admt_requires_declared_moisture(self) -> None:
        with self.assertRaises(units.AmbiguousConversionError):
            units.convert(1.0, "admt", "odt")
        context = units.ConversionContext(admt_moisture_wet_basis=0.10)
        self.assertAlmostEqual(
            units.convert(1.0, "admt", "odt", context=context), 0.90, places=12
        )
        self.assertAlmostEqual(
            units.convert(0.90, "odt", "admt", context=context), 1.0, places=12
        )

    def test_mbf_requires_recovery_factor(self) -> None:
        with self.assertRaises(units.MissingConversionFactorError):
            units.convert(1.0, "mbf", "m3")
        context = units.ConversionContext(
            recovery_factor_m3_per_mbf=0.25,
            recovery_source="declared test recovery",
        )
        self.assertAlmostEqual(
            units.convert(1.0, "mbf", "m3", context=context), 0.25, places=12
        )
        self.assertAlmostEqual(
            units.convert(0.25, "m3", "mbf", context=context), 1.0, places=12
        )
        self.assertAlmostEqual(units.convert(1.0, "mbf", "bf"), 1000.0, places=12)
        self.assertAlmostEqual(
            units.convert(1.0, "m3", "bf", context=context), 4000.0, places=12
        )

    def test_energy_conversions_and_volume_rejection(self) -> None:
        self.assertAlmostEqual(units.convert(1.0, "kwh", "mwh"), 0.001, places=12)
        self.assertAlmostEqual(units.convert(1.0, "mwh", "gj"), 3.6, places=12)
        self.assertAlmostEqual(
            units.convert(3.6, "gj", "mwh"), 1.0, places=12
        )
        with self.assertRaises(units.UnsupportedConversionError):
            units.convert(1.0, "kwh", "m3")
        with self.assertRaises(units.UnsupportedConversionError):
            units.convert(1.0, "gj", "odt")

    def test_ambiguous_and_unknown_units_fail_loudly(self) -> None:
        for unit in ("m3_odt", "m3_solid", "m3_loose", "kg", "tonne"):
            with self.assertRaises(units.UnitError):
                units.canonical_unit(unit)
        with self.assertRaises(units.UnsupportedUnitError):
            units.convert(1.0, "t", "m3")
        context = units.ConversionContext(allow_archive_tonne=True)
        self.assertAlmostEqual(
            units.convert(1.0, "t", "m3", context=context), 1.0 / 0.45, places=12
        )

    def test_round_trip_for_all_declared_pairs(self) -> None:
        pairs = (
            ("m3", "odt"),
            ("odt", "m3"),
            ("bdmt", "odt"),
            ("kwh", "mwh"),
            ("mwh", "gj"),
            ("mbf", "bf"),
        )
        for source, target in pairs:
            value = 1.7
            converted = units.convert(value, source, target)
            restored = units.convert(converted, target, source)
            self.assertTrue(
                math.isclose(restored, value, rel_tol=1e-12, abs_tol=1e-12),
                f"{source}<->{target} did not round-trip: {restored}",
            )

    def test_registry_marks_ambiguous_units_rejected(self) -> None:
        registry = {row["unit"]: row for row in units.unit_registry_rows()}
        self.assertEqual(registry["m3_odt"]["accepted"], "no")
        self.assertEqual(registry["m3"]["accepted"], "alias_of_m3_green")
        self.assertEqual(registry["bdmt"]["accepted"], "yes")


if __name__ == "__main__":
    unittest.main()
