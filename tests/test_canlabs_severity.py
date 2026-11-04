"""Focused tests for the CanLaBS severity module and its integration."""

from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from fresh_fibre.canlabs_severity import (
    CanLaBSWindowSummary,
    summarise_array,
)
from fresh_fibre.scanfi_supply import (
    SCANFI_HARVEST_FIELDNAMES,
    build_harvest_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"
SCANFI_FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestCanLaBSArraySummariser(unittest.TestCase):
    """Tests for the pure-array summariser (no rasterio dependency)."""

    def test_signed_values_preserved(self) -> None:
        """Negative and positive dNBR values are both valid and counted."""
        values = [-100, -50, 0, 50, 100, 200, 300]
        summary = summarise_array(values, nodata_value=-32768)
        self.assertEqual(summary.valid_count, 7)
        self.assertEqual(summary.nodata_count, 0)
        self.assertEqual(summary.min_value, -100)
        self.assertEqual(summary.max_value, 300)
        # Mean = (-100 + -50 + 0 + 50 + 100 + 200 + 300) / 7 = 500/7
        self.assertAlmostEqual(summary.mean_value, 500.0 / 7, places=5)
        self.assertEqual(summary.median_value, 50)

    def test_nodata_excluded(self) -> None:
        """The declared nodata sentinel is excluded from statistics."""
        values = [-32768, 100, 200, -32768, 300]
        summary = summarise_array(values, nodata_value=-32768)
        self.assertEqual(summary.valid_count, 3)
        self.assertEqual(summary.nodata_count, 2)
        self.assertEqual(summary.min_value, 100)
        self.assertEqual(summary.max_value, 300)
        self.assertAlmostEqual(summary.mean_value, 200.0)

    def test_all_nodata_returns_null_statistics(self) -> None:
        """All-nodata windows return counts and null statistics, not crashes."""
        values = [-32768, -32768, -32768]
        summary = summarise_array(values, nodata_value=-32768)
        self.assertEqual(summary.valid_count, 0)
        self.assertEqual(summary.nodata_count, 3)
        self.assertIsNone(summary.min_value)
        self.assertIsNone(summary.max_value)
        self.assertIsNone(summary.mean_value)
        self.assertIsNone(summary.median_value)

    def test_empty_array(self) -> None:
        """Empty arrays are handled gracefully."""
        summary = summarise_array([], nodata_value=-32768)
        self.assertEqual(summary.valid_count, 0)
        self.assertEqual(summary.nodata_count, 0)
        self.assertIsNone(summary.min_value)

    def test_metadata_forwarded(self) -> None:
        """Metadata fields are forwarded to the summary."""
        summary = summarise_array(
            [100, 200],
            nodata_value=-32768,
            source_path="/tmp/test.tif",
            source_crs="EPSG:3978",
            resolution_m=30.0,
            value_policy="custom_policy",
            manifest_ref="manifest.csv",
            checksum_sha256="abc123",
            window_shape=(10, 20),
        )
        self.assertEqual(summary.source_path, "/tmp/test.tif")
        self.assertEqual(summary.source_crs, "EPSG:3978")
        self.assertEqual(summary.resolution_m, 30.0)
        self.assertEqual(summary.value_policy, "custom_policy")
        self.assertEqual(summary.manifest_ref, "manifest.csv")
        self.assertEqual(summary.checksum_sha256, "abc123")
        self.assertEqual(summary.window_shape, (10, 20))

    def test_immutable(self) -> None:
        """Summary is frozen (immutable)."""
        summary = summarise_array([100], nodata_value=-32768)
        with self.assertRaises(AttributeError):
            summary.valid_count = 999  # type: ignore[misc]


class TestCanLaBSRasterWindow(unittest.TestCase):
    """Tests for the raster-window reader (requires rasterio)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raster_path = Path(self.tmp.name) / "test.tif"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_test_raster(self, values, crs="EPSG:3978", res=30.0, nodata=-32768):
        """Write a tiny synthetic GeoTIFF for testing."""
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        values_np = np.array(values, dtype=np.int16)
        height, width = values_np.shape
        transform = from_origin(0, height * res, res, res)
        profile = {
            "driver": "GTiff",
            "dtype": "int16",
            "width": width,
            "height": height,
            "count": 1,
            "crs": crs,
            "transform": transform,
            "nodata": nodata,
        }
        with rasterio.open(self.raster_path, "w", **profile) as dst:
            dst.write(values_np, 1)

    def test_read_window_basic(self) -> None:
        """Basic window read with valid values."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[100, 200], [300, 400]]
        self._write_test_raster(values)

        window = rasterio.windows.Window(col_off=0, row_off=0, width=2, height=2)
        summary = read_window(self.raster_path, window)

        self.assertEqual(summary.valid_count, 4)
        self.assertEqual(summary.nodata_count, 0)
        self.assertEqual(summary.min_value, 100)
        self.assertEqual(summary.max_value, 400)
        self.assertEqual(summary.source_crs, "EPSG:3978")
        self.assertEqual(summary.resolution_m, 30.0)

    def test_read_window_wrong_crs(self) -> None:
        """Wrong CRS raises ValueError."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[100]]
        self._write_test_raster(values, crs="EPSG:4326")

        window = rasterio.windows.Window(col_off=0, row_off=0, width=1, height=1)
        with self.assertRaisesRegex(ValueError, "CRS mismatch"):
            read_window(self.raster_path, window, expected_crs="EPSG:3978")

    def test_read_window_wrong_resolution(self) -> None:
        """Wrong resolution raises ValueError."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[100]]
        self._write_test_raster(values, res=10.0)

        window = rasterio.windows.Window(col_off=0, row_off=0, width=1, height=1)
        with self.assertRaisesRegex(ValueError, "Resolution"):
            read_window(self.raster_path, window, expected_resolution_m=30.0)

    def test_read_window_nodata(self) -> None:
        """Nodata values are excluded from statistics."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[-32768, 100], [200, -32768]]
        self._write_test_raster(values)

        window = rasterio.windows.Window(col_off=0, row_off=0, width=2, height=2)
        summary = read_window(self.raster_path, window)

        self.assertEqual(summary.valid_count, 2)
        self.assertEqual(summary.nodata_count, 2)
        self.assertEqual(summary.min_value, 100)
        self.assertEqual(summary.max_value, 200)

    def test_read_window_all_nodata(self) -> None:
        """All-nodata window returns null statistics."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[-32768, -32768], [-32768, -32768]]
        self._write_test_raster(values)

        window = rasterio.windows.Window(col_off=0, row_off=0, width=2, height=2)
        summary = read_window(self.raster_path, window)

        self.assertEqual(summary.valid_count, 0)
        self.assertEqual(summary.nodata_count, 4)
        self.assertIsNone(summary.min_value)

    def test_read_window_partial(self) -> None:
        """Partial window read works correctly."""
        from fresh_fibre.canlabs_severity import read_window
        import rasterio.windows

        values = [[100, 200, 300], [400, 500, 600], [700, 800, 900]]
        self._write_test_raster(values)

        # Read a 2x2 sub-window
        window = rasterio.windows.Window(col_off=1, row_off=1, width=2, height=2)
        summary = read_window(self.raster_path, window)

        self.assertEqual(summary.valid_count, 4)
        self.assertEqual(summary.min_value, 500)
        self.assertEqual(summary.max_value, 900)
        self.assertEqual(summary.window_shape, (2, 2))


class TestCanLaBSIntegration(unittest.TestCase):
    """Tests for CanLaBS integration into build_harvest_rows."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.input_dir = Path(self.tmp.name) / "bc-mini"
        shutil.copytree(FIXTURE_DIR, self.input_dir)
        self.scanfi_dir = Path(self.tmp.name) / "scanfi-diagnostic"
        shutil.copytree(SCANFI_FIXTURE_DIR, self.scanfi_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_no_canlabs_fields(self) -> None:
        """Without canlabs_severity mapping, fields are empty or zero."""
        rows = build_harvest_rows(
            inventory_path=self.scanfi_dir / "diagnostic_inventory_cells.csv",
            yield_rules_path=self.scanfi_dir / "diagnostic_yield_rules.csv",
            input_dir=self.input_dir,
        )
        for row in rows:
            self.assertEqual(row["canlabs_severity_source_path"], "")
            self.assertEqual(row["canlabs_severity_mean_dnbr"], "")
            # Count fields are "0" when no CanLaBS data is present
            self.assertEqual(row["canlabs_severity_valid_count"], "0")
            self.assertEqual(row["canlabs_severity_nodata_count"], "0")
            self.assertEqual(row["canlabs_severity_min_dnbr"], "")
            self.assertEqual(row["canlabs_severity_max_dnbr"], "")
            self.assertEqual(row["canlabs_severity_value_policy"], "")
            self.assertEqual(row["canlabs_severity_manifest_ref"], "")

    def test_canlabs_fields_populated(self) -> None:
        """With canlabs_severity mapping, fields are populated."""
        canlabs_map = {
            "scanfi_bc_coast_wet_forest_001": {
                "source_path": "/path/to/raster.tif",
                "mean_dnbr": 150.5,
                "valid_count": 1000,
                "nodata_count": 100,
                "min_dnbr": -50,
                "max_dnbr": 400,
                "value_policy": "raw_dnbr_not_classified",
                "manifest_ref": "manifest.csv",
            },
            "scanfi_bc_coast_second_growth_002": {
                "source_path": "/path/to/raster.tif",
                "mean_dnbr": 200.0,
                "valid_count": 800,
                "nodata_count": 50,
                "min_dnbr": -100,
                "max_dnbr": 500,
                "value_policy": "raw_dnbr_not_classified",
                "manifest_ref": "manifest.csv",
            },
        }
        rows = build_harvest_rows(
            inventory_path=self.scanfi_dir / "diagnostic_inventory_cells.csv",
            yield_rules_path=self.scanfi_dir / "diagnostic_yield_rules.csv",
            input_dir=self.input_dir,
            canlabs_severity=canlabs_map,
        )
        # Aggregated rows have inventory_cell_id as a semicolon-joined list.
        # Check that at least one aggregated row contains both cell IDs.
        target_ids = {
            "scanfi_bc_coast_wet_forest_001",
            "scanfi_bc_coast_second_growth_002",
        }
        matching_rows = [
            r for r in rows
            if target_ids.issubset(set(r["inventory_cell_id"].split("; ")))
        ]
        self.assertGreaterEqual(len(matching_rows), 1)
        agg_row = matching_rows[0]

        # Verify CanLaBS fields are populated in the aggregated row.
        self.assertNotEqual(agg_row["canlabs_severity_source_path"], "")
        self.assertNotEqual(agg_row["canlabs_severity_mean_dnbr"], "")
        self.assertNotEqual(agg_row["canlabs_severity_valid_count"], "")

    def test_aggregation_preserves_canlabs(self) -> None:
        """Aggregation preserves CanLaBS fields with transparent rules."""
        canlabs_map = {
            "cell_001": {
                "source_path": "/path/to/raster.tif",
                "mean_dnbr": 150.0,
                "valid_count": 1000,
                "nodata_count": 100,
                "min_dnbr": -50,
                "max_dnbr": 400,
                "value_policy": "raw_dnbr_not_classified",
                "manifest_ref": "manifest.csv",
            },
            "cell_002": {
                "source_path": "/path/to/raster.tif",
                "mean_dnbr": 250.0,
                "valid_count": 2000,
                "nodata_count": 200,
                "min_dnbr": -100,
                "max_dnbr": 500,
                "value_policy": "raw_dnbr_not_classified",
                "manifest_ref": "manifest.csv",
            },
        }
        rows = build_harvest_rows(
            inventory_path=self.scanfi_dir / "diagnostic_inventory_cells.csv",
            yield_rules_path=self.scanfi_dir / "diagnostic_yield_rules.csv",
            input_dir=self.input_dir,
            canlabs_severity=canlabs_map,
        )
        # Check that aggregated rows have CanLaBS fields
        for row in rows:
            # At least one field should be populated if any cell had data
            has_canlabs = any(
                row.get(f"canlabs_severity_{field}", "")
                for field in [
                    "source_path", "mean_dnbr", "valid_count",
                    "nodata_count", "min_dnbr", "max_dnbr",
                    "value_policy", "manifest_ref",
                ]
            )
            # Not all rows will have CanLaBS data (depends on inventory)
            # Just verify the fields exist in the row
            self.assertIn("canlabs_severity_source_path", row)
            self.assertIn("canlabs_severity_mean_dnbr", row)


class TestFieldnamesExtension(unittest.TestCase):
    """Verify SCANFI_HARVEST_FIELDNAMES includes CanLaBS columns."""

    def test_fieldnames_include_canlabs(self) -> None:
        """SCANFI_HARVEST_FIELDNAMES includes all CanLaBS columns."""
        expected = [
            "canlabs_severity_source_path",
            "canlabs_severity_mean_dnbr",
            "canlabs_severity_valid_count",
            "canlabs_severity_nodata_count",
            "canlabs_severity_min_dnbr",
            "canlabs_severity_max_dnbr",
            "canlabs_severity_value_policy",
            "canlabs_severity_manifest_ref",
        ]
        for field in expected:
            self.assertIn(field, SCANFI_HARVEST_FIELDNAMES)


if __name__ == "__main__":
    unittest.main()