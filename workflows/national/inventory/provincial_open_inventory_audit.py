"""Build the provincial open forest-inventory audit artifact.

The audit is deliberately sample-based: it records what each jurisdiction
publishes, under which licence, and whether a small verified sample exposes the
stand attributes needed by the national chain.  It does not ingest full layers
and it does not derive model inputs.

Run from the repository root:

    .venv/bin/python -m workflows.national.inventory.provincial_open_inventory_audit
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = ROOT / "data/derived/national/inventory/provincial_open_inventory_audit_v1"
SAMPLES = ARTIFACT / "samples"

RETRIEVAL_DATE = "2026-11-23"

# Source-level audit rows.  "sampled" means a small sample was downloaded and
# parsed; it does not imply that the full layer is suitable for ingestion.
SOURCES = [
    {
        "province": "AB",
        "source_id": "ab_avi_crown",
        "source_name": "Alberta Vegetation Inventory (AVI) Crown",
        "publisher": "Government of Alberta, Forestry and Parks / Environment and Protected Areas",
        "access_mode": "direct_download",
        "access_status": "open",
        "source_url": "https://open.alberta.ca/opendata/gda-3dbcfa02-e97a-4059-9414-1ed8e0700e80",
        "download_url": "https://extranet.gov.ab.ca/srd/geodiscover/srd_pub/biota/AlbertaVegetationInventoryCrown.zip",
        "licence": "Open Government Licence - Alberta",
        "licence_url": "https://open.alberta.ca/licence",
        "coverage": "AVI Crown blocks across Alberta (photo years 1987-2019; spatial extent -120,49 to -110,60)",
        "inventory_year": "1987-2019",
        "format": "ESRI File Geodatabase (ZIP)",
        "spatial_level": "stand_polygon",
        "species_field": "SP1..SP5 with SP1_PER..SP5_PER",
        "age_field": "ORIGIN (year of stand origin; age = PHOTO_YR - ORIGIN)",
        "height_field": "HEIGHT",
        "volume_biomass_field": "none in AVI polygon attributes",
        "has_species": True,
        "has_age": True,
        "has_height": True,
        "has_volume_biomass": False,
        "sampled": True,
        "sample_file": "samples/ab_avi_crown_sample.csv",
        "notes": "788,810 polygons in the downloaded FGDB. No volume or biomass field; AVI height/species/age are photo-interpreted. Four added fields PHOTO_YR, ARIS, DIST_PTRN, LID_UND; availability varies with vintage.",
    },
    {
        "province": "SK",
        "source_id": "sk_fri_forest_stands",
        "source_name": "FRI: Forest stands (FSTAND)",
        "publisher": "Saskatchewan Ministry of Environment, Forest Service Branch",
        "access_mode": "arcgis_rest",
        "access_status": "open",
        "source_url": "https://open.canada.ca/data/en/dataset/94a100fa-30b4-6ac0-430a-145b02bb81d4",
        "download_url": "https://services3.arcgis.com/zcv98lgAl8xQ04cW/arcgis/rest/services/FRI_Forest_Stands/FeatureServer/0",
        "licence": "Government of Saskatchewan Standard Unrestricted Use Data Licence (Version 2.0)",
        "licence_url": "https://www.saskatchewan.ca/government/government-data/datasets",
        "coverage": "Saskatchewan commercial forest zone and adjacent forest fringe",
        "inventory_year": "2017-2019",
        "format": "ArcGIS FeatureServer; CSV/GeoJSON/GeoPackage/SHP/FGDB downloads",
        "spatial_level": "stand_polygon",
        "species_field": "CSG (softwood/hardwood class only; no explicit tree-species code in the stand vector)",
        "age_field": "none in the stand vector",
        "height_field": "HEIGHT",
        "volume_biomass_field": "GBVOL, GMVOL8",
        "has_species": False,
        "has_age": False,
        "has_height": True,
        "has_volume_biomass": True,
        "sampled": True,
        "sample_file": "samples/sk_fri_forest_stands_sample.csv",
        "notes": "Stand vector carries height, basal area, QMD, stems, gross volume and merchantable volume, but only a softwood/hardwood class (CSG) rather than species. Species/age rasters are separate FRI products and were not sampled.",
    },
    {
        "province": "MB",
        "source_id": "mb_fri_1970s",
        "source_name": "Manitoba Forest Inventory - 1970s",
        "publisher": "Government of Manitoba, Environment and Climate Change",
        "access_mode": "direct_download",
        "access_status": "open",
        "source_url": "https://open.canada.ca/data/en/dataset/494605ed-26d1-a901-d7ee-e95f51dd38a0",
        "download_url": "https://www.arcgis.com/sharing/rest/content/items/0f7bc05a7c694714aca4abf79a15efa8/data",
        "licence": "OpenMB Information and Data Use License",
        "licence_url": "https://open.canada.ca/data/en/dataset/494605ed-26d1-a901-d7ee-e95f51dd38a0",
        "coverage": "Central Manitoba green zone (Boreal Plains and Boreal Shield); ~582,601 polygons",
        "inventory_year": "1970s (photo years 1970-1979 in sample)",
        "format": "ESRI File Geodatabase (ZIP)",
        "spatial_level": "stand_polygon",
        "species_field": "SPECIES, SP1..SP7 with SP1PER..SP7PER",
        "age_field": "AGE_YRPHOTO",
        "height_field": "none in the stand attributes",
        "volume_biomass_field": "none in the stand attributes",
        "has_species": True,
        "has_age": True,
        "has_height": False,
        "has_volume_biomass": False,
        "sampled": True,
        "sample_file": "samples/mb_fri_1970s_sample.csv",
        "notes": "Historical photo-interpreted FRI only. The 2021 Manitoba FRI/FLI is published as a web-map tile layer and 2016-2021 summary statistics, not as open stand polygons. No height or volume field in the open 1970s stand attributes.",
    },
    {
        "province": "ON",
        "source_id": "on_fri_packaged_products",
        "source_name": "Forest Resources Inventory (FRI) Term 1 / Term 2 packaged products",
        "publisher": "Ontario Ministry of Natural Resources and Forestry / Geospatial Ontario",
        "access_mode": "lids_account_order",
        "access_status": "metadata_open_attributes_not_direct",
        "source_url": "https://ontariogeohub-lio.opendata.arcgis.com/datasets/lio::forest-resources-inventory-packaged-products-version-2",
        "download_url": "",
        "licence": "Open Government Licence - Ontario",
        "licence_url": "https://www.ontario.ca/page/open-government-licence-ontario",
        "coverage": "Ontario managed forest (Forest Management Units); T1 2008-2017 and T2 2018-2028",
        "inventory_year": "T1 2008-2017; T2 2018-2028",
        "format": "FRI packaged products (per-FMU ZIP) via Land Information Data Subscription (LIDS)",
        "spatial_level": "stand_polygon",
        "species_field": "species composition in FRI packaged product (not sampled)",
        "age_field": "FRI age at inventory in packaged product (not sampled)",
        "height_field": "FRI height in packaged product (not sampled)",
        "volume_biomass_field": "not confirmed in packaged product metadata",
        "has_species": True,
        "has_age": True,
        "has_height": True,
        "has_volume_biomass": False,
        "sampled": False,
        "sample_file": "samples/on_fri_catalogue_sample.csv",
        "notes": "The FRI status layer and packaged-product catalogue are open and were sampled. The actual stand-attribute packages are ordered through LIDS; no direct open REST/download for stand attributes was found, so no attribute sample was taken. Catalogue sample is metadata only.",
    },
    {
        "province": "NS",
        "source_id": "ns_forest_inventory",
        "source_name": "Nova Scotia Forest Inventory - Current Forest Data (Cycles 2-4)",
        "publisher": "Nova Scotia Department of Natural Resources and Renewables",
        "access_mode": "direct_download",
        "access_status": "open",
        "source_url": "https://data.novascotia.ca/datasets/c8ai-fjbt",
        "download_url": "https://nsgi.novascotia.ca/WSF_DDS/DDS.svc/DownloadFile?tkey=fhrTtdnDvfytwLz6&id=90",
        "licence": "Open Government Licence - Nova Scotia",
        "licence_url": "https://novascotia.ca/opendata/licence.asp",
        "coverage": "All Nova Scotia lands; current forest data combining interpretation cycles 2-4",
        "inventory_year": "photography 1992-present (sample PHOTOYR 2007-2008)",
        "format": "ESRI Shapefile (ZIP, UT83)",
        "spatial_level": "stand_polygon",
        "species_field": "SPECIES; SP1..SP4 with SP1P..SP4P",
        "age_field": "none (maturity/site class proxies only)",
        "height_field": "HEIGHT; SS_HEIGHT",
        "volume_biomass_field": "TOTMRVOL, SWMRVOL, HWMRVOL; TOTSTDMRVO",
        "has_species": True,
        "has_age": False,
        "has_height": True,
        "has_volume_biomass": True,
        "sampled": True,
        "sample_file": "samples/ns_forest_inventory_sample.csv",
        "notes": "1,004,111 polygons in the downloaded shapefile DBF. Species composition, crown closure, height, basal area and merchantable volume are stand attributes; no explicit stand age. Volume is per hectare on the polygon.",
    },
    {
        "province": "PE",
        "source_id": "pe_clui_2010_forest",
        "source_name": "Prince Edward Island Corporate Landuse Inventory 2010 - Forest",
        "publisher": "Government of Prince Edward Island, Forests, Fish and Wildlife",
        "access_mode": "arcgis_rest",
        "access_status": "open",
        "source_url": "https://data-princeedwardisland-peigov.hub.arcgis.com/datasets/0e4238558ec141039d118009fa3bbb5f",
        "download_url": "https://services9.arcgis.com/zow9Ot3ujGSyJI3C/arcgis/rest/services/Corporate_Landuse_Inventory_2010/FeatureServer/0",
        "licence": "Open Government Licence - Prince Edward Island",
        "licence_url": "https://www.princeedwardisland.ca/en/information/finance/open-government-licence-prince-edward-island",
        "coverage": "Prince Edward Island, all forest land-use polygons",
        "inventory_year": "2010",
        "format": "ArcGIS FeatureServer",
        "spatial_level": "stand_polygon",
        "species_field": "COVER1..COVER5 with PER1..PER5",
        "age_field": "none (DEV_STAGE development stage only)",
        "height_field": "HEIGHT",
        "volume_biomass_field": "none in CLUI",
        "has_species": True,
        "has_age": False,
        "has_height": True,
        "has_volume_biomass": False,
        "sampled": True,
        "sample_file": "samples/pe_clui_2010_forest_sample.csv",
        "notes": "Photo-interpreted stand polygons with species composition, percent, height, crown closure and development stage. No volume and no explicit age. The 2020 CLUI was not exposed as a feature service in the portal search.",
    },
    {
        "province": "PE",
        "source_id": "pe_efi_2020",
        "source_name": "PEI Enhanced Forest Inventory 2020 (EFI2020)",
        "publisher": "Government of Prince Edward Island, Resource Inventory & Modelling Section",
        "access_mode": "arcgis_rest",
        "access_status": "open",
        "source_url": "https://data-princeedwardisland-peigov.hub.arcgis.com/datasets/8a7638bdc09648358e0b6fd14be6117a",
        "download_url": "https://gis.princeedwardisland.ca/server/rest/services/Hosted/EFI2020/FeatureServer/0",
        "licence": "Open Government Licence - Prince Edward Island (portal default; item licence metadata blank)",
        "licence_url": "https://www.princeedwardisland.ca/en/information/finance/open-government-licence-prince-edward-island",
        "coverage": "Prince Edward Island forest land, 20 m x 20 m cells",
        "inventory_year": "2020 LiDAR with 2020-2021 ground plots",
        "format": "ArcGIS FeatureServer (polygon grid)",
        "spatial_level": "raster_cell_polygon",
        "species_field": "none",
        "age_field": "none",
        "height_field": "topht, domht",
        "volume_biomass_field": "gtv (gross total volume)",
        "has_species": False,
        "has_age": False,
        "has_height": True,
        "has_volume_biomass": True,
        "sampled": True,
        "sample_file": "samples/pe_efi_2020_sample.csv",
        "notes": "LiDAR-derived 20 m grid with height, basal area, QMD, stems/ha and gross total volume, but no species or age. Item-level licence is blank; portal default is OGL-PEI. Use as ancillary volume/height, not as a species-attributed stand inventory.",
    },
    {
        "province": "NL",
        "source_id": "nl_land_cover_forest",
        "source_name": "FAL - Land Cover - Newfoundland (Forest layer)",
        "publisher": "Newfoundland and Labrador Department of Fisheries, Forestry and Agriculture",
        "access_mode": "arcgis_rest",
        "access_status": "open",
        "source_url": "https://geohub-gnl.hub.arcgis.com/datasets/ea86e8a2a296425bb4eff8fb8ee40f97",
        "download_url": "https://services8.arcgis.com/aCyQID5qQcyrJMm2/arcgis/rest/services/FFA_LandCover/FeatureServer/6",
        "licence": "Open Government Licence - Newfoundland and Labrador",
        "licence_url": "https://opendata.gov.nl.ca/public/opendata/page/?page-id=licence",
        "coverage": "Newfoundland island forest land cover (Labrador has a separate service)",
        "inventory_year": "current service (published 2024, updated 2025)",
        "format": "ArcGIS FeatureServer",
        "spatial_level": "stand_polygon",
        "species_field": "none (FORESTTYPE numeric class only)",
        "age_field": "none",
        "height_field": "none",
        "volume_biomass_field": "none in the open polygon layer",
        "has_species": False,
        "has_age": False,
        "has_height": False,
        "has_volume_biomass": False,
        "sampled": True,
        "sample_file": "samples/nl_land_cover_forest_sample.csv",
        "notes": "Open polygon layer exposes FORESTTYPE, STOCKING and SITECODE only. Provincial inventory tables publish volume/biomass by Forest Management District and stratum, but the open spatial layer does not carry species, age, height or volume. Labrador land cover has the same limitation.",
    },
    {
        "province": "NT",
        "source_id": "nt_wooded_areas",
        "source_name": "NWT Wooded Areas (ATLAS Public)",
        "publisher": "Government of the Northwest Territories, Department of Environment and Climate Change",
        "access_mode": "arcgis_rest",
        "access_status": "open_partial",
        "source_url": "https://www.apps.geomatics.gov.nt.ca/arcgis/rest/services/GNWT_Operational/ATLAS_Public/MapServer/41",
        "download_url": "https://www.apps.geomatics.gov.nt.ca/arcgis/rest/services/GNWT_Operational/ATLAS_Public/MapServer/41",
        "licence": "Open Government Licence - Northwest Territories",
        "licence_url": "https://opendata.gov.nt.ca",
        "coverage": "Community vegetation outlines in the NWT; not a territory-wide forest inventory",
        "inventory_year": "current (periodically updated by community)",
        "format": "ArcGIS MapServer",
        "spatial_level": "stand_polygon",
        "species_field": "none (VEGETATIONTYPE broad class)",
        "age_field": "none",
        "height_field": "none",
        "volume_biomass_field": "none",
        "has_species": False,
        "has_age": False,
        "has_height": False,
        "has_volume_biomass": False,
        "sampled": True,
        "sample_file": "samples/nt_wooded_areas_sample.csv",
        "notes": "ATLAS Wooded Areas is community-scale vegetation mapping with VEGETATIONTYPE only. NWT FVI polygons and the MVI polygon/raster products are not directly open; MVI is available on request. The RSI open-data page did not expose a working API/download during this audit.",
    },
    {
        "province": "NU",
        "source_id": "nu_no_inventory",
        "source_name": "No territorial forest inventory identified",
        "publisher": "Government of Nunavut",
        "access_mode": "none",
        "access_status": "gap",
        "source_url": "",
        "download_url": "",
        "licence": "not applicable",
        "licence_url": "",
        "coverage": "Nunavut",
        "inventory_year": "not applicable",
        "format": "none",
        "spatial_level": "none",
        "species_field": "none",
        "age_field": "none",
        "height_field": "none",
        "volume_biomass_field": "none",
        "has_species": False,
        "has_age": False,
        "has_height": False,
        "has_volume_biomass": False,
        "sampled": False,
        "sample_file": "",
        "notes": "No open territorial stand/polygon forest inventory with species, age, height or volume was found. National products (NFI/SCANFI) cover Nunavut but are not a provincial/territorial inventory. NFI notes that Arctic and northern ecozones in Nunavut are not inventoried.",
    },
]

FIELDS = [
    ("ab_avi_crown", "SP1", "Leading overstorey species code", "code", "species"),
    ("ab_avi_crown", "SP1_PER", "Leading species proportion (tenths)", "tenths", "species"),
    ("ab_avi_crown", "HEIGHT", "Average stand height", "m", "height"),
    ("ab_avi_crown", "ORIGIN", "Year of stand origin", "year", "age"),
    ("ab_avi_crown", "PHOTO_YR", "Photography year", "year", "vintage"),
    ("ab_avi_crown", "DENSITY", "Crown density class", "class", "other"),
    ("ab_avi_crown", "STRUC", "Stand structure class", "class", "other"),
    ("ab_avi_crown", "Shape_Area", "Polygon area", "m2", "area"),
    ("sk_fri_forest_stands", "CSG", "Softwood/hardwood species group class", "class", "species"),
    ("sk_fri_forest_stands", "HEIGHT", "Lorey mean height", "m", "height"),
    ("sk_fri_forest_stands", "BA", "Basal area", "m2/ha", "other"),
    ("sk_fri_forest_stands", "QMD", "Quadratic mean diameter", "cm", "other"),
    ("sk_fri_forest_stands", "MSTEMS", "Merchantable stems", "stems/ha", "other"),
    ("sk_fri_forest_stands", "GBVOL", "Gross total volume", "m3/ha", "volume"),
    ("sk_fri_forest_stands", "GMVOL8", "Gross merchantable volume (8 cm top)", "m3/ha", "volume"),
    ("sk_fri_forest_stands", "CC", "Crown closure", "%", "other"),
    ("sk_fri_forest_stands", "INVENTORY_YEAR", "Inventory year", "year", "vintage"),
    ("mb_fri_1970s", "SPECIES", "Species composition string", "code", "species"),
    ("mb_fri_1970s", "SP1", "Species 1 code", "code", "species"),
    ("mb_fri_1970s", "SP1PER", "Species 1 proportion (tenths)", "tenths", "species"),
    ("mb_fri_1970s", "AGE_YRPHOTO", "Estimated stand age at photo year", "years", "age"),
    ("mb_fri_1970s", "YEARPHOTO", "Photography year", "year", "vintage"),
    ("mb_fri_1970s", "CROWN", "Crown closure class", "class", "other"),
    ("mb_fri_1970s", "COVER_TYPE", "Cover type (H/N/M)", "class", "other"),
    ("mb_fri_1970s", "AREA_HA", "Polygon area", "ha", "area"),
    ("ns_forest_inventory", "SPECIES", "Species composition string", "code", "species"),
    ("ns_forest_inventory", "SP1", "Species 1 code", "code", "species"),
    ("ns_forest_inventory", "SP1P", "Species 1 proportion (tenths)", "tenths", "species"),
    ("ns_forest_inventory", "HEIGHT", "First-storey height", "m", "height"),
    ("ns_forest_inventory", "CRNCL", "First-storey crown closure", "%", "other"),
    ("ns_forest_inventory", "TOTBA", "Total basal area", "m2/ha", "other"),
    ("ns_forest_inventory", "TOTMRVOL", "Total merchantable volume", "m3/ha", "volume"),
    ("ns_forest_inventory", "AVDI", "Average total diameter", "cm", "other"),
    ("ns_forest_inventory", "PHOTOYR", "Photography year", "year", "vintage"),
    ("ns_forest_inventory", "HECTARES", "Polygon area", "ha", "area"),
    ("pe_clui_2010_forest", "COVER1", "Species/cover 1 code", "code", "species"),
    ("pe_clui_2010_forest", "PER1", "Cover 1 proportion (tenths)", "tenths", "species"),
    ("pe_clui_2010_forest", "HEIGHT", "Stand height", "m", "height"),
    ("pe_clui_2010_forest", "CROWN", "Crown closure", "%", "other"),
    ("pe_clui_2010_forest", "DEV_STAGE", "Development stage", "class", "age"),
    ("pe_clui_2010_forest", "YEAR_", "Inventory year", "year", "vintage"),
    ("pe_clui_2010_forest", "HECTARES", "Polygon area", "ha", "area"),
    ("pe_efi_2020", "topht", "Top height", "m", "height"),
    ("pe_efi_2020", "domht", "Dominant/co-dominant height", "m", "height"),
    ("pe_efi_2020", "ba", "Basal area", "m2/ha", "other"),
    ("pe_efi_2020", "maxdbh", "Largest tree DBH", "cm", "other"),
    ("pe_efi_2020", "tphmerch", "Merchantable trees", "stems/ha", "other"),
    ("pe_efi_2020", "gtv", "Gross total volume", "m3/ha", "volume"),
    ("nl_land_cover_forest", "FORESTTYPE", "Forest type class", "class", "species"),
    ("nl_land_cover_forest", "STOCKING", "Stocking class", "class", "other"),
    ("nl_land_cover_forest", "SITECODE", "Site productivity code", "class", "other"),
    ("nl_land_cover_forest", "Shape__Area", "Polygon area", "m2", "area"),
    ("on_fri_packaged_products", "species composition", "Species composition in packaged product", "code", "species"),
    ("on_fri_packaged_products", "FRI_AGE_AT_INVENTORY", "Age at inventory in packaged product", "years", "age"),
    ("on_fri_packaged_products", "FRI_HEIGHT", "Stand height in packaged product", "m", "height"),
    ("nt_wooded_areas", "VEGETATIONTYPE", "Broad vegetation type", "class", "other"),
]

GAPS = [
    ("AB", "No volume or biomass attribute in AVI polygons", "Volume must be modelled from species/height/age and yield tables; the audit does not ingest or calibrate a model.", "Use AVI as species/age/height evidence and label any volume as model-derived.", "open_gap"),
    ("SK", "Stand vector has no explicit tree species and no age field", "Only CSG softwood/hardwood class is available in FSTAND; species and age rasters are separate products not audited here.", "Use FRI volume/height as stand-level evidence; do not claim species-specific SK attributes from FSTAND.", "open_gap"),
    ("MB", "Only the 1970s FRI is open as stand polygons; no height or volume", "Current 2021 FRI/FLI is a web-map tile layer plus summary statistics, not open polygons.", "Treat MB stand attributes as historical species/age evidence; current structure must come from national products or summary tables.", "open_gap"),
    ("ON", "Stand-attribute packaged products are not directly open", "FRI status/catalogue are open, but attribute packages require a LIDS account/order; no attribute sample was possible.", "Do not claim an ON open attribute sample; use FRI metadata for coverage/vintage and national products for attributes if needed.", "access_gap"),
    ("NS", "No explicit stand age", "Species, height, crown closure, basal area and volume are present; maturity/site classes are proxies only.", "Do not present NS age as observed; use height/species/volume and label age as unavailable.", "attribute_gap"),
    ("PE", "CLUI 2010 has no volume and no explicit age; EFI2020 has no species/age", "The two open products are complementary but neither is a complete species-age-height-volume stand inventory.", "Use CLUI for species/height and EFI for volume/height; label the combination and do not claim a single complete PEI inventory.", "attribute_gap"),
    ("NL", "Open polygon layer has no species, age, height or volume", "Only FORESTTYPE, STOCKING and SITECODE are open spatially; inventory tables are aggregate by FMD/stratum.", "Do not claim stand-level NL attributes from the open layer; use aggregate tables only as summary evidence.", "attribute_gap"),
    ("NT", "No open territory-wide stand inventory with species/age/height/volume", "ATLAS Wooded Areas is community-scale and broad-class; FVI/MVI are not directly open (MVI on request).", "Treat NT as a gap for stand attributes; do not infer FVI attributes from the community layer.", "access_gap"),
    ("NU", "No territorial forest inventory", "No open Nunavut stand inventory found; NFI notes Arctic/northern ecozones in Nunavut are not inventoried.", "Treat NU as a gap; national rasters are not a substitute for a territorial inventory.", "coverage_gap"),
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None, "mean": None}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "p25": round(_quantile(values, 0.25), 4),
        "median": round(statistics.median(values), 4),
        "p75": round(_quantile(values, 0.75), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.mean(values), 4),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=True)

    source_fields = list(SOURCES[0].keys()) + ["sample_rows"]
    source_rows = []
    for source in SOURCES:
        row = dict(source)
        sample = source["sample_file"]
        row["sample_rows"] = len(_read_csv(ARTIFACT / sample)) if sample else 0
        source_rows.append(row)
    _write_csv(ARTIFACT / "source_table.csv", source_rows, source_fields)

    field_rows = [
        {
            "source_id": source_id,
            "field_name": name,
            "description": description,
            "unit": unit,
            "attribute_role": role,
            "present_in_open_sample": True,
        }
        for source_id, name, description, unit, role in FIELDS
    ]
    _write_csv(
        ARTIFACT / "field_coverage.csv",
        field_rows,
        ["source_id", "field_name", "description", "unit", "attribute_role", "present_in_open_sample"],
    )

    stats_specs = {
        "ab_avi_crown": {
            "sample": "ab_avi_crown_sample.csv",
            "columns": [("HEIGHT", "m", "height"), ("AGE_AT_PHOTO", "years", "age"), ("Shape_Area", "m2", "area")],
        },
        "sk_fri_forest_stands": {
            "sample": "sk_fri_forest_stands_sample.csv",
            "columns": [
                ("HEIGHT", "m", "height"),
                ("BA", "m2/ha", "basal_area"),
                ("QMD", "cm", "quadratic_mean_diameter"),
                ("MSTEMS", "stems/ha", "stems"),
                ("GBVOL", "m3/ha", "volume"),
                ("GMVOL8", "m3/ha", "merchantable_volume"),
                ("CC", "%", "crown_closure"),
            ],
        },
        "mb_fri_1970s": {
            "sample": "mb_fri_1970s_sample.csv",
            "columns": [("AGE_YRPHOTO", "years", "age"), ("AREA_HA", "ha", "area")],
        },
        "ns_forest_inventory": {
            "sample": "ns_forest_inventory_sample.csv",
            "columns": [
                ("HEIGHT", "m", "height"),
                ("CRNCL", "%", "crown_closure"),
                ("TOTBA", "m2/ha", "basal_area"),
                ("TOTMRVOL", "m3/ha", "volume"),
                ("AVDI", "cm", "quadratic_mean_diameter"),
                ("HECTARES", "ha", "area"),
            ],
        },
        "pe_clui_2010_forest": {
            "sample": "pe_clui_2010_forest_sample.csv",
            "columns": [("HEIGHT", "m", "height"), ("CROWN", "%", "crown_closure"), ("HECTARES", "ha", "area")],
        },
        "pe_efi_2020": {
            "sample": "pe_efi_2020_sample.csv",
            "columns": [
                ("topht", "m", "height"),
                ("ba", "m2/ha", "basal_area"),
                ("maxdbh", "cm", "diameter"),
                ("tphmerch", "stems/ha", "stems"),
                ("gtv", "m3/ha", "volume"),
            ],
        },
        "nl_land_cover_forest": {
            "sample": "nl_land_cover_forest_sample.csv",
            "columns": [("Shape__Area", "m2", "area")],
        },
    }
    stat_rows = []
    for source_id, spec in stats_specs.items():
        rows = _read_csv(ARTIFACT / "samples" / spec["sample"])
        for column, unit, role in spec["columns"]:
            values = [value for value in (_num(row.get(column)) for row in rows) if value is not None]
            result = _stats(values)
            stat_rows.append(
                {
                    "source_id": source_id,
                    "attribute": column,
                    "unit": unit,
                    "attribute_role": role,
                    "sample_rows": len(rows),
                    **result,
                }
            )
    _write_csv(
        ARTIFACT / "sample_stats.csv",
        stat_rows,
        ["source_id", "attribute", "unit", "attribute_role", "sample_rows", "n", "min", "p25", "median", "p75", "max", "mean"],
    )

    species_rows = []
    species_specs = {
        "ab_avi_crown": ("ab_avi_crown_sample.csv", ["SP1"]),
        "sk_fri_forest_stands": ("sk_fri_forest_stands_sample.csv", ["CSG"]),
        "mb_fri_1970s": ("mb_fri_1970s_sample.csv", ["SPECIES"]),
        "ns_forest_inventory": ("ns_forest_inventory_sample.csv", ["SP1", "SP2", "SP3", "SP4"]),
        "pe_clui_2010_forest": ("pe_clui_2010_forest_sample.csv", ["COVER1", "COVER2", "COVER3", "COVER4", "COVER5"]),
        "nl_land_cover_forest": ("nl_land_cover_forest_sample.csv", ["FORESTTYPE"]),
    }
    for source_id, (sample, columns) in species_specs.items():
        rows = _read_csv(ARTIFACT / "samples" / sample)
        counter: Counter[str] = Counter()
        for row in rows:
            for column in columns:
                value = str(row.get(column) or "").strip()
                if value:
                    counter[value] += 1
        total = sum(counter.values())
        for code, count in counter.most_common():
            species_rows.append(
                {"source_id": source_id, "species_or_class": code, "count": count, "share": round(count / total, 4)}
            )
    _write_csv(
        ARTIFACT / "species_distribution.csv",
        species_rows,
        ["source_id", "species_or_class", "count", "share"],
    )

    gap_rows = [
        {"province": province, "gap": gap, "detail": detail, "mitigation": mitigation, "gap_type": gap_type}
        for province, gap, detail, mitigation, gap_type in GAPS
    ]
    _write_csv(ARTIFACT / "gaps.csv", gap_rows, ["province", "gap", "detail", "mitigation", "gap_type"])

    full_artifacts = {
        "ab_avi_crown": ("ab_avi_crown_fgdb.zip", 557041258, "e93572129f25c83911b73eadfacff12624ff6b08f2db4b311c1662196b665093"),
        "mb_fri_1970s": ("mb_fri_1970s_fgdb.zip", 306897586, "d8e39398629869d587c21baa0a6c40ee98352459ad19d46da6e59f4c36145125"),
        "ns_forest_inventory": ("ns_forest_inventory.zip", 1061848545, "94a9704ae21a9222e89c7f3b7c24a2ba9d50af92fd3493d9a17320d6b72630e4"),
    }
    manifest_rows = []
    for source in SOURCES:
        sample = source["sample_file"]
        if not sample:
            continue
        path = ARTIFACT / sample
        row = {
            "source_id": source["source_id"],
            "province": source["province"],
            "sample_file": sample,
            "sample_bytes": path.stat().st_size,
            "sample_rows": len(_read_csv(path)),
            "sample_sha256": _sha256(path),
            "retrieval_date": RETRIEVAL_DATE,
            "full_artifact": "",
            "full_artifact_bytes": "",
            "full_artifact_sha256": "",
            "download_url": source["download_url"],
        }
        if source["source_id"] in full_artifacts:
            name, size, digest = full_artifacts[source["source_id"]]
            row["full_artifact"] = name
            row["full_artifact_bytes"] = size
            row["full_artifact_sha256"] = digest
        manifest_rows.append(row)
    _write_csv(
        ARTIFACT / "manifest.csv",
        manifest_rows,
        [
            "source_id",
            "province",
            "sample_file",
            "sample_bytes",
            "sample_rows",
            "sample_sha256",
            "retrieval_date",
            "full_artifact",
            "full_artifact_bytes",
            "full_artifact_sha256",
            "download_url",
        ],
    )

    with (ARTIFACT / "source_table.json").open("w") as f:
        json.dump(SOURCES, f, indent=2)
        f.write("\n")

    readme = f"""# Provincial open forest-inventory audit v1

Retrieval date: {RETRIEVAL_DATE}

This artifact audits open provincial/territorial forest inventories for the nine
E2 jurisdictions (AB, SK, MB, ON, NS, PE, NL, NT, NU). It records source,
licence, format, coverage, vintage and stand-vs-tree level, and verifies a small
sample of each directly accessible layer. It does **not** ingest full layers and
does **not** produce model inputs or synthetic stand attributes.

## Files

- `source_table.csv` / `source_table.json`: one row per source, including
  licence, access mode, URL, coverage, year, format and spatial level.
- `field_coverage.csv`: audited fields and their attribute role.
- `sample_stats.csv`: min/p25/median/p75/max/mean of numeric attributes in the
  checked-in samples. No values are imputed.
- `species_distribution.csv`: species or class counts in the samples.
- `gaps.csv`: licence, access, attribute and coverage gaps.
- `manifest.csv`: sample and full-artifact SHA-256 provenance.
- `samples/`: small (100-500 row) verified samples. Metadata-only samples are
  labelled in `source_table.csv`.

## Source and licence summary

- AB: AVI Crown, Open Government Licence - Alberta, direct FGDB download.
- SK: FRI Forest Stands, SK Standard Unrestricted Use Data Licence v2.0,
  ArcGIS REST.
- MB: Manitoba Forest Inventory 1970s, OpenMB Information and Data Use License,
  direct FGDB download.
- ON: FRI packaged products, Open Government Licence - Ontario; stand
  attributes require a LIDS account/order (metadata only in this audit).
- NS: Nova Scotia Forest Inventory, Open Government Licence - Nova Scotia,
  direct shapefile download.
- PE: Corporate Landuse Inventory 2010 and EFI2020, Open Government Licence -
  Prince Edward Island (EFI item licence metadata is blank; portal default).
- NL: FAL Land Cover - Newfoundland, Open Government Licence - Newfoundland and
  Labrador, ArcGIS REST.
- NT: ATLAS Wooded Areas, Open Government Licence - Northwest Territories,
  ArcGIS REST; community-scale broad class only.
- NU: no territorial forest inventory identified.

## Attribute coverage

Stand-level species, age and height are available for AB and MB (MB height is
absent); NS has species, height and volume but no age; SK has height and volume
but only a softwood/hardwood class; PE combines CLUI species/height with EFI
volume/height; NL and NT open layers lack the core attributes; ON stand
attributes are not directly open; NU has no inventory. See `gaps.csv` for the
consequence of each gap.

No request, DUA, restricted data, official OSeCAN change, UI change or model
change is involved. All samples are observations of published open layers; no
value is synthetic.
"""
    (ARTIFACT / "README.md").write_text(readme)
    print(f"wrote {ARTIFACT}")


if __name__ == "__main__":
    build()
