# CanLaBS v2 Source Review

**Status:** Accepted (transfer verified; archive pending)  
**Review Date:** 2026-08-08  
**Reviewer:** Supervisor (Phase 7 Source Triage)

## Source Metadata

- **Title:** Canada Landsat Burned Severity (CanLaBS v2)
- **Publisher:** Natural Resources Canada (NRCan)
- **DOI:** https://doi.org/10.23687/2af751e7-79f9-4da8-9b45-14688818dca3
- **Citation:** Guindon L., Correia D., Perbet P. 2026. Canada Landsat Burned Severity (CanLaBS v2): a Canada-wide Landsat-based 30-m resolution product of burned severity since 1985.
- **Methodology Reference:** Guindon et al. (2021). "Trends in wildfire burn severity across Canada, 1985 to 2015." Canadian Journal of Forest Research 51 (9): 1230–1244. https://doi.org/10.1139/cjfr-2020-0353
- **Temporal Coverage:** 1985–2024
- **Spatial Coverage:** Canada
- **Resolution:** 30 m
- **Projection:** NAD83 Canada Lambert (EPSG:3978)
  - proj4: `+proj=lcc +lat_0=0 +lon_0=-95 +lat_1=49 +lat_2=77 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs`
- **License:** Open Government Licence - Canada (confirm against the official release statement before mirroring)
- **Access Path:** https://ftp.maps.canada.ca/pub/nrcan_rncan/Forest-fires_Incendie-de-foret/CanLaBS_v2-Burned_Severity-Severite_des_feux/

## Layer Inventory

| Layer | Filename | Size | Description |
|-------|----------|------|-------------|
| dNBR Severity | `CanLaBS_1985_2024_v20260121.tif` | 1.6 GB | dNBR values for all burnt pixels according to NBAC |
| Salvage Mask | `CanLaBS_salvageMask_1985_2024_v20260121.tif` | 98 MB | Binary layer where '1' identifies pixels where salvage logging occurred |
| NBAC Fire Year | `NBAC_MRB_1972to2024_reproj.tif` | 335 MB | NBAC fire year composite |

Auxiliary files: `.aux.xml` (metadata) and `.ovr` (overviews) for each layer.

## Validation Results

### Official Readme
- [x] Readme fetched and validated from official FTP directory
- [x] Three layers confirmed matching inventory above
- [x] Projection confirmed: NAD83 Canada Lambert
- [x] Coverage confirmed: 1985–2024, Canada-wide

### Fresh Transfer — Primary Severity Layer

- **URL:** `https://ftp.maps.canada.ca/pub/nrcan_rncan/Forest-fires_Incendie-de-foret/CanLaBS_v2-Burned_Severity-Severite_des_feux/CanLaBS_1985_2024_v20260121.tif`
- **Method:** `curl -C - -L --progress-bar`
- **File:** `tmp/canlabs_v2_fresh/CanLaBS_1985_2024_v20260121_fresh.tif`
- **HTTP status:** 200 OK
- **Server Content-Length:** 1,751,083,497 bytes
- **Actual bytes received:** 1,751,083,497 bytes
- **Size mismatch:** 0 bytes
- **SHA-256:** `152ba33cca7c473ddc6c7d804d0855581f6d734b50f300637c8f2ee040ac5e8b`
- **ETag:** `685f69e9-64bce58f41000`
- **Last-Modified:** `Fri, 27 Feb 2026 13:38:08 GMT`
- **Structural validation:** `file` identifies BigTIFF; rasterio opens it as GTiff with one int16 band, dimensions 178400 x 119100, Deflate compression, nodata -32768, and EPSG:3978.
- **Prior file:** `tmp/canlabs_v2_severity.tif` remains preserved at 1,787,556,259 bytes for comparison and was not used.

The fresh transfer passes the server byte-count and local structural-integrity checks. The official FTP endpoint does not advertise a publisher SHA-256, so the recorded hash is a local transfer-integrity record, not a publisher-attested checksum. Retain the URL, response metadata, byte count, hash, and acquisition date with any controlled model use.

**Archive status:** The fresh verified copy remains in `tmp/`; it has not been added to the `data` submodule or archived through DataLad.

### Companion Layers

- Salvage mask and NBAC fire-year layers are not yet acquired.

## Risks and Limitations

1. **No publisher checksum:** The official endpoint does not advertise a SHA-256 for independent comparison. The recorded hash proves the bytes retrieved in this transfer, not publisher attestation.
2. **Archive pending:** Preserve the transfer manifest and obtain archive authorization before adding the primary payload to the `data` submodule.
3. **License confirmation:** Confirm the applicable license statement before mirroring.
4. **Temporal scope:** Covers 1985–2024; verify whether 2025 data is available separately.
5. **Integration:** Cross-reference with the NBAC 2020 sample for spatial consistency during model validation.

## Registry Reconciliation — 2026-08-08

The source remains represented as accepted in `metadata/required-datasets.yaml`. The fresh primary transfer matches the official server Content-Length exactly, and its local SHA-256 plus HTTP response metadata are recorded in the registry. The fresh file is suitable for controlled model evaluation with that transfer record attached; it remains outside the canonical archive until its manifest is preserved and DataLad archive authorization is available. No publisher-attested SHA-256 is advertised.

## Recommended Next Actions

1. Preserve the transfer manifest containing URL, HTTP response metadata, byte count, SHA-256, and acquisition date.
2. Archive the verified payload to `data/raw/nrcan/canlabs_v2/` using DataLad when archive authorization is available.
3. Cross-reference with the NBAC 2020 sample for spatial consistency during model validation.
