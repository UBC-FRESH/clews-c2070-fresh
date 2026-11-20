# Forest cost/revenue ledger — BC/QC and the Forest-Sector LP

| Field | Value |
|---|---|
| Document | `planning/forest-cost-revenue-ledger.md` |
| Date | 2026-11-20 |
| Status | Design draft. Documentation only; no model, data, UI, or interface change is implemented by this note. |
| Scope | A jurisdiction-resolved cost/revenue ledger for BC and QC, and its use in the Forest-Sector LP v1/v2 |
| Audience | FRESH team; Taco Niet and the energy/OSeCAN policy team |
| Companion documents | `planning/forest-sector-lp-v1.md`; `planning/harvest-aac-feasibility.md`; `planning/module-boundaries.md`; `planning/bc-woody-residue-supply-curve.md` |

**Abbreviations.** BCTS = BC Timber Sales; BMMB = Bureau de mise en marché des
bois (QC); MERIS = the QC ministry forest register named in the running source
review, with field semantics still pending; AAC = allowable annual cut; NFD =
National Forestry Database wood supply; THLB = timber harvesting landbase;
ODT = oven-dry tonne; PCS = pre-Crown surplus; NV = forest-sector net value;
CR = Crown revenue.

**Decision summary.** The ledger has two non-additive perspectives:
**Crown revenue** and **forest-sector net value**. The recommended primary
perspective is **forest-sector net value** (the reporting frame and the v2
objective candidate; the v1 objective remains least deviation); Crown revenue is
the observed anchor and a companion report, and the two reconcile
through a single identity (`pre-Crown surplus = forest-sector net value + Crown
revenue`). Every row carries exactly one perspective, one component, one sign,
one price point, and one value basis. BC/QC ingestion treats BCTS/BMMB auction
results as **stumpage/bid values, not delivered log prices**, and treats
appraisal-manual and tarification cost components as **allowances, not audited
actuals**. The v1 LP objective remains **least deviation**; BC/QC net value is a
post-solve report and, at most, a bounded within-BC/QC sensitivity. It does not
enter the national objective while other provinces have no comparable value
basis. Missing is missing, not zero.

## 1. Perspectives

### 1.1 Why the ledger needs two perspectives

A dollar of stumpage is revenue to the Crown and a cost to the forest sector.
Booked once without a perspective, it either disappears or is counted twice.
The ledger therefore stores the same underlying transaction twice only when it
is genuinely a transfer, with opposite signs and an explicit `mirror=1` marker.
The two perspectives answer different questions and must never be summed into
one "total revenue".

| Perspective | Question it answers | Not a measure of |
|---|---|---|
| `crown` | What did the Crown collect from the right to harvest? | Forest-sector viability, social value, sustainability |
| `forest_sector` | What residual value does the harvesting/processing sector retain after all production and Crown costs? | GDP, profit after tax, social welfare, processing margin |

A third perspective — **society**, which would add Crown charges back and add
externalities — is out of scope. It must not be approximated by adding the two
perspectives together.

### 1.2 Crown revenue

**Definition.** Cash and near-cash received by the provincial Crown for the
right to harvest Crown timber in the reporting year:

- stumpage: the upset/administered base plus any bonus or winning-bid premium;
- annual rent and licence fees;
- market, administration, or waste/brush-disposal charges;
- less credits, refunds, and rebates of those charges.

**Boundary.** Crown revenue is a **fiscal transfer**. It excludes:

- corporate income tax, carbon tax, property tax, and other taxes;
- fines and penalties;
- payments to third parties, including contractors and municipalities;
- revenue from processing, energy, carbon offsets, recreation, or other
  non-timber products;
- the forest sector's own production costs.

**Use.** Crown revenue is the more directly observed layer where auction or
billing records exist, and it is the anchor for the ledger and a distributional
report. It is **not** the LP's objective: maximizing Crown revenue rewards rent
extraction and high-grade/low-cost stands, not resource productivity or residue
supply.

### 1.3 Forest-sector net value

**Definition.** The residual value retained by the harvesting and log-supply
sector, up to the mill gate, after all production costs and Crown charges, at a
stated price point:

```text
forest_sector_net_value
  = delivered_log_revenue
  + residue_revenue
  - harvest
  - road
  - haul
  - silviculture
  - residue_recovery
  - overhead
  - stumpage_base
  - stumpage_bid_premium
  - admin_fee
  - crown_other
  + crown_credit

(where a source reports only a total, subtract `stumpage_total` in place of
`stumpage_base + stumpage_bid_premium`)
```

**Boundary.** The default boundary is the **mill gate**:

- Log revenue is the value of merchantable logs delivered to the mill.
- Haul from the roadside landing to the mill is inside the boundary.
- Processing (sawmilling, panel, pulp, pellet, energy) is outside.
- **Mill residue is outside**: once logs cross the mill gate, bark, sawdust,
  shavings, and chips belong to the processing sector. Counting mill-residue
  revenue here would double count the log input.
- Harvest residue (slash, tops, limbs) sold at roadside or facility gate is
  inside only when it is a separate sale and its recovery cost is also booked.

**What it is not.** It is a **resource-rent-like residual**, not GDP and not
value added: labour and capital payments sit inside the cost components. It is
not profit after tax, not a social surplus, and not a sustainable-supply test.
A negative value is a valid result: it says the allocated harvest does not cover
its full cost at the stated basis, and it must be reported, not clipped to zero.

### 1.4 The reconciliation identity

The two perspectives are linked by one identity. Define **pre-Crown surplus**
(PCS) as the surplus before any Crown charge:

```text
PCS = delivered_log_revenue
    + residue_revenue
    - harvest - road - haul - silviculture - residue_recovery - overhead

Crown revenue (CR) =
      stumpage_base + stumpage_bid_premium
    + admin_fee + crown_other - crown_credit
    (or stumpage_total in place of base + premium)

Forest-sector net value (NV) = PCS - CR

Therefore:  PCS = NV + CR
```

This identity is the strongest no-double-counting check in the ledger. It holds
only when both perspectives use the same year, volume, price point, component
set, and currency basis. If it does not hold within tolerance, a component is
missing or double counted; the ledger must fail, not adjust a residual.

### 1.5 Decision: primary perspective for the LP

**Primary: forest-sector net value.** The LP allocates harvest; the question
"which allocation produces more value net of cost" is a forest-sector question,
not a treasury question. Net value is also the only perspective that can host
the residue-recovery cost and the log-revenue side of the eventual v2/v3
net-value objective. "Primary perspective" means the ledger's decision frame and
the v2 objective candidate; it does not change the v1 objective, which remains
least deviation (Section 5).

**Companion: Crown revenue.** Report it alongside, by jurisdiction and tenure,
because it is the observed anchor and because the distribution between Crown
and sector is a policy-relevant result.

**Reconciliation headline: pre-Crown surplus.** Report it as the sum of the two,
never as a third independent estimate. It is the distribution-neutral total.

**Data reality.** Crown revenue is better observed than net value. The ledger
therefore reports Crown revenue wherever the source supports it, and reports
forest-sector net value only where every component has at least `medium`
confidence. Where a component is missing or `low`, the net value is
`not_estimated` or a range, not a point estimate and not zero.

**Coverage note.** The task brief describes BC/QC as about 65% of national
harvest. The repo's 2024 template gives BC 38.88 + QC 25.94 = 64.82 Mm3 of
118.98 Mm3, or **54.5%**, not 65%. The 65% figure may use a different
denominator (a 100 Mm3 scenario, Crown-only volume, or another year). The ledger
must record `coverage_denominator` and `coverage_share` with every coverage
claim; do not repeat 65% without its base.

### 1.6 No-mixing rules

1. A total contains rows from exactly one perspective.
2. The only permitted cross-perspective operation is `PCS = NV + CR`.
3. Never add Crown revenue to delivered log revenue.
4. Never use stumpage as a revenue in the forest-sector perspective or as a
   log price in either perspective.
5. Never book the same sale as both `stumpage` and `delivered_log_revenue`
   unless the sale terms genuinely contain both a timber charge and a delivered
   log price; if they do, split with a documented formula and flag it.
6. A mirrored row must have the same absolute value, the opposite sign, the same
   licence, and `mirror=1` in `notes`. A missing or duplicated mirror is an
   error, not a rounding difference.
7. Do not net a Crown credit against a sector cost outside the identity. Use the
   `crown_credit` component with its own sign.

## 2. Component definitions and signs

### 2.1 Price points and boundary

Every value row declares a price point through `value_basis` (Section 3.3).
The revenue and cost rows in a net-value calculation must use compatible price
points:

| Revenue basis | Haul treatment | Rule |
|---|---|---|
| `delivered_log_revenue` at `mill_gate` | Include `haul` to mill | Default boundary; do not add a second mill-delivery charge |
| `delivered_log_revenue` at `roadside` | Exclude `haul` | Haul is outside the boundary; a haul row makes the pair invalid |
| `stumpage_*` at `standing` | No revenue row | A standing bid is a right-to-harvest value, never a log price |
| `residue_revenue` at `facility_gate` | Include residue transport inside `residue_recovery` | Do not book a separate residue haul |
| `residue_revenue` at `roadside` | Exclude facility transport | Facility transport is outside or must be a separate row |

The boundary is annual and undiscounted. Discounting, inflation adjustment, and
exchange-rate conversion are analysis-layer operations, not ledger operations.
Road construction may be annualized over the volume it serves; if so, the
amortization basis must be in `notes`. Do not mix cash and accounting
depreciation in one component.

### 2.2 Component table

Signs are relative to the row's own perspective: `+` is an inflow to that
perspective, `-` is an outflow. `—` means the component is outside the
perspective's boundary and must not have a row.

| Component | Definition and boundary | Unit | Crown | Forest sector | Typical source |
|---|---|---|---|---|---|
| `delivered_log_revenue` | Merchantable log revenue at the declared price point; excludes residue, processing margin, and subsidies | CAD/m3 log | — | + | Log price deck, mill settlement, delivered sale |
| `residue_revenue` | Harvest-residue revenue at the facility or roadside gate; excludes mill residue and energy revenue | CAD/ODT | — | + | Residue contract, bioenergy facility |
| `stumpage_base` | Legal minimum, upset, or administered Crown charge per m3 | CAD/m3 log | + | − | BCTS upset, QC tarification |
| `stumpage_bid_premium` | Winning bid above the base; a rent transfer, not a production cost | CAD/m3 log | + | − | BCTS/BMMB auction |
| `stumpage_total` | Source-reported total Crown stumpage where base and premium cannot be split; mutually exclusive with the two rows above for the same licence/year | CAD/m3 log | + | − | Billing system, aggregate report |
| `harvest` | Falling, bucking, skidding/yarding, loading, and stump-side supervision; excludes road, haul, and silviculture | CAD/m3 log | — | − | Appraisal allowance, actual cost |
| `road` | Forest-road construction (amortized over volume served), maintenance, and deactivation; excludes public roads and haul | CAD/m3 log | — | − | Appraisal allowance, road contract |
| `haul` | Transport from roadside to the declared delivery point; excludes residue haul and road construction | CAD/m3 log | — | − | Appraisal allowance, trucking contract |
| `silviculture` | Site preparation, planting, brushing, surveys, and the regeneration obligation; excludes the silviculture levy and carbon plantings | CAD/m3 log or CAD/ha | — | − | Appraisal allowance, actual spend |
| `residue_recovery` | Collection, comminution, drying, bundling, loading, and residue transport to the facility; excludes energy conversion | CAD/ODT | — | − | Residue ledger, BC Stage B |
| `overhead` | Supervision, insurance, compliance, and licence administration not elsewhere classified; excludes Crown fees | CAD/m3 log | — | − | Actual or allocated |
| `admin_fee` | Annual rent, licence fee, market/admin fee paid to the Crown; excludes stumpage and any levy already in `silviculture` | CAD/m3 log | + | − | Tenure invoice |
| `crown_other` | Other routine Crown charges (waste/brush disposal, etc.); excludes fines and penalties | CAD/m3 log | + | − | Invoice |
| `crown_credit` | Refund or credit of a Crown charge; store a positive amount and the opposite sign | CAD/m3 log | − | + | Invoice or credit note |

Annual rent, licence fees, and other lump-sum charges are converted to
`CAD/m3 log` using the licence's documented annual volume before ingestion. If
no annual volume exists, the charge is a coverage gap, not a one-m3 row.

### 2.3 Sign matrix

| Component | `crown` | `forest_sector` |
|---|---:|---:|
| `delivered_log_revenue` | — | +1 |
| `residue_revenue` | — | +1 |
| `stumpage_base` | +1 | −1 |
| `stumpage_bid_premium` | +1 | −1 |
| `stumpage_total` | +1 | −1 |
| `harvest` | — | −1 |
| `road` | — | −1 |
| `haul` | — | −1 |
| `silviculture` | — | −1 |
| `residue_recovery` | — | −1 |
| `overhead` | — | −1 |
| `admin_fee` | +1 | −1 |
| `crown_other` | +1 | −1 |
| `crown_credit` | −1 | +1 |

### 2.4 Missing is not zero

A component with no usable value gets **no row**. Coverage is reported in a
separate matrix (`covered`, `partial`, `missing`) by jurisdiction, year, and
component. **Complete coverage** means every in-boundary component has a row at
`medium` or better; **partial coverage** means at least one is missing or `low`.
The ledger does not insert a zero, a provincial average, or a "reasonable"
placeholder. A net-value calculation that encounters a missing component returns
`not_estimated` and the missing-component list. This is the same fail-closed
rule as `planning/forest-sector-lp-v1.md` §5.2.

## 3. Schema

### 3.1 File, grain, and key

The canonical file is `forest_cost_revenue.csv`. One row is one component value
for:

```text
jurisdiction × source × year × spatial_unit × species × product
× perspective × component × licence
```

That tuple is the primary key. Duplicate keys are an error. If a source reports
an aggregate, ingest it as a single row with `licence=none` and a
`notes=aggregated_from=N` marker; do not leave the parts and the aggregate in
the same table.

The ledger is a **rate table**, not a transaction-total table. `value_cad` is
CAD per unit of `unit`; a row total is `value_cad × volume_m3`. This is
deliberate: the LP allocates m3, and rate rows are additive after volume
weighting. A source that reports only a total is converted at ingest to
`total / volume`; `notes` records `conversion=<total> -> CAD_per_m3_log` and the
original total, and `value_basis` retains the evidence type.

### 3.2 Fields

| Field | Type | Required | Rule |
|---|---|---|---|
| `jurisdiction` | text | yes | `BC`, `QC`, `AB`, `SK`, `MB`, `ON`, `NB`, `NS`, `NL`, `PE`, `YT`, `NT`, `NU`, or `CAN`. Use the `province` code in `provinces.csv`; no full names. |
| `source` | text | yes | Lowercase snake-case id registered in `metadata/source-inventory.csv`, or `prov_<id>` while a source review is open. No URLs; URLs live in the source register. A provisional source has `confidence <= medium`. |
| `year` | integer | yes | Four-digit price year of the value. If observation year and price year differ, `year` is the price year and `notes` carries `observed_year=...`. No ranges. |
| `spatial_unit` | text | yes | Stable unit id: `BC-TSA24`, `BC-TFL_XX`, `BC-BCTS_<sale>`, `QC-UA_XXXX`, `QC-REGION_XX`, `CAN`. Must resolve through a crosswalk to province and, where available, operable stratum. No coordinates and no free text. |
| `species` | text | yes | Canonical species code (BC/QC prefixes preserved) or `all`. `spp` is allowed only for unresolved mixed species with `confidence=low`. No blanks. |
| `product` | enum | yes | `sawlog`, `peeler`, `pulpwood`, `mixed_log`, `harvest_residue`, `mill_residue`, `hog_fuel`, `biomass`, `all`. Log and residue products must be explicit. `mill_residue` is allowed only in a separate processing ledger; it must not appear in a forest-sector net-value total. |
| `volume_m3` | numeric | yes | Quantity weight in the unit declared by `unit`, `> 0`. For `CAD_per_m3_log` it is merchantable m3; for `CAD_per_odt_residue` it is ODT; for `CAD_per_ha` it is ha; for `CAD_per_gj` it is GJ. The column name is fixed by the brief; `unit` is authoritative. If the schema can still change, rename this field to `quantity`. |
| `unit` | enum | yes | `CAD_per_m3_log`, `CAD_per_odt_residue`, `CAD_per_ha`, `CAD_per_gj`. No totals, no `CAD_per_year`, no `CAD_per_km`. Annual and lump-sum values must be converted at ingest to one of these units with a documented volume; if no volume exists, the row is a coverage gap. |
| `value_cad` | numeric | yes | CAD per unit of `unit`, always `>= 0`. `crown_credit` is also stored as a positive amount; its direction comes from `sign` (−1 under Crown, +1 under the forest sector). Do not store negative costs. |
| `value_basis` | text | yes | `<evidence>:<price_point>` from the controlled vocabulary in §3.3. Both parts are required. |
| `perspective` | enum | yes | `crown` or `forest_sector`. No `total`, no `both`. |
| `component` | enum | yes | One of the 14 components in §2.2. |
| `sign` | integer | yes | `1` or `-1`, relative to the row's perspective. `0` is invalid. |
| `confidence` | enum | yes | `high`, `medium`, or `low`. `none` is invalid; a non-usable value gets no row. Definitions in §3.3. |
| `licence` | text | yes | Tenure, licence, sale, lot, or permit id (`BCTS-...`, `TFL...`, `TSA...`, `CAAF...`, `UA...`, `LOT...`); `none` only for an aggregate row. Used to match mirror rows and to weight by tenure. |
| `notes` | text | yes | Free text with the structured tokens in §3.3. Required for every row; must name the source table or page and any conversion. |

### 3.3 Controlled vocabularies

**`value_basis` grammar.**

```text
value_basis = <evidence>:<price_point>

evidence =
    observed_payment        actual payment or invoice
  | observed_sale           auction or sale result
  | observed_price          transaction or settlement price
  | administered_rate       published regulation or tariff
  | appraisal_allowance     appraisal-manual cost allowance
  | published_average       published average, not a transaction
  | model_derived           converted or allocated from another basis
  | model_input             model parameter or input deck, not an observation
  | fallback                documented default for a province with no source

price_point =
    standing | roadside | mill_gate | facility_gate | na
```

Examples: `observed_sale:standing` for a BCTS winning bid;
`administered_rate:standing` for QC tarification;
`appraisal_allowance:roadside` for a BC harvest allowance;
`observed_price:mill_gate` for a delivered log settlement;
`model_derived:facility_gate` for a residue cost;
`model_input:mill_gate` for a MERIS species/product or species/DBH revenue
deck (a model parameter, not a transaction);
`fallback:na` for a documented non-BC/QC default.

`model_input` rows must carry `not_observed=1` and `model_input=1` in
`notes`, and cannot use `confidence=high`. The validator enforces this so a
model parameter cannot be mistaken for an observed payment or sale.

**`confidence`.**

| Value | Meaning |
|---|---|
| `high` | Observed transaction, payment, or audited actual; complete volume and product; price point known. |
| `medium` | Published average, administered rate, or appraisal allowance with a documented method; or an observed value with partial coverage; price point known. |
| `low` | Proxy, expert judgment, unresolved species/grade crosswalk, extrapolation from a sample, or an unresolved price point. |
| `none` | Not usable. Never appears in a row; use a coverage gap. |

**`notes` tokens.** Separate tokens with `; `. Required where applicable:

```text
currency_basis=CAD_nominal | CAD_real_<year>
observed_year=<yyyy>                     if different from year
price_point=<...>                        if not already unambiguous
volume_basis=m3_solid | m3_apparent      QC, and any stacked-volume source
conversion=<factor> <from> -> <to>; conversion_source=<id>
amortization=<volume_served|annual|combined>
mirror=1                                 mirrored Crown/sector row
stumpage_residual=true                   appraisal components and stumpage from the same formula
coverage=<sample|partial|full>; coverage_denominator=<...>
comparison_only=1; not_additive_with=<source>   alternative source, not summed
aggregated_from=<n>                      aggregate row
```

**Year and currency.** `value_cad` is CAD. `year` is the price year. Constant-
dollar rows must state `currency_basis=CAD_real_<year>` and must not be summed
with nominal rows until a derived view deflates them to a common year. Do not
convert USD or an undocumented legacy currency silently; convert explicitly and
record the rate and source, or exclude the row.

### 3.4 Derived views

The CSV is the audit layer; the following views are computed, never stored as
extra rows:

1. **Totals.** `sum(value_cad * volume_m3 * sign)` by perspective, component,
   jurisdiction, year. Never sum across perspectives.
2. **Per-m3 values.** Convert non-m3 rows first:
   - `CAD_per_odt_residue` → `CAD_per_m3_log` via `value_cad * odt_per_m3`;
   - `CAD_per_ha` → `CAD_per_m3_log` via `value_cad / yield_m3_per_ha`;
   - `CAD_per_gj` → `CAD_per_m3_log` via `value_cad * gj_per_m3`.
   Every factor comes from the residue ledger, the yield layer, or the energy
   conversion (`18.5 GJ/ODT`), with a source id. A missing factor is an error,
   not a zero.
3. **Reconciliation.** `PCS = NV + CR`; residual by jurisdiction/year.
4. **Coverage.** Covered volume and value share by component; sample share for
   BCTS/BMMB; tenure and species coverage.

### 3.5 Illustrative rows

The values below are illustrative only; they are not data. They show the grain,
the base/premium split, the mirror convention, the price-point distinction, and
the residue unit. The source ids are canonical for readability; until a source
is registered, actual rows use `prov_<id>` and confidence is capped at `medium`
even where the illustration shows `high`.

```csv
jurisdiction,source,year,spatial_unit,species,product,volume_m3,unit,value_cad,value_basis,perspective,component,sign,confidence,licence,notes
BC,bcts_auction_results,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,30.00,observed_sale:standing,crown,stumpage_base,1,high,BCTS-24001,"currency_basis=CAD_nominal; base=upset"
BC,bcts_auction_results,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,12.50,observed_sale:standing,crown,stumpage_bid_premium,1,high,BCTS-24001,"currency_basis=CAD_nominal; premium=winning minus upset"
BC,bcts_auction_results,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,30.00,observed_sale:standing,forest_sector,stumpage_base,-1,high,BCTS-24001,"mirror=1"
BC,bcts_auction_results,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,12.50,observed_sale:standing,forest_sector,stumpage_bid_premium,-1,high,BCTS-24001,"mirror=1"
BC,bc_appraisal_manual,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,38.10,appraisal_allowance:roadside,forest_sector,harvest,-1,medium,BCTS-24001,"currency_basis=CAD_nominal; allowance not actual; stumpage_residual=true"
BC,bc_log_price_deck,2024,BC-TSA24,SWE,sawlog,12500,CAD_per_m3_log,98.40,published_average:mill_gate,forest_sector,delivered_log_revenue,1,medium,BCTS-24001,"currency_basis=CAD_nominal; deck average; not a transaction"
QC,qc_tarification,2024,QC-UA-0642,SAB,sawlog,8000,CAD_per_m3_log,18.75,administered_rate:standing,crown,stumpage_base,1,high,UA0642-2024,"currency_basis=CAD_nominal; regional rate"
QC,qc_tarification,2024,QC-UA-0642,SAB,sawlog,8000,CAD_per_m3_log,18.75,administered_rate:standing,forest_sector,stumpage_base,-1,high,UA0642-2024,"mirror=1"
QC,qc_bmmb_sales,2024,QC-UA-0642,SAB,sawlog,8000,CAD_per_m3_log,72.00,observed_sale:mill_gate,forest_sector,delivered_log_revenue,1,medium,LOT-2024-119,"currency_basis=CAD_nominal; delivered sale; confirm haul boundary"
BC,bc_stage_b,2020,BC-B2,all,harvest_residue,1500,CAD_per_odt_residue,31.57,model_derived:facility_gate,forest_sector,residue_recovery,-1,medium,BC-B2,"currency_basis=CAD_real_2020; transport+processing only"
```

The QC delivered-sale row must **not** be accompanied by a QC tarification row
for the same lot unless the sale terms include a separate Crown charge. The BC
appraisal row is an allowance, not an actual; `stumpage_residual=true` says it
is also an input to the same stumpage formula.

The same `volume_m3` appears on every component row for a sale. It is a
**weight**, not a transaction count: do not sum volume across components of the
same sale. Sum volume only across distinct sales or units, and only when the
`licence` differs or the row is a documented aggregate.

## 4. BC/QC ingestion plan

### 4.1 Source map

| Source id | System | What it provides | Ledger components | `value_basis` | Confidence | Coverage | Main trap |
|---|---|---|---|---|---|---|---|
| `bcts_auction_results` | BC Timber Sales public sale results | Sale id, species/grade, volume, upset, winning bid, bonus, date, location | `stumpage_base`, `stumpage_bid_premium` | `observed_sale:standing` | High for the bid; medium for representativeness | BCTS sales only | Bid is not a log price; bonus is not a cost; BCTS is a minority tenure |
| `bc_appraisal_manual` | BC Timber Pricing Branch appraisal manual | Activity cost allowances, average market value, return-to-forest, terrain/zone factors | `harvest`, `road`, `haul`, `silviculture`; possibly `delivered_log_revenue` via average market value | `appraisal_allowance:roadside` or `published_average:mill_gate` | Medium/low | All appraised BC Crown volume | Allowance is not actual; average-market-value price point must be established; the formula is residual |
| `bc_hbs` | BC harvest/stumpage billing (if the source review confirms access) | Actual stumpage billed by scale/grade | `stumpage_base`, `stumpage_bid_premium`, or `stumpage_total` | `observed_payment:standing` | High | Billed Crown volume | Billing year versus harvest year; does not include non-stumpage charges |
| `bc_log_price_deck` | FEMIC VLM or ministry average (if accessible) | Log prices by species/grade/region | `delivered_log_revenue` | `published_average:mill_gate` or `observed_price:mill_gate` | Medium | Partial | Not a transaction; price point must be known |
| `qc_meris` | QC BMMB/MFFP MERIS 3.3.0 analysis and simulation tool (public ACCDB; field semantics confirmed 2026-11-20) | 191 tarification zones; administered stumpage (`tbl_redevances_F1234`); supply-cost parameters (`tbl_couts_zones`); treatment rates; species/product and species/DBH model-input revenue decks; demo scenarios. **No observed permit volume or observed delivered log price.** | `stumpage_base`; `delivered_log_revenue` as `model_input` only; `road`, `haul`, `overhead`, `silviculture` | `administered_rate:standing`; `model_input:*`; `published_average:*`; `model_derived:*` | Medium/low | Tarification zones, not UAs; no volume coverage | MERIS is not a register of actual harvest. Economic matrix is $2023; current rates are 2025. The model-input revenue deck is not a transaction price. See `data/derived/national/harvest_basis/meris_qc_full/` and `planning/meris-qc-extraction.md`. |
| `qc_bmmb_sales` | BMMB auction results | Lot, species/quality, volume, price, sale terms | `stumpage_base`, `stumpage_bid_premium`, or `delivered_log_revenue` | `observed_sale:standing` or `observed_sale:mill_gate` | Medium | Auctioned volume | Standing versus delivered; premium versus total; quality codes |
| `qc_tarification` | QC administered pricing | Rates by species/quality/region; formula components | `stumpage_base`; `appraisal_allowance` for cost components | `administered_rate:standing` or `appraisal_allowance:standing` | High for the rate; medium for components | All Crown volume | Rate is not a market price; formula components are not actuals |
| `bc_stage_b` | BC Stage B residue transport and processing | Residue transport and processing cost | `residue_recovery` | `model_derived:facility_gate` | Medium | BC | Transport plus processing only; excludes collection, grinding, stumpage, opportunity cost |
| `legacy_ffcm` | Legacy FFCM FMU supply cost | Flat log and residue cost | `harvest` or `residue_recovery` fallback | `fallback:na` | Low | Nine provinces | Composition undocumented; not stumpage; no annual volume |
| `nrcan_fsd` | National harvest statistics | Harvest volume by province/year | Volume reconciliation only | — | — | National | Not a value source; do not put it in the value ledger |

The ids in this table are the proposed canonical ids. Until the source review
closes and the id is registered in `metadata/source-inventory.csv`, ingest under
`prov_<id>` and cap confidence at `medium`. The table shows the canonical id for
readability.

A source that is not registered in `metadata/source-inventory.csv` is ingested
under `prov_<id>` and cannot exceed `medium` confidence. Volume-only sources
(`nrcan_fsd`, harvest declarations) do not enter the value table; they are used
in the coverage and volume-reconciliation report.

### 4.2 BC ingestion rules

1. **Classify the sale terms before reading the price.** BCTS sells standing
   timber with harvesting obligations. The upset and winning bid are
   `stumpage_base` and `stumpage_bid_premium`, with
   `value_basis=observed_sale:standing`; they are not `delivered_log_revenue`.
   A BCTS "log sale" or delivered component is split only if the sale document
   states it.
2. **Split base and premium.** Upset = base; winning bid minus upset = premium.
   If the source reports the bonus bid directly, use it as the premium and do
   not subtract again. If the upset or bonus is a lump sum, convert it to a
   per-m3 rate using the sale volume and record the conversion. Both are Crown
   revenue and both are costs to the sector. If only the total is available, use
   `stumpage_total` and do not also store base/premium for that licence/year.
3. **Weight by volume.** A provincial or zonal value is
   `sum(rate * volume) / sum(volume)`, not a mean of sale rates. Do not let a
   small sale with an extreme bid dominate. If only total volume is reported,
   use `species=all` and `confidence=low`; do not invent a species split.
4. **Appraisal allowances.** Extract the allowance by activity, edition, zone,
   terrain, and haul distance. Store it with
   `value_basis=appraisal_allowance:<price_point>`, not as actual. The manual
   may show the allowance as a deduction in the stumpage formula; store the
   absolute amount as a positive cost with `sign=-1`, never as a negative
   `value_cad`. Record the manual edition and the appraisal inputs in `notes`.
   Do not replace a sale-specific allowance with a provincial average.
5. **Average market value.** Establish its price point from the manual before
   using it as revenue. If it is a mill-gate value, pair it with `haul`; if it
   is roadside, exclude `haul`. Label it `published_average`, never
   `observed_price`, and never mix it with a transaction price in one average.
6. **Road versus haul.** Appraisal road cost covers construction and
   maintenance; haul covers transport. If the manual reports a combined
   "road and haul" allowance, split only with a documented method; otherwise
   store `road` with `notes=combined_road_haul` and exclude it from net value
   until split.
7. **Silviculture versus levy.** The regeneration obligation is `silviculture`.
   A statutory silviculture levy or fee is `admin_fee` (a Crown charge). Do not
   count both for the same obligation. If the licensee performs the work, it is
   `silviculture`; if the Crown performs it and charges the licensee, it is
   `admin_fee`.
8. **Currency and year.** Use the manual edition's price year. If the manual is
   in constant dollars, record `currency_basis=CAD_real_<year>` and do not mix
   it with nominal sale-year rows until a derived view deflates.
9. **Tenure coverage.** BCTS is not all BC. Report `bcts_volume_share` and do not
   scale a BCTS average to the provincial harvest. For non-BCTS volume, use the
   appraisal manual (an allowance) or report `partial`; do not assume the BCTS
   bid applies.
10. **No extrapolation.** Do not apply BC values to QC or to other provinces.

### 4.3 QC ingestion rules

1. **Classify the sale terms before reading the price.** A `bois sur pied`
   (standing) sale is `stumpage_base` plus any premium; it is not a delivered
   log price. A `bois façonné` or delivered sale is `delivered_log_revenue`; it
   is not stumpage. If the sale bundles timber and harvesting services, split
   only with a documented formula; otherwise label the row
   `notes=bundled_harvest_service` and do not book a separate harvest cost.
2. **Tarification versus BMMB.** Tarification is the administered base; BMMB is
   a market sale. Store the base and the premium separately. If BMMB reports a
   total sale value, convert it to a per-m3 rate using the lot volume. Do not add
   the tarification rate to the BMMB winning price unless the BMMB record
   explicitly reports a premium above the base.
3. **Quality and grade.** Map QC quality classes to the product taxonomy through
   a versioned crosswalk. Do not collapse grade into species; grade drives
   value. If the class is unresolved, use `confidence=low` and say so.
4. **Region.** Use the administrative region or UA; tarification rates vary by
   region. Do not use a province-wide average when a regional rate exists.
5. **Volume basis.** Record `m3_solid` or `m3_apparent` in `notes`. Convert
   apparent to solid with a documented factor by product and region; never mix
   the two in a total. This is a common source of false volume reconciliation.
6. **Tarification formula components.** The administered formula may contain
   harvest, transport, silviculture, administration, and a margin. Extract them
   as `appraisal_allowance`, not as actual costs, store positive amounts with
   the correct sign, and record `stumpage_residual=true` when they are also
   inputs to the stumpage rate.
7. **Crown versus private.** Tarification and BMMB cover Crown wood. Private
   wood is out unless a separate source exists. Do not apply Crown rates to
   private volume, and do not report a QC provincial average that silently
   includes private wood.
8. **Coverage.** BMMB is auctioned volume only. MERIS may cover all Crown
   permits but its value fields may be incomplete. Report
   `covered_volume_share` and `sample_share` separately.
9. **Currency and year.** Record the tarification year and the sale year
   separately if they differ. Do not mix nominal and constant rows without a
   deflator.
10. **No extrapolation.** Do not apply QC values to BC or to other provinces.

### 4.4 Cross-jurisdiction normalization

| Dimension | Rule |
|---|---|
| Species | Canonical species codes, BC/QC prefixes preserved. A crosswalk is required before a BC and QC species are compared. Unresolved mixed species use `spp` with `confidence=low`. |
| Product and grade | One product taxonomy. A versioned BC↔QC grade crosswalk is required; grade classes are not assumed equivalent. |
| Volume | Merchantable m3 solid for logs; ODT for residue; ha for area. QC apparent volume is converted with a documented factor. BC scale deductions and QC volume bases must travel with the row. |
| Price point | Standing, roadside, mill gate, or facility gate. Revenue and haul must be compatible. A standing value is never a log price. |
| Currency | CAD. Price year explicit. Nominal and constant rows are not summed without deflation. |
| Tenure | Crown versus private, and auction versus all. BCTS/BMMB are samples, not provincial populations. |
| Weighting | Volume-weighted. No sale-count averages. |
| Imputation | No BC→QC or QC→BC imputation. No national average from two provinces. |

### 4.5 Pipeline and artifacts

1. **Register.** Close the source review, record the field dictionary, licence,
   access mode, and retrieval query in the source register.
2. **Land raw.** Put raw extracts in the `data` submodule under
   `data/raw/forest_cost_revenue/<source>/`; no large payloads in the parent
   repository.
3. **Stage.** Parse to a staging table with a source row id, source-native
   units, and the original field names. Do not normalize in the parser.
4. **Normalize.** Map to the ledger schema; convert units with a registered
   factor; write one row per component. Every conversion gets a `notes` token
   and a `conversions.csv` entry.
5. **Validate.** Run the checks in Section 6. Reject the batch on a schema,
   sign, key, unit, or identity failure; do not patch individual rows.
6. **Emit.** Write `forest_cost_revenue.csv`,
   `forest_cost_revenue_manifest.json`, `forest_cost_revenue_coverage.csv`,
   `forest_cost_revenue_sources.csv`, and
   `forest_cost_revenue_conversions.csv` under
   `data/derived/national/forest_cost_revenue/`.
7. **Join.** Match to the LP allocation by jurisdiction, year, and (where
   available) stratum. Report covered volume and coverage share; do not rescale
   to 100%.
8. **Review and label.** A second reviewer checks the sign matrix, the mirrors,
   and the identity. The artifact is `screening_only`, `policy_grade=false`,
   with the source review status and the missing-component list.

### 4.6 Anti-patterns

- A BCTS or BMMB bid used as a delivered log price.
- Stumpage booked as revenue in the forest-sector perspective.
- Crown revenue added to log revenue in one "total revenue".
- Upset treated as the winning bid, or the bonus treated as the only Crown
  revenue.
- Appraisal allowances treated as actual costs without a label.
- Tarification treated as a market price or as the full Crown revenue.
- A BCTS average applied to all BC harvest, or a BMMB average applied to all QC
  Crown volume.
- Mill-residue revenue counted inside a mill-gate forest-sector boundary.
- Nominal and constant dollars mixed in one total.
- QC `m3_apparent` and `m3_solid` mixed in one volume total.
- The silviculture levy counted as both `silviculture` and `admin_fee`.
- A full log harvest cost allocated to residue, or a residue cost added to the
  log cost and called the cost of harvest.
- A zero inserted for a missing province or component.

## 5. LP objective

### 5.1 v1: least deviation, unchanged

The v1 objective remains the existing lexicographic least-deviation rule in
`planning/forest-sector-lp-v1.md` §4.2:

```text
d_plus[p,t] - d_minus[p,t] = sum_s H[p,s,t] - target[p,t]
d_plus, d_minus >= 0

primary:    minimize sum_p (d_plus[p,t] + d_minus[p,t])
secondary:  after fixing the primary optimum within 1 m3, minimize the maximum
            proportional increase above the observed-share target
```

The ledger does **not** change this objective. Its v1 role is reporting:

- compute the implied Crown revenue, forest-sector net value, and pre-Crown
  surplus of the allocated harvest;
- flag any province/stratum with negative net value for review;
- report coverage and confidence, not a single national value.

The regression requirement is explicit: adding the ledger must not change the
v1 allocation by more than 1 m3. If it does, the objective has been contaminated
and the run is invalid.

### 5.2 Why BC/QC net value is not a partial objective

A net-value term for BC/QC combined with least deviation for the rest is not a
common metric. The solver would compare a measured dollar in BC/QC with an
unmeasured share target elsewhere, and the result would depend on coverage, not
on forest productivity. With BC/QC at roughly 55% of the 2024 harvest (the
brief's 65% is denominator-dependent), the model could move the remaining 45%
toward or away from the measured provinces for reasons that are purely an
artifact of which provinces have data.

There is a second problem even inside BC/QC: the value per m3 is a
**constant-mix average** over the observed product mix. Reallocating provincial
totals with a constant average assumes constant returns and an unchanged grade
mix; both are false at the margin. A net-value objective needs species/product
resolution and a cost-volume slope, which v1 does not have.

The LP design note already reaches the same conclusion: option B (maximize net
value) is deferred to v2/v3 because the national log price deck, stumpage,
harvest, road, and silviculture data do not exist as operational inputs
(`planning/forest-sector-lp-v1.md` §4.1, §4.3).

### 5.3 Bounded hybrid, sensitivity only

If the program requires a BC/QC economic signal before v2, use the following
**two-stage, within-coverage** rule. It never moves volume across the coverage
boundary.

```text
Stage 1: solve the v1 least-deviation LP. Record D* and H*.

Stage 2 (sensitivity):
    maximize  sum_{p in {BC,QC}} NV[p] * H[p]
    subject to:
        sum_p |H[p] - target[p]| <= D* + 1 m3
        H[p] = H*[p]                     for p not in {BC, QC}
        sum_s H[p,s] <= U[p]             for every p
        sum_{p,s} H[p,s] = T
        H[p] = sum_s H[p,s]
        H[p,s] >= 0
```

`NV[p]` is the volume-weighted net value per m3 from the ledger, computed only
if every component for that province is `medium` or better. Non-BC/QC provinces
are fixed at their Stage-1 values, so the only choice is **BC versus QC**. Because
`NV` is constant per m3, Stage 2 is a corner-solution diagnostic: it shows the
direction and upper bound of a measured value difference, not a marginal
response. Do not present it as an optimal provincial split. Report the swap, the
value change, and the Stage-1 result side by side. Label it
`partial_coverage_sensitivity`; it is not the v1 result and it is not a national
economic gain.

If the policy question requires moving volume between BC/QC and the rest, do not
use this hybrid. Wait for a coverage-symmetric value deck (§5.4), or run a
clearly labelled **fallback** scenario in which every non-BC/QC province gets a
documented flat value with `confidence=low`. A fallback is an assumption, not a
measurement; it must be reported as such and cannot be the primary case.

### 5.4 Gate to a net-value objective

Net value can replace or augment least deviation only when all of the following
hold:

1. Every province has a `delivered_log_revenue` row or a documented stumpage
   base plus a cost basis; a documented fallback is allowed only for a
   sensitivity, not for the primary.
2. The price point and haul boundary are consistent across provinces.
3. BC and QC have at least one year of actual cost data, not only appraisal
   allowances.
4. The ledger has species/product resolution, so `NV` is not a constant-mix
   average.
5. A second reviewer has signed off the value deck, the grade crosswalk, and the
   currency basis.
6. The LP has a documented cost-volume slope; a flat provincial average is not a
   marginal value.

Until then, the primary objective stays least deviation.

### 5.5 Constraints for any net-value use

- Provincial AAC/NFD/proxy ceilings remain hard; a value term cannot relax them.
- The national harvest total remains exogenous; net value does not choose
  `T[t]`.
- All cost rows are non-negative before the sign is applied; a negative value is
  allowed only for `crown_credit`.
- If residue revenue is included, residue recovery cost is included. Residue is
  not a free by-product with a negative cost.
- The product mix must be feasible under the grade/bucking rules; the model
  cannot select a grade the supply chain cannot produce.
- A province with `not_estimated` value cannot receive a zero or a national
  average. It is either excluded from the value term or given a documented
  fallback in a labelled sensitivity.
- The value term must not silently override the observed-share target. Report
  the deviation and the value change separately.
- The model must not use Crown revenue as a negative cost or as a subsidy to the
  sector. The perspectives are separate.
- Do not discount inside the LP. If a present-value objective is wanted, add a
  documented discount rate and a time path; neither exists in v1.

### 5.6 Reporting

Report by jurisdiction and year:

| Column | Content |
|---|---|
| Harvest | LP `H[p,t]`, m3 |
| Covered volume | Volume with a usable value row, m3 |
| Coverage share | `covered / harvest`; denominator stated |
| Delivered log revenue | CAD and CAD/m3, with price point and confidence |
| Production costs | Harvest, road, haul, silviculture, residue recovery, overhead; CAD and CAD/m3 |
| Crown charges | Stumpage base, premium (or total), admin, other, credits |
| Pre-Crown surplus | CAD and CAD/m3 |
| Crown revenue | CAD and CAD/m3 |
| Forest-sector net value | CAD and CAD/m3; `not_estimated` if coverage incomplete |
| Confidence | Minimum component confidence, and the missing-component list |
| Labels | `screening_only`, `policy_grade=false`, `partial_coverage=true` where applicable |

Report BC and QC as measured; report other provinces as `not_estimated`, never
as zero. Do not publish a national net-value total until coverage is complete;
if a partial total is shown for context, label it `partial_coverage` and show
the covered share. Report a range where a component is `low`, and do not report
more decimal places than the weakest source.

## 6. Validation

### 6.1 Checks

| ID | Check | Assertion | Fail behaviour |
|---|---|---|---|
| FS-CRL-01 | Schema parse | Every required field present; enums valid; `year` four digits; `volume_m3 > 0`; `value_cad >= 0` | Error with row key; no default row |
| FS-CRL-02 | Key uniqueness | The 9-field key is unique | Error; aggregate explicitly or drop the duplicate |
| FS-CRL-03 | Sign matrix | Every row matches §2.3 | Error; no "close enough" sign |
| FS-CRL-04 | Perspective/component compatibility | No production component under `crown`; no `crown_other` revenue under `forest_sector` as a positive | Error |
| FS-CRL-05 | Value non-negative | `value_cad >= 0`; `crown_credit` uses the sign field | Error |
| FS-CRL-06 | Unit/product compatibility | Log products use `CAD_per_m3_log`; residue products use `CAD_per_odt_residue`; area uses `CAD_per_ha` | Error; no implicit conversion |
| FS-CRL-07 | Total recomputation | Where the source reports a total, `value_cad * volume_m3` matches it within $1 or 0.5%, whichever is larger | Error; report both |
| FS-CRL-08 | Volume reconciliation | Log rows by jurisdiction/year match LP `H` within 1 m3 or 0.1%, or the difference is a documented coverage share | Error; do not rescale |
| FS-CRL-09 | Residue reconciliation | Residue ODT matches the covered harvest × `f_gross × rho` within 1 ODT or 0.1%, with the same factor version | Error; no silent factor change |
| FS-CRL-10 | Conversion presence | Every non-m3 row has a registered factor and source | Error; no zero-fill |
| FS-CRL-11 | No double counting | Base + premium not combined with `stumpage_total`; road and haul do not overlap; silviculture and the levy are not both booked; mill residue is not in the sector boundary; harvest residue and mill residue are not both counted for the same log volume | Error |
| FS-CRL-12 | Identity | Where coverage is complete, `PCS = NV + CR` within $1 or 0.1% of PCS, whichever is larger; where coverage is incomplete, the check is `not_applicable` and the report is `not_estimated` | Error on a complete-coverage residual; a residual is not a plug |
| FS-CRL-13 | Weighted-average bounds | A weighted value lies within `[min, max]` of its contributors | Error |
| FS-CRL-14 | Coverage flags | Every jurisdiction/component has `covered`, `partial`, or `missing`; sample and tenure shares are reported | Error if absent; net value is `not_estimated`, not zero |
| FS-CRL-15 | Currency consistency | Nominal and constant rows are not summed without a deflator; `currency_basis` present | Error |
| FS-CRL-16 | Source traceability | Every row resolves to a registered source and a source row/page; every conversion has a token | Error |
| FS-CRL-17 | LP non-interference | v1 allocation with the ledger equals the allocation without it within 1 m3 | Error; the objective is contaminated |
| FS-CRL-18 | Mirror rows | Each `mirror=1` row has an equal absolute value, the opposite sign, and the same licence/component/year | Error |
| FS-CRL-19 | No fake precision | Reported precision does not exceed the weakest source; no six-decimal rate from a rounded source | Warning at ingest; error at publication |
| FS-CRL-20 | Unknown enum | Any unknown source, species, product, unit, component, perspective, or value basis | Error; no fallback enum |
| FS-CRL-21 | Source exclusivity | For one `(jurisdiction, year, spatial_unit, species, product, licence, component, perspective)`, exactly one source is additive; alternatives are `comparison_only` and volume-partitioned, never summed | Error; no overlapping coverage |

### 6.2 Sign and double-count specifics

- `crown` rows may contain only `stumpage_base`, `stumpage_bid_premium`,
  `stumpage_total`, `admin_fee`, `crown_other`, and `crown_credit` (the last with
  sign −1).
- `forest_sector` rows may contain the production and revenue components, the
  stumpage components (sign −1), `admin_fee` and `crown_other` (sign −1), and
  `crown_credit` (sign +1).
- `stumpage_total` is mutually exclusive with `stumpage_base` and
  `stumpage_bid_premium` for the same
  `(jurisdiction, year, spatial_unit, species, product, licence)`.
- When `stumpage_residual=true`, the appraisal allowance and the stumpage are
  not independent observations. Do not present their sum as an independently
  measured cost, and do not use the allowance to "validate" the stumpage; the
  allowance is an input to the residual formula.
- A sale may appear as both a stumpage row and a delivered-revenue row only if
  the sale document contains both a timber charge and a delivered log price;
  the rows must share the licence and carry a `split=<method>` note.
- `road` and `haul` are separate: road construction/maintenance moves the log
  from stump to landing; haul moves it from landing to the delivery point. A
  combined source value is split or excluded, not double booked.
- `silviculture` and `admin_fee` must not both contain the same levy. The levy is
  a Crown charge; the regeneration work is a sector cost.
- Harvest residue and mill residue are different streams. Mill residue is out of
  the forest-sector boundary by default. If the LP residue supply includes mill
  residue, use a separate `processing_residue` ledger and do not add it to
  forest-sector net value.
- **Source exclusivity.** For one licence/component/year/perspective, only one
  source is additive. Use the hierarchy `observed_payment` > `observed_sale` >
  `administered_rate` > `appraisal_allowance` > `published_average` > `fallback`.
  A second source is a cross-check with
  `notes=comparison_only; not_additive_with=<source>`. If the two sources cover
  different volumes, partition by volume; never sum overlapping coverage. This
  is what prevents a BCTS winning bid and an HBS billing for the same sale, or a
  BMMB winning price and a tarification base for the same lot, from double
  counting.
- The reconciliation identity is the final check. A component that is present in
  both perspectives with the wrong sign will fail it.

### 6.3 Volume and unit reconciliation

- **Log volume.** The sum of `volume_m3` for log rows by jurisdiction/year must
  equal the LP's `H[p,t]`, or the difference must equal a documented uncovered
  volume. Do not scale the ledger to match; report the coverage share.
- **Residue volume.** Residue ODT must reconcile to
  `H * f_gross * rho` at the same factor version. If the residue ledger uses a
  different factor, the run fails.
- **Area.** `CAD_per_ha` rows must reconcile to the spatial unit's operable or
  licence area. A missing area is a coverage gap, not a zero.
- **Unit conversions.** All conversions use the registry:
  `1 ODT = 18.5 GJ`; `1 PJ = 1,000,000 GJ`; ODT/m3 from the residue ledger;
  m3/ha yield from the yield layer; m3 apparent to solid from the QC volume
  factor. A conversion row has `from`, `to`, `factor`, `source`, `version`.
- **No unit mixing.** A total may contain only rows with the same denominator
  after conversion. `CAD_per_odt_residue` and `CAD_per_m3_log` are converted
  before summing.

### 6.4 Fail-closed rules

The ledger fails the batch, with the row key and a descriptive message, on:

- a missing required field, unknown enum, duplicate key, or non-positive volume;
- a sign that violates §2.3, or a component outside its perspective;
- a negative `value_cad` other than a flagged credit;
- a unit/product mismatch, or a missing conversion factor;
- a source that is not registered and not prefixed `prov_`;
- a mixed nominal/constant total without a deflator;
- an identity residual above tolerance;
- a mirror row without its pair, or a pair with unequal absolute values;
- a value that cannot be traced to a source row/page;
- a v1 allocation that changes when the ledger is joined.

No default row, no zero fill, no silent drop, and no "adjust the residual".

## 7. Gaps

### 7.1 What BCTS does not provide

- Delivered log prices: BCTS reports standing bids, not mill-gate or roadside
  log prices.
- Actual harvesting, road, haul, and silviculture costs: those are the buyer's
  costs and are not in the auction result.
- Non-BCTS tenure values: TFLs, volume-based tenures, community forests, and
  private wood are outside the BCTS sample.
- Residue quantities, recovery costs, and mill-residue values.
- Processing margins and product prices.
- A 2025–2070 time path; BCTS results are sale-year snapshots.
- Statistical representativeness: BCTS sales are selected for auction and are a
  minority of BC harvest.

### 7.2 What the BC appraisal manual does not provide

- Audited actual costs: the manual provides **allowances** used to set stumpage,
  not invoices or productivity measurements.
- A transaction price for a specific stand: average market value is an
  administered average, and its price point must be established.
- Road maintenance actuals, haul contract rates, and silviculture outcomes.
- Residue quantities or recovery costs.
- Uncertainty bounds on the allowances.
- A time path across manual editions; the edition is a vintage, not a forecast.
- A guarantee that the allowance and the actual cost are equal; the difference
  is exactly the sector's residual, not a data error.

### 7.3 What QC MERIS/BMMB does not provide

- Full Crown volume: BMMB is auctioned volume only; MERIS coverage and field
  semantics are still being confirmed by the running source review.
- Private wood: tarification and BMMB cover Crown wood.
- Delivered log prices when the sale is standing.
- Actual costs: the tarification formula is administered, not audited.
- A BC–QC grade crosswalk and a common quality taxonomy.
- Residue quantities, recovery costs, and mill-residue values.
- A complete Crown revenue account if rents, fees, or credits sit in a separate
  system.
- A time path; the tarification year is a vintage.

### 7.4 What QC tarification does not provide

- Market-clearing prices; it is an administered rate, not a market price.
- Actual costs; its components are formula allowances.
- Marginal values by species/grade beyond the tariff classes.
- Residue, private wood, or processing margins.
- A documented uncertainty range or a market-based cross-check.

### 7.5 Cross-cutting gaps

- **Delivered log revenue** is the largest gap. Neither BCTS nor tarification is
  a log price deck. BC FEMIC VLM and QC mill settlements may help, but neither is
  registered as a transaction deck in this repository.
- **Actual costs** are missing for every component. Appraisal allowances and
  tarification formulas are not actuals.
- **Residue** is outside all three source families. It comes from the residue
  ledger, not the revenue ledger.
- **Tenure and sample bias**: BCTS and BMMB are selected subsets. A provincial
  average from them is not a provincial population estimate.
- **Species and grade crosswalks**: BC and QC classify species and quality
  differently; no versioned crosswalk exists.
- **Volume basis**: QC apparent versus solid, BC scale deductions, and residue
  ODT conversions are all unresolved at the source level.
- **Time path**: all sources are vintages, not a 2025–2070 path. Do not
  interpolate or extrapolate.
- **Other provinces**: no comparable value data; least deviation remains the only
  defensible allocation rule.
- **Processing margin**: out of boundary, so the ledger cannot report total
  sector value, GDP, or a full economic impact.
- **Taxes, subsidies, carbon, and non-market values**: out of boundary; the
  ledger is not a social cost-benefit analysis.
- **Statistical uncertainty**: no sampling frame or confidence intervals.
  Report coverage and confidence, not a false standard error.
- **Licensing and redistribution**: BCTS/BMMB terms may restrict publication;
  the ledger may be internal-only until the licence review closes.
- **Formula versioning**: appraisal manuals and tarification change annually;
  without an edition/year register, a value is not reproducible.

### 7.6 What would close the gaps

| Gap | Closure action | Priority |
|---|---|---|
| Delivered log revenue | Register a BC and QC log price deck by species/grade/region; document the price point and whether it is a transaction or an average | High |
| Actual harvest cost | Obtain contractor or licensee cost records for a representative BC/QC sample; compare to the appraisal allowance | High |
| Road and haul actuals | Obtain road construction/maintenance and trucking contracts; separate construction from maintenance and from haul | High |
| Silviculture actuals | Obtain regeneration and survey spend; separate the obligation from the levy | Medium |
| BCTS/BMMB representativeness | Compare auction volume and species mix to the provincial harvest; report the sample share and the selection bias | High |
| QC private wood | Register a private-wood price source or exclude private volume explicitly | Medium |
| Species/grade crosswalk | Build and version a BC↔QC species and quality crosswalk | High |
| Volume basis | Document QC apparent/solid factors and BC scale deductions; add them to the conversion registry | High |
| Residue cost/revenue | Link the ledger to the residue ledger; keep mill residue in a separate processing ledger | High |
| Time path | Decide hold versus documented path; do not interpolate | Medium |
| Other provinces | Keep least deviation; add value rows only when a source is registered | Low |
| Processing margin | Out of scope; state the boundary in every report | Low |
| Taxes/subsidies/carbon | Out of scope; do not net them into the ledger | Low |
| Statistical uncertainty | Add a sampling frame or report coverage only | Medium |
| Licensing | Complete the source licence review before publication | High |
| Formula versioning | Record the appraisal-manual edition and tarification year on every row | High |

## 8. Non-claims

- This note does not set a stumpage rate, a tax, a royalty, or a fiscal policy.
- It does not determine a legal AAC, a sustainable harvest, or a THLB.
- It does not validate the RSYC chain, the residue curve, or the OSeCAN model.
- It does not produce a national log price deck, a national cost curve, or a
  national net-value estimate.
- It does not make any BC/QC value `policy_grade`; the labels remain
  `screening_only` and `policy_grade=false`.
- It does not treat a BCTS/BMMB bid as a delivered log price, an appraisal
  allowance as an actual cost, or a tarification rate as a market price.
- It does not add Crown revenue to forest-sector revenue, and it does not use
  the sum of the two as a third independent estimate.
- It does not provide a statistical estimate of provincial value from a selected
  auction sample.

## 9. References

- `planning/forest-sector-lp-v1.md` — LP v1 objective, constraints, cost gaps,
  labels, and the least-deviation rule.
- `planning/harvest-aac-feasibility.md` — provincial ceilings, 2024 harvest
  template, cost-data inventory, and the warning against a flat harvest-cost
  adder.
- `planning/module-boundaries.md` — ownership, interface contract, split versus
  comparison modes, and labels.
- `planning/bc-woody-residue-supply-curve.md` — BC B1/B2 split, residue ratio,
  Stage B transport/processing basis, and the mill-residue double-count warning.
- `planning/national-bucking-supply-architecture.md` — product/grade taxonomy,
  `BuckedLog`, and `ResidueLedger`.
- `metadata/source-inventory.csv` — source ids, licence status, and review state.
- `data/derived/national/harvest_basis/provinces.csv` — jurisdiction codes and
  tenure caveats.
- `data/derived/national/harvest_basis/harvest_basis_template.csv` — 2021–2024
  harvest and AAC basis.
- `data/derived/national/harvest_basis/provincial_allocation/` — LP allocation
  artifacts and manifests.
- `data/derived/national/clews_supply/bc_woody_residue/audit/bc_cost_derivation.csv`
  — BC Stage B residue cost derivation.
- `metadata/ffcm-archive/parsed/fmu_supply.csv` — legacy flat log/residue cost;
  not a stumpage rate and not a marginal cost.
- `metadata/readiness-gates.yaml` — `calibrated_cost_surfaces` and related gates.

## Appendix A — Worked identity (illustrative)

Numbers are illustrative, not data. They show how the perspectives reconcile.

| Item | CAD/m3 log | Sign | Perspective |
|---|---:|---:|---|
| Delivered log revenue at mill gate | 100.00 | + | forest sector |
| Harvest residue revenue | 5.00 | + | forest sector |
| Harvest | 40.00 | − | forest sector |
| Road | 5.00 | − | forest sector |
| Haul | 20.00 | − | forest sector |
| Silviculture | 5.00 | − | forest sector |
| Residue recovery | 2.00 | − | forest sector |
| Overhead | 0.00 | − | forest sector |
| **Pre-Crown surplus** | **33.00** | | |
| Stumpage base | 15.00 | + / − | Crown / forest sector |
| Stumpage bid premium | 5.00 | + / − | Crown / forest sector |
| Admin fee | 1.00 | + / − | Crown / forest sector |
| **Crown revenue** | **21.00** | | Crown |
| **Forest-sector net value** | **12.00** | | forest sector |

Check: `33.00 = 12.00 + 21.00`. At 10,000 m3, PCS = 330,000 CAD, Crown revenue
= 210,000 CAD, forest-sector net value = 120,000 CAD.

Wrong calculations to reject:

- `100.00 + 21.00 = 121.00` — adds Crown revenue to log revenue.
- `100.00 - 15.00 - 5.00 - 1.00 = 79.00` — omits production costs.
- `42.50` (a BCTS winning bid) used as the delivered log price — treats a
  standing stumpage value as a log price.
- `38.10` (an appraisal allowance) reported as an actual harvest cost without a
  label.
- `12.00` reported with no coverage flag when the delivered price is a
  province-average deck, not a transaction.

## Appendix B — Source field crosswalk

| Ledger field | BCTS | BC appraisal manual | QC BMMB/MERIS | QC tarification |
|---|---|---|---|---|
| `jurisdiction` | BC | BC | QC | QC |
| `source` | `bcts_auction_results` | `bc_appraisal_manual` | `qc_bmmb_sales` / `qc_meris` | `qc_tarification` |
| `year` | Sale year | Manual edition / price year | Sale year | Tarification year |
| `spatial_unit` | Sale location / TSA / TFL | Appraisal zone / TSA / TFL | Region / UA | Region / UA |
| `species` | Sale species/grade | Appraisal species | Lot species/quality | Tariff species group |
| `product` | Sawlog/pulp/peeler as sold | Appraisal sort | Lot quality class | Tariff quality class |
| `volume_m3` | Sale volume | Appraised volume | Lot volume | Billed volume |
| `unit` | `CAD_per_m3_log` | `CAD_per_m3_log` or `CAD_per_ha` | `CAD_per_m3_log` | `CAD_per_m3_log` |
| `value_cad` | Upset or premium | Allowance | Sale price or premium | Administered rate |
| `value_basis` | `observed_sale:standing` | `appraisal_allowance:roadside` | `observed_sale:standing` or `:mill_gate` | `administered_rate:standing` |
| `perspective` | `crown` (and mirror) | `forest_sector` | `crown` or `forest_sector` by sale terms | `crown` (and mirror) |
| `component` | `stumpage_base`, `stumpage_bid_premium` | `harvest`, `road`, `haul`, `silviculture` | `stumpage_base`, `stumpage_bid_premium`, or `delivered_log_revenue` | `stumpage_base` |
| `sign` | +1 Crown / −1 sector | −1 sector | +1 or −1 by perspective | +1 Crown / −1 sector |
| `confidence` | High for bid | Medium | Medium | High for rate |
| `licence` | BCTS sale id | Cutting authority | BMMB lot / permit | CAAF / UA |
| `notes` | Premium split, currency | Edition, zone, residual flag | Sale terms, quality, volume basis | Formula, region, year |
