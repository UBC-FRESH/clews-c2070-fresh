"""Tests for the TSA tenure-to-facility crosswalk and contract bounds."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from workflows.national.assignments.bc_fibre import TsaApportionment
from workflows.national.assignments.fixtures import (
    FixtureError,
    _load_apportionment_contracts,
)
from workflows.national.assignments.schema import FacilityCapacity, FacilityOffer
from workflows.national.assignments.tenure_crosswalk import (
    CONTRACT_EVIDENCE_TIER,
    FacilityRecord,
    _contract_rows,
    canonical_tsa,
    entity_alias_family,
    match_licence,
    normalize_entity_name,
)


def _licence(
    holder: str,
    tsa: str,
    volume: float,
    *,
    licence_number: str = "A12345",
    tenure_class: str = "Replaceable Forest Licences",
    source_row: int = 10,
) -> TsaApportionment:
    return TsaApportionment(
        tsa=tsa,
        tenure_class=tenure_class,
        tenure_subclass="",
        licence_number=licence_number,
        holder=holder,
        expiry="",
        row_type="licence",
        period_year=2018,
        volume_m3=volume,
        source_sheet=tsa,
        source_row=source_row,
    )


def _facility(
    facility_id: str,
    company: str,
    *,
    tsa: str = "Morice",
    tsa_key: str | None = None,
    facility_class: str = "SAW",
    nameplate_m3_y: float = 500000.0,
) -> FacilityRecord:
    return FacilityRecord(
        facility_id=facility_id,
        facility_name=f"{company}-{facility_id}",
        facility_class=facility_class,
        company=company,
        normalized_company=normalize_entity_name(company),
        family=entity_alias_family(company),
        tsa_label=tsa,
        tsa_key=canonical_tsa(tsa) if tsa_key is None else tsa_key,
        tsa_source="test_spatial_join",
        stage_a_scope=facility_class in {"SAW", "PAP", "PEL", "BIO"},
        nameplate_m3_y=nameplate_m3_y,
        source_row=2,
    )


class NormalizationTests(unittest.TestCase):
    def test_legal_suffixes_are_removed_but_industry_words_remain(self) -> None:
        self.assertEqual(
            normalize_entity_name("Tolko Industries Ltd."), "tolko industries"
        )
        self.assertEqual(
            normalize_entity_name("West Fraser Timber Co. Ltd."),
            "west fraser timber",
        )
        self.assertEqual(normalize_entity_name("Canfor Pulp LP"), "canfor pulp")
        self.assertEqual(
            normalize_entity_name("1040806 Ltd (Dunkley)"), "1040806 dunkley"
        )

    def test_alias_table_covers_major_holders_and_keeps_distinct_families(self) -> None:
        self.assertEqual(entity_alias_family("Tolko Industries Ltd."), "tolko")
        self.assertEqual(entity_alias_family("WFP"), "western_forest_products")
        self.assertEqual(
            entity_alias_family("Western Forest Products Inc."),
            "western_forest_products",
        )
        self.assertEqual(entity_alias_family("Teal Cedar"), "teal_jones")
        self.assertEqual(entity_alias_family("Canfor Pulp LP"), "canfor")
        self.assertEqual(entity_alias_family("Stella-Jones"), "")

    def test_tsa_aliases_join_workbook_and_fadm_names(self) -> None:
        self.assertEqual(canonical_tsa("PG"), "prince george")
        self.assertEqual(canonical_tsa("FtSt John"), "fort st john")
        self.assertEqual(canonical_tsa("Great Bear North TSA"), "gbr north")
        self.assertEqual(canonical_tsa("100 Mile House TSA"), "100 mile house")
        self.assertEqual(canonical_tsa("Haida Gwaii"), "queen charlotte")


class MatchRuleTests(unittest.TestCase):
    def test_exact_company_in_same_tsa_matches(self) -> None:
        decision = match_licence(
            _licence("Canfor", "Morice", 1000.0),
            (_facility("BC-SAW-488", "Canfor Corporation"),),
        )
        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.facility.facility_id, "BC-SAW-488")
        self.assertEqual(decision.method, "exact_holder_unique")
        self.assertEqual(decision.confidence, "high")

    def test_alias_company_in_same_tsa_matches(self) -> None:
        decision = match_licence(
            _licence("Tolko", "100 Mile", 1000.0),
            (
                _facility(
                    "BC-PLY-674",
                    "Tolko Industries Ltd.",
                    tsa="100 Mile House",
                ),
            ),
        )
        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.facility.facility_id, "BC-PLY-674")
        self.assertEqual(decision.method, "alias_holder_same_tsa")
        self.assertEqual(decision.confidence, "medium")

    def test_two_facilities_in_the_same_tsa_are_ambiguous(self) -> None:
        decision = match_licence(
            _licence("Canfor", "Fort St John", 1000.0),
            (
                _facility("BC-SAW-383", "Canfor Corporation", tsa="Fort St John"),
                _facility("BC-PAP-1319", "Canfor Corporation", tsa="Fort St John"),
            ),
        )
        self.assertEqual(decision.status, "ambiguous")
        self.assertIsNone(decision.facility)
        self.assertEqual(decision.method, "exact_holder_same_tsa_ambiguous")
        self.assertEqual(len(decision.candidates), 2)

    def test_multiple_facilities_without_a_tsa_match_are_ambiguous(self) -> None:
        decision = match_licence(
            _licence("Canfor", "Fort Nelson", 1000.0),
            (
                _facility("BC-SAW-352", "Canfor Corporation", tsa="Cranbrook"),
                _facility("BC-SAW-488", "Canfor Corporation", tsa="Morice"),
            ),
        )
        self.assertEqual(decision.status, "ambiguous")
        self.assertEqual(decision.method, "exact_holder_multiple_no_tsa_match")
        self.assertEqual(decision.same_tsa_candidate_count, 0)

    def test_unique_alias_family_outside_the_tsa_is_a_low_confidence_match(self) -> None:
        decision = match_licence(
            _licence("1040806 Ltd (Dunkley)", "Mackenzie", 1000.0),
            (_facility("BC-SAW-1253", "Dunkley Lumber", tsa="Prince George"),),
        )
        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.facility.facility_id, "BC-SAW-1253")
        self.assertEqual(decision.method, "alias_holder_unique")
        self.assertEqual(decision.confidence, "low")

    def test_unknown_holder_is_unmatched(self) -> None:
        decision = match_licence(
            _licence("Coast Tsimshian Res Ltd", "Kalum", 1000.0),
            (_facility("BC-SAW-488", "Canfor Corporation"),),
        )
        self.assertEqual(decision.status, "unmatched")
        self.assertEqual(decision.method, "no_exact_or_alias_company")


class ContractBoundTests(unittest.TestCase):
    def test_licence_rows_to_one_facility_become_one_summed_contract(self) -> None:
        facility = _facility("BC-SAW-488", "Canfor Corporation", nameplate_m3_y=2_000_000.0)
        decisions = (
            match_licence(_licence("Canfor", "Morice", 940424.0, source_row=22), (facility,)),
            match_licence(_licence("Canfor", "Morice", 324500.0, source_row=23), (facility,)),
        )
        contracts = _contract_rows(decisions)
        self.assertEqual(len(contracts), 1)
        contract = contracts[0]
        self.assertEqual(contract["facility_id"], "BC-SAW-488")
        self.assertEqual(contract["grade"], "all_logs")
        self.assertEqual(contract["min_volume"], 0.0)
        self.assertEqual(contract["max_volume"], 1264924.0)
        self.assertEqual(contract["gate_cost"], 0.0)
        self.assertEqual(contract["evidence_tier"], CONTRACT_EVIDENCE_TIER)
        self.assertEqual(contract["licence_count"], 2)
        self.assertFalse(contract["exceeds_nameplate"])

    def test_unmatched_rows_do_not_create_a_contract(self) -> None:
        decision = match_licence(
            _licence("Coast Tsimshian Res Ltd", "Kalum", 1000.0),
            (_facility("BC-SAW-488", "Canfor Corporation"),),
        )
        self.assertEqual(_contract_rows((decision,)), [])


class ApportionmentLoaderTests(unittest.TestCase):
    def _facility(self) -> FacilityCapacity:
        return FacilityCapacity(
            facility_id="BC-SAW-488",
            facility_name="Canfor-Houston",
            facility_class="SAW",
            nameplate_m3_y=500000.0,
            offers=(
                FacilityOffer(
                    offer_id="BC-SAW-488::SAWLOG",
                    period="2026",
                    facility_id="BC-SAW-488",
                    facility_name="Canfor-Houston",
                    facility_class="SAW",
                    grade_id="SAWLOG",
                    primary_product="sawlog",
                    capacity_m3_y=500000.0,
                    processing_cost_cad_m3=10.0,
                    product_revenue_cad_m3=20.0,
                    capacity_basis="test",
                    cost_basis="test",
                    revenue_basis="test",
                    evidence="test",
                ),
            ),
            evidence="test",
        )

    def _write(self, rows: list[dict[str, object]]) -> Path:
        fieldnames = [
            "facility_id",
            "grade",
            "min_volume",
            "max_volume",
            "gate_cost",
            "source",
            "evidence_tier",
            "contract_source",
        ]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return Path(handle.name)

    def test_apportionment_row_becomes_a_diagnostic_maximum(self) -> None:
        path = self._write(
            [
                {
                    "facility_id": "BC-SAW-488",
                    "grade": "all_logs",
                    "min_volume": "0",
                    "max_volume": "1264924",
                    "gate_cost": "0",
                    "source": "TSA Tenure Apportionment V5.xls:Morice:22",
                    "evidence_tier": "apportionment_derived_diagnostic",
                    "contract_source": "apportionment_derived_diagnostic",
                }
            ]
        )
        try:
            contracts = _load_apportionment_contracts(
                path, (self._facility(),), period="2026"
            )
            self.assertEqual(len(contracts), 1)
            contract = contracts[0]
            self.assertEqual(contract.max_volume_m3, 1264924.0)
            self.assertEqual(contract.min_volume_m3, 0.0)
            self.assertEqual(contract.gate_cost_cad_m3, 0.0)
            self.assertEqual(
                contract.contract_source, "apportionment_derived_diagnostic"
            )
            self.assertTrue(contract.diagnostic)
        finally:
            path.unlink(missing_ok=True)

    def test_duplicate_facility_rows_fail_loudly(self) -> None:
        row = {
            "facility_id": "BC-SAW-488",
            "grade": "all_logs",
            "min_volume": "0",
            "max_volume": "1000",
            "gate_cost": "0",
            "source": "test",
            "evidence_tier": "apportionment_derived_diagnostic",
            "contract_source": "apportionment_derived_diagnostic",
        }
        path = self._write([row, row])
        try:
            with self.assertRaises(FixtureError):
                _load_apportionment_contracts(path, (self._facility(),), period="2026")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
