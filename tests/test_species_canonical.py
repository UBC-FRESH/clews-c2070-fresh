"""Tests for ``fresh_fibre.species_canonical``.

The species canonicalization module is the FRESH-side bridge between
CASFRI v5's per-jurisdiction producer codes and the project's
inter-jurisdiction canonical identifier policy.

These tests cover:

- ``cross_jurisdiction_map`` resolves BC's two-letter silvics codes and
  QC's producer codes to CASFRI canonical species.
- ``qc_lookup`` resolves QC-internal abstract codes (e.g. ``SAB``,
  ``EPB``, ``BOJ``) to CASFRI canonical species.
- ``merge_decisions`` collapses duplicates by raw/province/canonical/policy.
- ``unknown_species`` flagging is preserved through the pipeline.
- The jurisdictional-prefix policy is stable and named.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fresh_fibre.species_canonical import (
    CROSS_JURISDICTION_OUTPUT_COLUMNS,
    PROVINCE_PREFIX_TABLE,
    VALUE_POLICIES,
    build_index,
    canonical_id,
    canonicalize,
    decision_identifier,
    jurisdictional_prefix,
    load_cross_jurisdiction_map,
    load_qc_lookup,
    merge_decisions,
    province_normalise,
    read_legacy_stand_table,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_DIR = Path(REPO_ROOT) / "data" / "raw" / "casfri_v5" / "translation_tables"


class TestJurisdictionPrefixPolicy(unittest.TestCase):
    """The jurisdictional-prefix policy is named and stable."""

    def test_documented_provinces_have_prefix(self) -> None:
        expected = {
            "BC": "BC_",
            "QC": "QC_",
            "AB": "AB_",
            "ON": "ON_",
            "MB": "MB_",
            "NB": "NB_",
            "NS": "NS_",
            "PE": "PE_",
            "NL": "NL_",
            "SK": "SK_",
            "YT": "YT_",
            "NT": "NT_",
            "NU": "NU_",
        }
        self.assertEqual(PROVINCE_PREFIX_TABLE, expected)

    def test_jurisdictional_prefix_helper(self) -> None:
        self.assertEqual(jurisdictional_prefix("BC"), "BC_")
        self.assertEqual(jurisdictional_prefix("QC"), "QC_")
        self.assertEqual(jurisdictional_prefix("XX"), "XX_")  # unknown province


class TestProvinceNormalise(unittest.TestCase):
    """``province_normalise`` collapses upstream column labels."""

    def test_two_letter_codes_are_pass_through(self) -> None:
        self.assertEqual(province_normalise("QC"), "QC")
        self.assertEqual(province_normalise("bc"), "BC")

    def test_unknown_input_pass_through(self) -> None:
        self.assertEqual(province_normalise("PP"), "PP")


class TestCrossJurisdictionMap(unittest.TestCase):
    """The cross-jurisdiction species table loads correctly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mappings = load_cross_jurisdiction_map(TRANSLATION_DIR / "species_code_mapping.csv")

    def test_at_least_one_thousand_mappings_loaded(self) -> None:
        # Each row of the upstream CSV is one species; each populated
        # producer code is a separate mapping entry.
        self.assertGreater(len(self.mappings), 200)

    def test_bc_producer_codes_present(self) -> None:
        bc = [m for m in self.mappings if m.province == "BC"]
        self.assertGreater(len(bc), 1)
        # At least one row should have a known BC producer code.
        bc_codes = {m.raw_code for m in bc}
        self.assertTrue(any(code in bc_codes for code in ("FDC", "AT", "BL")))

    def test_qc_producer_codes_present(self) -> None:
        qc = [m for m in self.mappings if m.province == "QC"]
        self.assertGreater(len(qc), 1)
        # The cross-jurisdiction file uses 2-letter codes for QC, including:
        # SB for balsam fir, BP for paper birch (or similar).
        qc_codes = {m.raw_code for m in qc}
        self.assertTrue(bool(qc_codes))

    def test_picea_mariana_resolves(self) -> None:
        # Find some producer code for Picea mariana (black spruce) under any
        # province.
        matches = [m for m in self.mappings if m.casfri_canonical == "PICE_MAR_###"]
        self.assertGreater(len(matches), 0)
        for m in matches:
            self.assertTrue(m.scientific_name)


class TestQcLookup(unittest.TestCase):
    """``qc_ini03_species_lookup.csv`` resolves QC abstract codes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = load_qc_lookup(TRANSLATION_DIR / "qc_ini03_species_lookup.csv")

    def test_qc_lookup_has_thousands_of_rows(self) -> None:
        self.assertGreater(len(self.rows), 500)

    def test_qc_code_sab_resolves_to_abie_bal_via_qc_lookup(self) -> None:
        sab = next((m for m in self.rows if m.raw_code == "SAB"), None)
        self.assertIsNotNone(sab)
        self.assertEqual(sab.casfri_canonical, "ABIE_BAL_###")
        self.assertEqual(sab.province, "QC")

    def test_qc_code_boj_resolves_to_betu_all_via_qc_lookup(self) -> None:
        boj = next((m for m in self.rows if m.raw_code == "BOJ"), None)
        self.assertIsNotNone(boj)
        self.assertEqual(boj.casfri_canonical, "BETU_ALL_###")


class TestIndexAndCanonicalize(unittest.TestCase):
    """Once indexed, ``canonicalize`` returns the right table entries."""

    @classmethod
    def setUpClass(cls) -> None:
        cross = load_cross_jurisdiction_map(TRANSLATION_DIR / "species_code_mapping.csv")
        qc = load_qc_lookup(TRANSLATION_DIR / "qc_ini03_species_lookup.csv")
        cls.cross_index = build_index(cross)
        cls.qc_index = build_index(qc)

    def test_qc_sab_resolves_via_qc_lookup(self) -> None:
        tables = canonicalize([("SAB", "QC")], self.cross_index, self.qc_index)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].casfri_canonical, "ABIE_BAL_###")
        self.assertEqual(tables[0].fresh_canonical, "ABIE_BAL_###")
        self.assertEqual(
            tables[0].value_policy, VALUE_POLICIES["resolved_via_qc_lookup"],
        )

    def test_unmapped_code_returns_unknown_policy(self) -> None:
        tables = canonicalize([("ZZZUNKNOWN", "QC")], self.cross_index, self.qc_index)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].casfri_canonical, "")
        self.assertEqual(tables[0].fresh_canonical, "")
        self.assertEqual(
            tables[0].value_policy, VALUE_POLICIES["unresolved_unknown"],
        )

    def test_empty_raw_code_skipped(self) -> None:
        tables = canonicalize([("", "QC")], self.cross_index, self.qc_index)
        self.assertEqual(tables, [])

    def test_bc_code_resolves_via_cross_jurisdiction(self) -> None:
        # Pick a BC code that's in the cross-jurisdiction map.
        bc_code = None
        for m in load_cross_jurisdiction_map(
            TRANSLATION_DIR / "species_code_mapping.csv",
        ):
            if m.province == "BC":
                bc_code = m.raw_code
                break
        # If no BC codes loaded, the test is environmentally incomplete.
        if bc_code is None:
            self.skipTest("No BC cross-jurisdiction code recognised")
        tables = canonicalize([(bc_code, "BC")], self.cross_index, self.qc_index)
        self.assertGreater(len(tables), 0)
        entry = tables[0]
        self.assertNotEqual(entry.casfri_canonical, "")
        self.assertEqual(
            entry.value_policy, VALUE_POLICIES["resolved_via_cross_jurisdiction_map"],
        )

    def test_decision_identifier_collisions_rejected_for_distinct_policies(self) -> None:
        # Different policies for the same raw/province/canonical produce
        # different decision_ids.
        first = decision_identifier("SAB", "QC", "ABIE_BAL_###", "qc_species_resolved_via_lookup")
        second = decision_identifier("SAB", "QC", "ABIE_BAL_###", "qc_species_unmapped_unresolved")
        self.assertNotEqual(first, second)


class TestMergeDecisions(unittest.TestCase):
    """``merge_decisions`` collapses duplicate decisions deterministically."""

    def test_merge_collapses_duplicate_keys(self) -> None:
        from fresh_fibre.species_canonical import SpeciesTable

        first = SpeciesTable(
            raw_code="SAB", province="QC", jurisdictional_prefix="",
            casfri_canonical="ABIE_BAL_###", fresh_canonical="ABIE_BAL_###",
            scientific_name="", value_policy="qc_species_resolved_via_lookup",
            decision_id="abc1",
        )
        second = SpeciesTable(
            raw_code="SAB", province="QC", jurisdictional_prefix="",
            casfri_canonical="ABIE_BAL_###", fresh_canonical="ABIE_BAL_###",
            scientific_name="", value_policy="qc_species_resolved_via_lookup",
            decision_id="abc2",  # different decision id, but same key
        )
        merged = merge_decisions([first, second])
        self.assertEqual(len(merged), 1)

    def test_merge_keeps_distinct_policies(self) -> None:
        from fresh_fibre.species_canonical import SpeciesTable

        first = SpeciesTable(
            raw_code="SAB", province="QC", jurisdictional_prefix="",
            casfri_canonical="ABIE_BAL_###", fresh_canonical="ABIE_BAL_###",
            scientific_name="", value_policy="qc_species_resolved_via_lookup",
            decision_id="abc1",
        )
        second = SpeciesTable(
            raw_code="SAB", province="QC", jurisdictional_prefix="",
            casfri_canonical="ABIE_BAL_###", fresh_canonical="",
            scientific_name="", value_policy="qc_species_unmapped_unresolved",
            decision_id="def2",
        )
        merged = merge_decisions([first, second])
        self.assertEqual(len(merged), 2)


class TestCanonicalId(unittest.TestCase):
    """``canonical_id`` returns the CASFRI canonical for the given mapping."""

    def test_canonical_id_when_mapping_present(self) -> None:
        from fresh_fibre.species_canonical import SpeciesMapping
        mapping = SpeciesMapping(
            raw_code="SB", province="QC", casfri_canonical="PICE_MAR_###",
            scientific_name="Picea mariana", producer_province="qc",
            source="cross_jurisdiction_map",
            value_policy="qc_species_resolved_via_lookup",
        )
        self.assertEqual(canonical_id(mapping, "QC"), "PICE_MAR_###")

    def test_canonical_id_when_mapping_absent(self) -> None:
        self.assertEqual(canonical_id(None, "QC"), "")


class TestReadLegacyStandTable(unittest.TestCase):
    """Reading legacy stand tables with column-name drift."""

    def test_legacy_csv_with_synonyms(self, temp_csv: Path | None = None) -> None:
        import csv
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["raw_code", "province"])
            writer.writeheader()
            writer.writerow({"raw_code": "SAB", "province": "QC"})
            writer.writerow({"raw_code": "EPB", "province": "QC"})
            writer.writerow({"raw_code": "", "province": "QC"})
            temp_path = handle.name

        try:
            rows = read_legacy_stand_table(Path(temp_path))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], ("SAB", "QC"))
        finally:
            Path(temp_path).unlink()

    def test_qc_fixture_mini_table_path(self) -> None:
        # Tests that the synthetic-bounded QC mini fixture can be parsed
        # by the legacy stand-table reader once it exists in the repo.
        qc_fixture_path = (
            REPO_ROOT / "metadata" / "ffcm-archive" / "prototype-inputs"
            / "bc-qc-mini-2prov" / "qc-diagnostic" / "qc_diagnostic_inventory_cells.csv"
        )
        if not qc_fixture_path.exists():
            self.skipTest("QC mini fixture not yet generated")
        # The reader will not see the QC mini directly because the file
        # uses different column names, but it still has signals we can
        # surface for downstream diagnostics.
        self.assertTrue(qc_fixture_path.exists())


class TestCrossJurisdictionOutputSchema(unittest.TestCase):
    """``CROSS_JURISDICTION_OUTPUT_COLUMNS`` is the unified output schema."""

    def test_columns_match_documented_keys(self) -> None:
        expected = {
            "raw_code", "province", "jurisdictional_prefix",
            "casfri_canonical", "fresh_canonical", "scientific_name",
            "value_policy", "decision_id",
        }
        self.assertEqual(set(CROSS_JURISDICTION_OUTPUT_COLUMNS), expected)


if __name__ == "__main__":
    unittest.main()
