# National facility assignment: Stage A

This package is the Stage A transportation/allocation layer between national
assortment supply and the Stage B forest-sector network-flow model.

```
TSA24 assortment matrix + residue ledger
  -> supply offers by dimension-only grade          (fixtures.py)
  -> BC facility table + coordinate provenance      (facilities.py)
  -> unit-normalized facility capacity              (units.py)
  -> FFCM TL graph transport + transload costs      (transport.py)
  -> diagnostic contracts                           (fixtures.py)
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
- Units: all model quantities are m3 green. ODT and dry-mass units are carried
  on offers for reporting and converted only at declared boundaries. `units.py`
  defines `m3_green`, `odt`, `bdmt`, `admt`, `mbf`, `kwh`, `mwh`, and `gj`;
  ambiguous conversions fail loudly rather than silently converting.

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
| `graph_proxy` | Documented FFCM graph proxy | TSA24 Prince George mill anchor, TL hub coordinate proxy |
| `diagnostic` | Documented pilot assumption, not a source value | diagnostic contracts, unassigned penalty |
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

- Build one row per BC facility in `facilities_bc.csv`, including out-of-scope
  `PLY`/`OSB`/`NSP` rows and any zero/blank-capacity facility. The LP keeps only
  BC `SAW`, `PAP`, `PEL`, and `BIO` with positive nameplate.
- Resolve coordinates in this order: Mills DB (`coord_source=mills_db`), CFS
  `suppliers.dbf` (`suppliers_dbf`), an FFCM TL/water hub anchor
  (`tl_anchor_proxy`), then `missing`. The hub anchor is the median coordinate
  of geocoded facilities directly attached to the same hub; it is a
  low-confidence proxy, not a Euclidean guess.
- Normalize `infeed_rate` through `units.py`:
  - `m3/y` -> m3/y directly.
  - `odt/y` and `bdmt/y` -> m3/y at the declared 0.45 ODT/m3 density.
  - `admt/y` -> rejected unless an air-dry moisture is declared.
  - `mbf/y` -> rejected unless a recovery factor (m3 per mbf) is declared.
  - `kwh/y`, `mwh/y`, `gj/y` -> rejected for capacity; energy cannot become
    volume without a declared energy density.
- Group lines by facility × primary product. Product capacity is the maximum
  normalized line capacity across feedstock alternatives; facility nameplate is
  the sum across products. This avoids counting the same FFCM line once per
  feedstock alternative. PAP `bdmt/y` alternatives convert below the paired
  `m3/y` log line, so the nameplate is unchanged.
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
(`W`) nodes, but it has no TSA24 supply node. The pilot therefore uses a
documented supply anchor: the Mills-DB geocoded Prince George sawmill
`BC-SAW-506` (Canfor Prince George; 53.99, -122.69). This is a proxy, not a
production route. The earlier `BC-SAW-488` anchor was a code collision: the
FFCM `BC-SAW-488` is Canfor Houston, while the Mills DB `BC-SAW488` is the
Prince George sawmill now represented by `BC-SAW-506`.

From that anchor the builder uses:

```
anchor -> TL access leg from routes.csv
        -> TL-TL shortest path from routes.csv
        -> TL -> facility route from routes.csv
```

`transport_links_bc.csv` records the path nodes, path legs, distance, mode
(`ROAD`, `RAIL`, `WATER`, or `COMBINATION`), line-haul cost, transload cost, and
`path_confidence` (`direct_route`, `graph_shortest_path`, or `missing`). Road
and rail costs use the BC transporters with archive fuel/operating rates and
`transporter_materials` capacities; transload costs are applied at each TL node
from `transload_costs.csv`. `$/t` transload costs use the feedstock grade's
declared density, not a global tonne assumption.

A facility with no TL path is marked `missing` and excluded; it is never given
a fabricated distance or cost. Route capacity is absent from the parsed route
table, so every link records `capacity=None` and `capacity_source=absent`.

### BC coordinate recovery and diagnostic contracts

`bc_fibre.py` builds `facilities_bc_recovered.csv` from the 2015 CFS Mills DB
and the FFCM raw site registry.  Exact facility codes are only a candidate:
the 2015 and 2018 code systems collide, so a code match must be corroborated
by normalized company, city, mill-name, or coordinate evidence.  Rejected
code collisions are written to `bc_facility_match_conflicts.csv`; unmatched
facilities keep the low-confidence TL hub proxy or `missing`.

The same module parses the BC Fibre Database sawmill allocation input and TSA
tenure apportionment workbooks into `contracts_bc_diagnostic.csv`:

- TSA apportionment rows carry real 2018 AAC volumes but no facility ID.
- Sawmill allocation rows carry dimensionless residue shares but no volume.
- No row has both a facility ID and a volume, so `fixtures.py` keeps
  `contract_source=diagnostic_absent` for every Stage A facility.

The evidence tier is `bc_fibre_db_2018_diagnostic`; these are
diagnostic/project-legacy values, not real supply agreements.

### Contracts

No populated contract table exists. Each facility receives a diagnostic
contract with `min=0`, `max=nameplate`, and `gate=0`. `Contract.diagnostic` is
true, `contract_source=diagnostic_absent` is written on every contract, and the
notes say to replace it with real terms. The test suite covers non-zero minima
and tighter maxima.

## Validation (`validate.py`)

The validator recomputes, rather than trusts:

- volume conservation: assigned + unassigned = supply for every offer;
- facility and grade capacity;
- grade eligibility and transport-link existence;
- contract minimum and maximum;
- unassigned reasons;
- moisture blend limits;
- unit consistency and documented capacity conversions;
- transport path confidence, capacity source, and distance basis;
- diagnostic contract source;
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
.venv/bin/python -m pytest -q tests/test_assignment.py workflows/national/assignments/tests
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
| `facilities_bc.csv` | one row per BC facility, coordinate source/confidence, nameplate, line list, transport status |
| `facility_capacity.csv` | fixture facility total table |
| `facility_offers.csv` | fixture facility-grade table |
| `transport_links.csv` | fixture transport table consumed by the LP |
| `transport_links_bc.csv` | auditable path table: nodes, legs, mode, distance, line-haul, transload, confidence, capacity source |
| `contracts.csv` | fixture contract table |
| `assumptions.csv` | all diagnostic assumptions |
| `line_screen.csv` | accepted/rejected processing lines |
| `transport_screen.csv` | accepted/missing facility paths |
| `previous_pilot_class_mix.csv` | captured prior-run class shares for comparison |
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

- No TSA24 FMU/supply node exists in the FFCM graph; the Prince George mill
  proxy is the documented anchor and the main transport uncertainty.
- Only 8 of 179 BC facilities have direct Mills DB/CFS coordinates; 113
  in-scope facilities use a low-confidence TL hub proxy and 41 have no
  coordinate. Euclidean distance is not used.
- No route or transload capacity in the parsed route table; links are uncapped.
- No populated contract table; contracts are `diagnostic_absent`.
- Mixed capacity units: `bdmt/y` converts at the declared 0.45 density,
  `admt/y` needs a declared moisture, `mbf/y` needs a declared recovery factor,
  and energy units never convert to volume.
- No hog-fuel supply in the TSA24 matrix; the grade is reserved.
- No mill-residue (sawdust/woodchips) supply; only logging residue.
- One period, no inventory dynamics, no demand/market clearing.
- No MKRF input or output.
