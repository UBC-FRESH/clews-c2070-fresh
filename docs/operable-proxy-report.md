<!-- Committed copy of data/derived/national/managed_landbase/operable_proxy/OPERABLE_PROXY_REPORT.md -->

# National Operable Merchantable-Forest Proxy

**Status: screening proxy. This is not a legal timber harvesting landbase (THLB), not an AAC landbase, and not an operability or access model.**

Generated: 2026-11-20T15:01:27+00:00  
Runtime: 81.3 s  
Grid: 30 m SCANFI pixels aggregated to 990 m blocks.

## Method

1. Mask SCANFI v2 NFI land cover to treed classes 5 (broadleaf), 6 (conifer), and 7 (mixed).
2. Apply the default merchantability screen: height >= 5 m, aboveground biomass >= 20 Mg/ha, and crown closure >= 10%.
3. Read the existing managed-landbase proxy by province/ecoprovince (tenure union minus CPCAD) and divide it by the SCANFI treed area in the same province-ecoprovince. The source `managed_share` column uses an ecoprovincial denominator, which understates the share when an ecoprovince spans provinces; the managed numerator is unchanged.
4. Apply that share uniformly to the merchantable area. Tenure polygons were **not** re-intersected with the merchantable raster; this is a share-based approximation.
5. Report low and high threshold sensitivity cases, plus each threshold in isolation.

The managed share is therefore a *landbase-share* adjustment, not a spatial tenure overlay. It assumes managed forest is distributed like all forest inside each province-ecoprovince. The source `managed_share` column is divided by ecoprovincial forest area; because several ecoprovinces span provincial boundaries, the share is recomputed here as managed proxy divided by SCANFI treed area in the same province-ecoprovince.

## National area results

| Case | Thresholds | Area (Mha) | Share of treed |
|---|---|---:|---:|
| low | h >= 3 m, b >= 10 Mg/ha, c >= 5% | 383.38 | 90.7% |
| base | h >= 5 m, b >= 20 Mg/ha, c >= 10% | 332.60 | 78.7% |
| high | h >= 7 m, b >= 30 Mg/ha, c >= 20% | 267.94 | 63.4% |

SCANFI treed area: **422.48 Mha**.
Default (base) merchantable area: **332.60 Mha**.
Default operable proxy: **144.69 Mha** (87.0% of the existing 166.28 Mha managed proxy).

## Base case by province/territory

| Province | SCANFI treed (Mha) | Merchantable (Mha) | Managed proxy (Mha) | Operable proxy (Mha) | Effective managed share |
|---|---:|---:|---:|---:|---:|
| QC | 81.66 | 72.18 | 28.27 | 26.16 | 32.0% |
| BC | 66.61 | 60.43 | 54.37 | 49.26 | 74.0% |
| ON | 68.52 | 53.48 | 33.81 | 30.01 | 43.8% |
| AB | 38.35 | 32.03 | 21.79 | 18.40 | 48.0% |
| NT | 43.46 | 24.53 | 0.82 | 0.64 | 1.5% |
| MB | 33.75 | 23.87 | 6.90 | 5.49 | 16.3% |
| NL | 23.53 | 21.43 | 1.11 | 0.88 | 3.8% |
| YT | 25.91 | 17.73 | 8.07 | 5.76 | 22.2% |
| SK | 26.16 | 14.51 | 7.49 | 4.93 | 18.9% |
| NB | 6.69 | 5.62 | 2.56 | 2.14 | 31.9% |
| NS | 4.73 | 4.41 | 1.10 | 1.02 | 21.7% |
| NU | 2.83 | 2.11 | missing | missing | missing |
| PE | 0.27 | 0.25 | missing | missing | missing |

## Comparison to existing managed proxy and NFI

- Existing managed proxy (tenure union minus CPCAD): **166.28 Mha**.
- Base merchantable area: **332.60 Mha** (0.90x NFI forest land).
- Base operable proxy: **144.69 Mha** (0.39x NFI forest land; 87.0% of the managed proxy).
- NFI Table 4.0 forest land: **369.02 Mha**.
- SCANFI treed / NFI forest land: **1.14x**. SCANFI treed classes include northern and non-inventory treed land outside the NFI forest-land definition, so a ratio above 1 is expected.

## Sensitivity

| Case | National area (Mha) | Share of treed |
|---|---:|---:|
| low (height >= 3 m, biomass >= 10 Mg/ha, closure >= 5%) | 383.38 | 90.7% |
| base (height >= 5 m, biomass >= 20 Mg/ha, closure >= 10%) | 332.60 | 78.7% |
| high (height >= 7 m, biomass >= 30 Mg/ha, closure >= 20%) | 267.94 | 63.4% |
| height_ge_5 (treed and height >= 5 m) | 371.52 | 87.9% |
| biomass_ge_20 (treed and biomass >= 20 Mg/ha) | 356.32 | 84.3% |
| closure_ge_10 (treed and crown closure >= 10%) | 420.88 | 99.6% |

The single-constraint rows are not alternatives to the base case; they show how much area each default threshold removes on its own. The low/high rows are the defensible envelope for the combined screen.

## Validation

- Strip-level pixel conservation: max residual **0** pixels.
- National treed area vs national area-weights product: **0.000 ha** difference.
- No NaN in treed/merchantable area fields: **True**.
- No negative area fields: **True**.
- Merchantable <= treed in every unit: **True**.
- Managed share <= 1 in every unit: **True**.
- Operable <= merchantable where defined: **True**.
- Threshold ordering low >= base >= high: **True**.
- Province sums equal ecoprovince sums: **True**.
- National operable proxy <= existing managed proxy: **True**.

## Gaps and caveats

- **No legal THLB**: tenure boundaries are rights areas, not harvest-landbase determinations.
- **No operability or slope constraint**: steep terrain, inoperable sites, and site degradation are not removed.
- **No road/access constraint**: NRN province bundles exist in `data/raw/nrn/` for all 13 jurisdictions but were not integrated into this run; distance-to-road and access classes remain a gap.
- **No age, disturbance, or silvicultural constraint**: recent burns, insect mortality, and non-commercial stands are not removed beyond the SCANFI attributes used here.
- **Share-based tenure approximation**: the managed share is uniform inside each province-ecoprovince, so the spatial pattern of tenure is not represented. The share is the existing managed-proxy numerator over local SCANFI treed area; it is not the source file's ecoprovincial `managed_share` column.
- **SCANFI uncertainty**: the thresholds are applied to a 30 m prediction surface, not a provincial inventory.
- **Missing jurisdictions**: NU and PE have no accepted 2016 tenure source, so their managed and operable values are blank, not zero.
- **BC upper bound**: the 2016 tenure compilation covers almost all BC forest; the BC managed share is an upper bound, not a licence-specific footprint.

## Outputs

- `operable_proxy_by_province.csv`: province/territory x threshold case.
- `operable_proxy_by_ecoprovince.csv`: province x ecoprovince x threshold case.
- `operable_proxy_summary.json`: thresholds, national totals, sensitivity, validation, inputs.
- `manifest.json`: input and output hashes, software versions, runtime, validation.

## Reproduction

```bash
.venv/bin/python scripts/national/build_operable_proxy.py \
  --out-dir data/derived/national/managed_landbase/operable_proxy
```

Raw SCANFI, tenure, CPCAD, boundary, and RSYC payloads remain in the `data` submodule and are not committed to the parent repository.
