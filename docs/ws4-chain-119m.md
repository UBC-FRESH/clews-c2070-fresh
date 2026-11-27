# WS4-driven 119 Mm³ harvest and chain comparison

## Status and non-claims

This is a **sidecar verification artifact** that uses the national 990-m WS4
estate to build a 119 Mm³ harvest schedule and to apply the frozen chain v5.1
per-province product/residue coefficients. It is a **`model_estimate`**
(`observed=false`, `policy_grade=false`, `national_claim=false`,
`ws4_derived=true`). The national WS4 dataset tables, the frozen
`national-facility-chain-v5.1` artifacts, and official OSeCAN are unchanged
(`chain_v51_unchanged=true`, `official_osecan_changed=false`).

The schedule is a whole-block arithmetic verification, not a sustainable
harvest, AAC, policy result, or investment case. No block volume is scaled or
fabricated.

## Artifacts

| Path | Role |
|---|---|
| `data/derived/ws4/scanfi-national-990m/schedules/ws4_119m_cc.csv` | 45-row whole-block CC schedule at offset 0 |
| `data/derived/ws4/scanfi-national-990m/runs/ws4_119m_cc_1y/` | One-year WS4 simulation run |
| `data/derived/national/facilities/ws4_chain_119m/` | Harvest, coefficients, product chain, comparison, summary, manifest, report |
| `workflows/national/facilities/ws4_chain_119m.py` | Fail-closed reproducer |
| `tests/test_ws4_chain_119m.py` | Schedule, conservation, coefficient, comparison, and WS4 smoke tests |

## Method

1. Read the national WS4 `fragments`, `blocks`, `tracks`, `features`, and
   `curves` tables.
2. Parse `AU(<province>)` from each natural/unmanaged track name and join the
   track's `feature.Yield.<province>.unmanaged.Total` curve.
3. Compute fragment volume as `area_ha × curve(age)` at the exact integer age;
   sum fragments to block volume. Block area is checked against fragment area.
4. Keep natural/unmanaged blocks with `offset >= 40`. Select whole blocks per
   province with a volume-aware greedy: fill in descending volume while the
   next block fits, then choose the unselected block with the smallest
   overshoot. A block is never selected twice.
5. Write the 45-row schedule and run one year of `CC` at offset 0 with
   `.venv-ws4/bin/ws4`. Read harvested volume and treated area from
   `product.HarvestedVolume.<province>.unmanaged.CC` and
   `product.Treated.<province>.unmanaged.CC`.
6. Apply the frozen v5.1 per-province coefficients (frozen
   `product_chain_by_province.csv` output ÷ frozen provincial harvest) on two
   bases:
   - `ws4_achieved`: realised WS4 harvest;
   - `scaled_to_119m_control`: one national factor preserving WS4 provincial
     shares and matching the 118.9773391 Mm³ control total.
7. Compare national totals with frozen v5.1. The frozen targets are checked in
   the builder: solid 37.674 Mm³, pulp 12.066 M odt, process residue
   498.751 PJ, harvest gross 444.411 PJ, harvest 60% 266.646 PJ, and total
   gross 943.162 PJ.

`ws4 validate` returned `Dataset is valid`. The simulation was a one-year run
at offset 0; the existing run was reused on rebuild because its schedule
matched in content.

## Achieved provincial harvest

| Province | Target (m³) | Achieved (m³) | Delta (m³) | Area (ha) | Blocks | Mean age | Curves |
|---|---:|---:|---:|---:|---:|---:|---:|
| BC | 39,978,577 | 39,978,582.326 | 5.326 | 291,643.47 | 8 | 83.75 | 7 |
| AB | 22,891,404 | 22,891,408.290 | 4.290 | 182,523.78 | 4 | 94.35 | 4 |
| SK | 4,344,697.6 | 4,344,700.599 | 2.999 | 103,128.75 | 3 | 45.86 | 3 |
| MB | 1,135,235.8 | 1,135,237.870 | 2.070 | 18,734.04 | 3 | 63.74 | 3 |
| ON | 13,493,596 | 13,493,597.979 | 1.979 | 70,730.37 | 4 | 72.41 | 4 |
| QC | 26,674,334 | 26,674,336.267 | 2.267 | 325,022.04 | 4 | 122.68 | 4 |
| NB | 6,234,700 | 6,234,713.366 | 13.366 | 48,731.22 | 5 | 61.85 | 5 |
| NS | 2,388,245 | 2,388,247.670 | 2.670 | 17,590.59 | 4 | 73.72 | 4 |
| PE | 302,210.4 | 302,220.656 | 10.256 | 2,304.90 | 3 | 56.17 | 3 |
| NL | 1,454,037.5 | 1,454,040.795 | 3.295 | 21,849.03 | 3 | 94.99 | 3 |
| YT | 43,925.8 | 43,929.128 | 3.328 | 851.94 | 2 | 84.32 | 1 |
| NT | 36,376 | 36,377.933 | 1.933 | 628.92 | 2 | 64.13 | 2 |
| NU | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **National** | **118,977,339.1** | **118,977,392.880** | **53.780** | **1,083,739.05** | **45** | | |

The national overshoot is 53.780 m³ (0.000045%). The largest provincial
absolute overshoot is NB at 13.366 m³; the largest percentage overshoot is YT
at 0.00758%. These are block-granularity overshoots, not scaled targets.

## Chain totals versus frozen v5.1

| Metric | Unit | Frozen v5.1 | WS4 achieved | WS4 delta | Scaled | Scaled delta |
|---|---|---:|---:|---:|---:|---:|
| Harvest | m³ | 118,977,339.100 | 118,977,392.880 | +53.780 | 118,977,339.100 | 0.000 |
| Solid product | m³ | 37,673,626.761 | 37,673,641.463 | +14.702 | 37,673,624.433 | −2.328 |
| Pulp product | odt | 12,066,311.270 | 12,066,317.395 | +6.125 | 12,066,311.941 | +0.671 |
| Process residue | PJ | 498.750917 | 498.751140 | +0.000223 | 498.750915 | −0.000002 |
| Harvest gross | PJ | 444.410766 | 444.410997 | +0.000231 | 444.410796 | +0.000030 |
| Harvest 60% | PJ | 266.646460 | 266.646598 | +0.000138 | 266.646477 | +0.000018 |
| Total gross | PJ | 943.161683 | 943.162137 | +0.000454 | 943.161711 | +0.000028 |

`solid_product_m3` is sawlog product plus panel product. Process residue and
harvest residue remain separate streams. PJ values use 18.5 GJ/ODT.

## OSeCAN status

The residue curve and OSeCAN handoff were **not rebuilt**. The existing curve
and handoff workflows are frozen to the v5.1 facility assignment and
province-to-zone weight inputs. A WS4-basis rerun would require a facility
assignment and province-to-zone reallocation outside this WS4 schedule scope.
Only chain totals were rebuilt; no tier position is claimed. Official OSeCAN
is unchanged.

## Caveats

- Whole blocks are selected. Provincial overshoot is block-granularity
  overshoot; blocks are not split, scaled, or fabricated.
- 990-m forest-area fragments and RSYC group curves are inventory
  realisations, not operable, tenure-feasible, or economically feasible areas.
- One `CC` treatment at offset 0 is simulated. The managed track shares the
  natural curve; planting response, silviculture, fertilization, and genetic
  gain are not modelled.
- Facility assignment, mill capacity, transport, and interprovincial trade are
  not re-run. Products and residue are coefficient applications by origin
  province.
- The scaled basis uses one national factor that preserves WS4 provincial
  shares; it is not per-province target scaling.
- 0.60 harvest recovery is a reference case, not a calibrated recovery factor.

## Verification

| Check | Result |
|---|---|
| `ws4 validate` | exit 0, `Dataset is valid` |
| Schedule uniqueness and eligibility | 45 unique rows; all natural/unmanaged, offset ≥ 40 |
| Harvest/area conservation | WS4 run equals selected block volume/area by province |
| Target/overshoot arithmetic | 13 provinces; national overshoot 53.780 m³ |
| Coefficient application | Both bases reproduce frozen coefficients |
| Comparison arithmetic | National totals and deltas match summary |
| NaN/negative scan | No NaN or negative numeric value |
| WS4 validate/simulate smoke | 1-block one-year run reproduces block volume |
| Unexpected `D` status | None in parent or data repository at build time |

Reproduce:

```bash
.venv/bin/python -m workflows.national.facilities.ws4_chain_119m
.venv/bin/pytest -q tests/test_ws4_chain_119m.py
```
