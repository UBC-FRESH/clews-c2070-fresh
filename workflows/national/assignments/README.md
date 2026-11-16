# National facility assignment: Stage A

This package is the Stage A transportation/allocation layer between national
assortment supply and the Stage B forest-sector network-flow model.

```
TSA24 assortment matrix + residue ledger
  -> supply offers by dimension-only grade          (fixtures.py)
  -> FFCM BC facility lines, normalized capacity    (fixtures.py)
  -> diagnostic road transport + contracts          (fixtures.py)
  -> PuLP transportation LP                         (model.py)
  -> assignment, utilization, contract, unassigned,
     objective, and validation tables               (run_tsa24.py)
  -> Stage B supply_offer / technology / transport_link subset
```

Stage A answers a narrow question: given a supply ledger and a facility network,
which facility should receive which grade at what delivered cost, and what is
left unassigned? Stage B continues the flow through technology conversion,
product markets, demand, and multi-period network dynamics. Stage A does not
invent demand rows, market clearing, or line-level scheduling.

## Scope

- Geography: BC facilities, TSA24/Prince George supply origin.
- Facility classes: `SAW`, `PAP`, `PEL`, `BIO`.
- Period: one period in the pilot, but every record carries a period key.
- Decision variables: continuous `x[s,f,g,t]` and unassigned slack `u[s,g,t]`.
  No binaries in the pilot; `y[f,l,t]` is omitted because alternative feedstock
  lines are aggregated into grade-specific capacity and the facility nameplate
  remains the total cap.
- Objective: maximise product revenue minus roadside supply, transport, gate,
  processing, and unassigned penalty. A cost-min diagnostic is not used.
- Units: all model quantities are m3 green. ODT is carried on offers for
  reporting and converted only at declared boundaries.

The base pilot area is **1,000 ha**. It is a contract/data-shape diagnostic, not
a TSA24 supply estimate. The optional `--sensitivity-ws4-quota` run at
306,047.3 ha is a **period-scale** sensitivity and is labeled as such; it must
not be read as an annual TSA supply forecast.

## Evidence tiers

| Tier | Meaning | Examples |
|---|---|---|
| `parsed_archive` | Directly parsed FFCM archive table | `facilities.csv`, `processing_lines.csv`, `routes.csv` |
| `derived_tsa24_matrix` | TSA24 assortment matrix / residue ledger | grade volumes, matrix prices |
| `ledger_assumption` | Residue ledger assumption | 0.45 ODT/m3, 0.45 wet-basis moisture |
| `diagnostic` | Documented pilot assumption, not a source value | 150 km access leg, diagnostic contracts, penalty |
| `assumption` | Generic placeholder | reserved hog-fuel grade with no supply |

Every non-archive conversion is written to `assumptions.csv`; rejected lines
and rejected transport links are written to `line_screen.csv` and
`transport_screen.csv`.

## Schema (`schema.py`)

| Type | Grain | Key fields |
|---|---|---|
| `FeedstockGrade` | grade | unit, accepted sorts, density, moisture, evidence |
| `SupplyOffer` | offer × period | grade, origin, volume m3, density, roadside cost |
| `FacilityOffer` | facility × grade × period | grade capacity, processing cost, product revenue |
| `FacilityCapacity` | facility | nameplate m3/y, grade offers |
| `TransportLink` | origin × facility × grade × period | distance, CAD/m3, optional capacity |
| `Contract` | contract × period | accepted grades, min/max m3, gate cost, diagnostic flag |
| `Assignment` | solved assignment | volume, cost components, revenue, contribution |
| `UnassignedSupply` | leftover offer | volume, reason, penalty |
| `AssignmentResult` | run | assignments, unassigned, utilization, contracts, objective, validation |
| `AssignmentInputs` | parsed problem | periods, grades, supplies, facilities, links, contracts |

`AssignmentInputs` validates cross-table references once. `model.py` and
`validate.py` therefore consume trusted objects, not raw CSV strings. Invalid
states fail immediately with `AssignmentError`.

## LP formulation (`model.py`)

Sets: supplies `S`, facilities `F`, grades `G`, periods `T`, links `L`.

Variables:

- `x[s,f,g,t] >= 0`: m3 green assigned from supply `s` to facility `f`.
- `u[s,g,t] >= 0`: m3 green unassigned.

Objective:

```
max  sum (R_fg - C_s - H_sf - G_fg - P_fg) x[s,f,g,t]
     - penalty * sum u[s,g,t]
```

where `R` is facility product revenue per m3, `C` roadside supply cost, `H`
transport cost, `G` contract gate cost, and `P` line processing cost.

Constraints:

1. Supply balance: `sum_f x + u = V_s`.
2. Facility nameplate: `sum_{s,g} x <= nameplate_f`.
3. Grade capacity: `sum_s x[s,f,g,t] <= capacity_fg`.
4. Contract minimum and maximum per facility/period.
5. Optional moisture blend: `sum_s M_s x <= limit_f * sum_s x`.
6. Optional transport-link capacity: `x <= link_capacity`.
7. Non-negativity.

The LP is MILP-ready: binary/line-activation variables can be added later
without changing the table contract, but the pilot deliberately has none.

## Fixture construction (`fixtures.py`)

### Supply

- Read `assortment_matrix.csv` detail rows; the `is_total` row is a volume check.
- Group by species × product/sort × top class.
- Multiply the per-hectare matrix volume by `area_ha`.
- Map product to grade:
  - `sawlog` -> `SAWLOG`
  - `pulpwood` -> `PULP`
  - `other` -> `OTHER_LOG`
  - `residue` -> `RESIDUE`
  - `HOGFUEL` is reserved but has no supply in the TSA24 matrix.
- Roadside cost is the matrix `price_cad_m3` proxy. It is not a harvest or
  stumpage cost. Residue has no positive matrix price and therefore costs zero
  in the base run.
- Residue ODT uses the ledger's 0.45 ODT/m3 density and 0.45 wet-basis
  moisture. Log moisture also uses 0.45 because the matrix has no log moisture
  field; every affected row is flagged.

Assortment sorts are **dimension-only**. No BC official grade equivalence is
claimed. Facility eligibility is a feedstock-class/product screen, not a grade
quality test.

### Facilities

- Keep BC `SAW`, `PAP`, `PEL`, `BIO` facilities from `facilities.csv`.
- Normalize `infeed_rate`:
  - `m3/y` -> m3/y directly.
  - `odt/y` -> m3/y at `1 / 0.45`, the ledger density.
  - `bdmt/y` -> rejected. PAP bdmt/y lines are duplicates of the paired m3/y
    log line; the m3/y line is used.
  - `admt/y`, `mbf/y`, `kwh/y`, and other units -> rejected for capacity.
- Group lines by facility × primary product. Product capacity is the maximum
  normalized line capacity across feedstock alternatives; facility nameplate is
  the sum across products. This avoids counting the same FFCM line once per
  feedstock alternative.
- Grade capacity is the sum, across products, of the maximum capacity of lines
  that accept that grade. Grade capacities are alternatives, not additive
  across grades; the facility nameplate remains the binding total.
- Processing cost is converted to CAD/m3 **with the same line's outfeed ratio**
  when the archive unit is `$/mbf`, `$/kwh`, `$/admt`, `$/odt`, or `$/t`.
  No global board-foot, kWh, or dry-mass conversion is assumed.
- Product revenue uses parsed commodity prices and each line's outfeed mix.
  Mass prices applied to `odt/y` or `admt/y` outfeed carry an explicit
  `mass_basis_odt_or_admt_treated_as_tonne` flag.

### Transport

The parsed FFCM route graph connects facilities to transload (`TL`) and water
(`W`) nodes, but it has no TSA24 supply node and only eight BC facilities have
coordinates. A facility-to-TSA24 route therefore does not exist in the archive.

The pilot uses a documented diagnostic:

```
distance = 150 km TSA24 access leg
         + minimum FFCM facility-to-TL ROAD distance
```

The facility leg is from `routes.csv`; the access leg is an assumption. This is
not Euclidean distance and not a production route. Facilities without an FFCM
road route are excluded from the transport layer rather than given a fabricated
distance.

Road cost uses `BC_logging_truck` for logs and `BC_residue_truck` for residue,
with archive fuel and operating rates and truck capacities. No transload cost is
applied because the diagnostic path is direct road. Route capacity is absent
from the parsed route table, so transport links are uncapped.

### Contracts

No populated contract table exists. Each facility receives a diagnostic
contract with `min=0`, `max=nameplate`, and `gate=0`. `Contract.diagnostic` is
true and the notes say to replace it with real terms. The test suite covers
non-zero minima and tighter maxima.

## Validation (`validate.py`)

The validator recomputes, rather than trusts:

- volume conservation: assigned + unassigned = supply for every offer;
- facility and grade capacity;
- grade eligibility and transport-link existence;
- contract minimum and maximum;
- unassigned reasons;
- moisture blend limits;
- unit consistency and documented capacity conversions;
- finite values;
- objective decomposition against the solver objective;
- facility utilization and contract report completeness.

A failed check is a hard failure. `run_tsa24.py` writes the validation report
and exits non-zero.

## Running

```bash
.venv/bin/python -m workflows.national.assignments.run_tsa24
```

Optional period-scale sensitivity:

```bash
.venv/bin/python -m workflows.national.assignments.run_tsa24 \
  --sensitivity-ws4-quota
```

Tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_assignment.py' -v
```

## Outputs

The pilot writes to `data/derived/national/assignments/tsa24_pilot/`:

| File | Contents |
|---|---|
| `facility_assignment.csv` | positive assignments with cost/revenue decomposition |
| `facility_utilization.csv` | facility nameplate, assigned, utilization, binding flag |
| `facility_grade_utilization.csv` | facility × grade capacity and assignment |
| `contract_compliance.csv` | contract bounds and compliance |
| `unassigned_supply.csv` | leftover volume and reason |
| `objective_decomposition.csv` | revenue and each cost component |
| `validation_summary.json` | recomputed validation checks and data gaps |
| `ASSIGNMENT_REPORT.md` | pilot narrative and tables |
| `manifest.json` | config, source/output hashes, counts, assumptions |
| `supply_offers.csv` | fixture supply table |
| `facility_capacity.csv` | fixture facility total table |
| `facility_offers.csv` | fixture facility-grade table |
| `transport_links.csv` | fixture transport table |
| `contracts.csv` | fixture contract table |
| `assumptions.csv` | all diagnostic assumptions |
| `line_screen.csv` | accepted/rejected processing lines |
| `transport_screen.csv` | accepted/rejected facility routes |
| `grades.csv` | grade definitions |
| `stage_b_supply_offer.csv` | Stage B `supply_offer` subset |
| `stage_b_technology.csv` | Stage B `technology` subset |
| `stage_b_transport_link.csv` | Stage B `transport_link` subset |

The Stage B subset matches the column names in `fresh_fibre/graph.py`.
`stage_b_transport_link.csv` has one capacity-weighted origin-destination row
per facility because the current Stage B transport table has no grade/commodity
dimension; the Stage A grade-specific truck-cost split remains in
`transport_links.csv`. Stage B still needs its own scenario, commodity,
network_node, technology_flow, market, and provenance tables; Stage A does not
fabricate them.

## Known gaps

- No TSA24 supply-node-to-transload route; the 150 km access leg is the main
  transport uncertainty.
- Only 8 of 179 BC facilities have geocoding; Euclidean distance is not used.
- No route or transload capacity in the parsed route table.
- No populated contract table.
- Mixed capacity units: `bdmt/y` is rejected, `odt/y` is normalized at 0.45,
  and `mbf`/`kwh` are used only through same-line outfeed ratios.
- No hog-fuel supply in the TSA24 matrix; the grade is reserved.
- No mill-residue (sawdust/woodchips) supply; only logging residue.
- One period, no inventory dynamics, no demand/market clearing.
- No MKRF input or output.
