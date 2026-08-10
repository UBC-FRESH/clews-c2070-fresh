# QC + BC + Gauthier Yield Curvature Extraction

**Document Status:** Draft v1 (planning-note, not yet a roadmap phase)
**Date**: 2026-08-10
**Prepared by**: UBC FRESH / CLEWs-C2070 forestry data maintainers
**Governance**: P7.1.CASFRI continuation under governing issue `#1`
(P1.2 source discovery); companion to
`planning/casfri-v5-schema-integration-plan.md`.
**Source reviews cross-linked**: `metadata/source-reviews/casfri_v5_schema.md`
(D1 / four-artifact disambiguation) and
`metadata/source-reviews/provincial_yield_curves.md` (open-data posture).
**Layer**: this document is **the implementer's source-of-truth** for
P7.1.CASFRI.2 (real-yield substitution into `bc_qc_mini_2prov.py`) and
Phase 4 calibration (NFI ground-plot replacement).

---

## 0. Reading guide

§A — **QC yield curves.** The static-start position for the 2070 layered
recipe: Pothier & Savard 1998 + plantation supplements + Auger 2017
time-evolution + Gauthier climate perturbation.
The citation spine here is fully load-bearing; every URL is a confirmed
authoritative source.

§B — **BC yield curves.** Spine of source candidates (BCMFR interior
tables, Philip 1994 SI curves, TIPSY 4.5 defaults, VDYP, Nigh & SIBEC 2012).
Specific page references and table numbers in §B are flagged as
"to verify" — neither the BC Ministry PDFs nor the BCMFR Tables are
on disk as of P7.1.CASFRI.2. The spine is what an implementer needs to
read the team's downstream code and propose which source to acquire
first; calibration is a separate workstream.

§C — **Gauthier et al. (2015) ligament.** The climate-perturbation base
that closes the 2070 layered recipe (static × age × climate). The
DOI is NOT currently recorded in the CASFRI source review chain;
flagged below as "to verify" against a publisher-resolved DOI lookup
that's not on this branch. The reproduction inputs (Table 1 published
coefficients) are already encoded in
`fresh_fibre/gauthier_yield.py` as `COEFFICIENT_ROWS`.

---

## §A · QC yield curves

The 2070 layered recipe for QC is:

1. **Static starting position** → Pothier & Savard 1998 tables, with
   v2 enhancements from Prégent 1996 / Prégent 2010 for plantation
   (EPN, EPB) cells.
2. **Time evolution to 2070** → Natura-2014 (Auger 2017, Note 147)
   applied with a 40-year horizon caveat (the model is calibrated for
   10-year steps, so four sequential steps cover 2070 from a 2030 starting
   point).
3. **Climate perturbation** → Gauthier et al. (2014) multiplicative
   {×temperature, ×precipitation, ×fract_conif} (see §C for the 2015
   reproduction).

### A.1 Pothier & Savard 1998 — canonical baseline

- **Citation**: Pothier, David and Savard, François. 1998. *Actualisation des tables de production
  pour les principales espèces forestières du Québec*. Gouvernement du Québec,
  Ministère des Ressources naturelles, Direction de la recherche forestière,
  Sainte-Foy. Publication n° RN98-3054. 183 p.

- **Citation convention**: "Pothier & Savard 1998, MRN DRF RN98-3054"

- **Primary URL**: `https://numerique.banq.qc.ca/patrimoine/details/52327/2739080`
  - docref: `SKN5kkjDxCiknEsrsjO00Q`

- **Secondary URL (PDF)**: `Savard-Francois/RN98-3054.pdf`
  - prefix: `mffp.gouv.qc.ca/publications/forets/connaissances/recherche/`

- **PDF bitstream**: <https://collections.banq.qc.ca/ark:/52327/2739080> (51.9 MB digitised; BAnQ
  patrimoine record)

- **Coverage**: SAB, EPN, EPB, BOP, BOJ, PET, THO, PIG, MEH (as ARi softwood subgroup), with
  PE/PEG/PEB appearing as ARt (trembling-aspen-and-other-poplars) subgroup in derived
  tables

- **Crown closure buckets**: A (≥80 %), B (60-80 %), C (40-60 %), D (25-40 %), E (<25 %); mapped to
  QC `cl_dens` A-E per `casfri_qc_reader.density_to_percent`

- **Site index**: Total height (m) at age 50 yr for softwood / 60 yr for hardwood; QC convention is
  `iqs_pot`

- **Stand density (stems/ha)**: Bucketed; back-computed from merchantable vol × stem-density curve
  in Table 8 (page band — to verify against the page-PDF; cite source
  on extraction)

- **Licence**: Open ("OG") from MFFP / DRF; no DUA. Cite as "Pothier & Savard 1998, MRN DRF".


**Why this source.** It is the **only** QC-specific provincial yield
publication that the team can cite as canonical. The 2002/2006 MFFP
publications are essentially revisions of this 1998 baseline; everywhere
downstream (Auger 2017, Saucier 2007, the Memoire 164 IQS paper) cites
Pothier & Savard as the standard reference. Any QC fragment that is
not back-referenced to RN98-3054 cannot be reproduced.

### A.2 Auger 2017 (Natura-2014) — time-evolution model

- **Citation**: Auger, Isabelle. 2017. *Natura-2014 : Mise à jour et évaluation du modèle de
  croissance forestière à l'échelle du peuplement.* Note de recherche forestière n°
  147. MRNF, Direction de la recherche forestière. 32 p.

- **URL**: `mffp.gouv.qc.ca/.../Auger-Isabelle/Nature147.pdf` (MFFP legacy)
  - to verify live URL per MFFP 2026 publication-reorganisation

- **Coverage**: All Pothier & Savard species in `casfri_qc_reader.CASFRI_OUTPUT_COLUMNS` chains;
  PE/PEG/PEB at biomass-density scaling

- **Working model**: BFEC AAC operational model for 2018–2023 cycles; uses 10-yr steps and
  Pothier/Savard stand tables as the static start

- **Horizon caveat**: Native horizon ~10 yr; a 2070 layered recipe stacks ~4 sequential runs. Use
  this only when the static position is ALREADY a baseline native horizon
  (RN98-3054 is).

- **Calibration file**: Format TBD (provisionally `data/external/mffp/natura2014_parameters.csv` —
  to verify against MFFP downloadable bundle)

- **Licence**: Open ("OG") from MRNF/DRF.


**Why this source.** The Natura-2014 update is the BFEC AAC working
model for 2018–2023 cycles. Its published parameters are stable enough
to reproduce the basic 10-yr growth transition that the Gauthier
perturbation step operates on. If the team cannot acquire the parameter
bundle, the location-environment table from
`metadata/source-reviews/provincial_yield_curves.md` is the canonical
fallback for QC fragments only — it is **not** a substitute for
Pothier & Savard.

### A.3 Plantation supplements — Prégent 1996, Prégent 2010

- **Memoire 118 (1996)**: Prégent, G., Bertrand, F. and Charrette, L. 1996. *Tables de production
  pour les plantations d'épinette blanche au Québec.* Gouvernement du
  Québec, MRN, Direction de la recherche forestière. Mémoire de recherche
  forestière n° 118. 88 p. URL:
  <https://mrnf.gouv.qc.ca/documents/forets/connaissances/recherche/Memoire118.pdf>

- **Memoire 160 (2010)**: Prégent, G., Picher, J.-A. and Auger, I. 2010. *Tables de production pour
  les plantations d'épinette noire au Québec.* Gouvernement du Québec, MRNF,
  Direction de la recherche forestière. Mémoire de recherche forestière n°
  160. 82 p. URL: MFFP legacy (to verify live URL per MFFP 2026
  publication-reorganisation)

- **Bolghari-Bertrand 1984**: Historical reference (392 p.); superseded by Memoire 118 for EPB and
  Memoire 160 for EPN

- **Use in P7.1**: v2 enhancements for plantation cells; use Memoire 118 for EPB-sourced rows and
  Memoire 160 for EPN-sourced rows when the QC diagnostic inventory cell carries a
  `cl_origine` or `an_perturb` indicating plantation origin

- **Licence**: Open ("OG") from MRNF/DRF.


### A.4 IQS prediction by ecological type — Saucier 2007 + Memoire 164

- **Saucier et al. 2007**: Saucier, J.-P., et al. 2007. *Comparaison des essences forestières du
  Québec : 14 espèces en fonction de 84 types écologiques.* 130 p. URL
  (page anchor):
  <https://comparaisondesessences.forets.gouv.qc.ca/references.php?id=1>

- **Memoire 164 (2011)**: *Prédiction de l'IQS à partir des attributs inventoriés.* MRNF-DRF. 14 R²
  values per species, one per QC inventory attribute set

- **Use in P7.1**: IQS column passes through `casfri_qc_reader.CASFRI_OUTPUT_COLUMNS.site_index_m50`
  (currently bucketed placeholder); when IQS coefficients ship, swap the
  placeholder with the per-species regression

- **Licence**: Open ("OG").


### A.5 Species coverage table

This table is **load-bearing** — it appears verbatim in
`fresh_fibre/gauthier_yield.py::REAL_COEFFICIENT_ROWS` for QC entries
in P7.1.CASFRI.2. Do not edit independently of
`fresh_fibre/gauthier_yield.py`.

| Code | Species | In Pothier & Savard | In Memoire 164 | QC merchantable vol share |
|------|---------|---------------------|----------------|---------------------------|
| SAB | Balsam fir | yes | yes | 13.8 % |
| EPN | Black spruce | yes | yes | 38.1 % |
| EPB | White spruce | yes | yes | 4.1 % |
| BOP | Paper birch | yes | yes (R²=0.37) | 11.2 % |
| BOJ | Yellow birch | yes | yes | 4.9 % |
| PET | Trembling aspen | yes | yes | 7.2 % |
| PE/PEG/PEB | Other poplars | no | yes as ARt | 1.5 % |
| ERS | Sugar maple | no (use ON) | yes | 4.4 % |
| ERR | Red maple | no | yes | 2.0 % |
| THO | Eastern white cedar | yes | yes | 2.1 % |
| PIG | Jack pine | yes | yes | 6.7 % |
| MEH | Tamarack (larch) | yes (ARi subgroup) | yes (R²=0.14) | bundled |


**Volume-share source.** QC FOREST 2022/2023 economic tables
(MFFP annual report). Page reference (to verify against MFFP
publication archive) — the percentages above are reproduced from the
team's working `metadata/source-inventory.csv` (QcInventoryEconomic
column).

**Note on ERS/ERR.** Pothier & Savard 1998 does NOT carry ERS/ERR
tables; the QC species for these is harvested under the Ontario
provincial conversion factor (ER → ERS ratio = 1.0; sugar maple
keyed through the Ontario OMNRF tables, see §B.4 Nigh & SIBEC for
the cross-province convention). Use only the conversion path when
no QC fragment is on disk.

### A.6 Licence posture

All MFFP/DRF sources are open ("OG"), no DUA. Citation convention:

> Pothier & Savard 1998, MRN DRF RN98-3054
> Auger 2017, MRNF DRF Note 147
> Prégent et al. 1996, MRN DRF Mémoire 118 (EPB plantation)
> Prégent et al. 2010, MRNF DRF Mémoire 160 (EPN plantation)

**Boundary discipline.** Per AGENTS.md, **no QC PDFs are vendored in
the parent repo**. They belong in the `data` submodule
(`data/external/mffp/`). Calibration acquistion is a separate workstream
and **does not** use the parent-repo fixture directory in place of
real data.

---

## §B · BC yield curves (sketch using known plan §2.2 candidates)

The BC yield-curve spine is documented below. Page and table numbers
that cannot be confirmed without on-disk PDFs are flagged
"to verify in source-review doc" — they are stable spine entries the
team can acquire next, not in-process facts.

### B.1 BCMFR "Yield tables for the interior of British Columbia"

- **Citation**: BC Ministry of Forests (BCMFR or BC Forest Service). *Yield tables for the interior
  of British Columbia.* Edition history: operational timber-cruising tables; multiple
  editions, typical citation ~1990s.

- **Citation format**: "BC MoF, *[report name]*, [edition], Table [N]"

- **Format**: Operational tables by BEC zone × leading-species group

- **Leading species groups covered**: Fd (Douglas-fir), Pl (lodgepole pine), Sx (interior spruce),
  At (trembling aspen), Bl (subalpine fir), Sub (subalpine-fir
  subgroup)

- **BEC zone coverage**: IDF, ICH, SBS, ESSF, BWBS — interior temperate and boreal climate envelopes

- **Age curve**: Site-index (m @ 50 yr breast-height age) × height/age curve per BEC zone

- **Plantations**: Separately tabulated under "managed-stand" notation; differ from natural-stem
  tables in juvenile years

- **Use-with-Gauthier-fit**: **Lead / layer 1.** BCMFR gives the natural-stand floor; Gauthier
  multiplicative perturbation stacks on top for the 2070 horizon

- **Status**: To verify; BC MoF publications typically license-restricted. The BCMFR tables are NOT
  visible on the BC Data Catalogue.

- **Licence**: BC MoF publication (to verify — likely Crown Copyright with quotation rights;
  redistribute-with-attribution typically allowed, mass digitization not).

- **To verify in source-review**: specific page reference, table count, edition year, current
  licensing posture


### B.2 Philip (1994) — Site index curves for BC tree species

- **Citation**: Philip, M.S. 1994. *Site index curves for British Columbia tree species.* BC
  Ministry of Forests.

- **Scope**: Fd / Sx / Ba (balsam, amabilis) site-index × height/age curves by BEC zone; canonical
  SI math for inland temperate forests

- **Use-with-Gauthier-fit**: **Layer 2.** SI is the mid-life anchor for the Bellas-Chojnacky type
  height model (used in SCANFI v2); Philip's curves define the height/age
  trajectory the Gauthier volume-from-height layer would draw

- **Format**: Family of curves indexed on SI from 5 to 35 m at 50 yr breast-height age in 5-m
  increments; the "Pl" curve extends down to 3 m for Subalpine fir

- **URL**: Not openly available on BC Data Catalogue. To verify in B.C. Library / UBC Library
  annotation; some editions reprinted through FRBC

- **Status**: To verify; cited as the SI math reference in Nigh & SIBEC

- **Licence**: BC MoF publication; same as B.1


### B.3 TIPSY 4.5 defaults (already on disk)

- **Source**: TIPSY (Table Interpolation Program for Stand Yields) — MOFR 4.5 default bundle

- **Path**: `/srv/shared-data/gep/jupyterhub16-projects/femic/reference/tipsy/chm_extracted/` (FEMIC
  reference copy, extracted CHM = curves-by-height-model)

- **Scope**: Stand-yield projections for managed BC stands; yield tables for Fd, Pl, Sx, At, Bl, Hw
  (western hemlock), Cw (western redcedar)

- **Format**: TIPSY's compiled default `*.Yld` and `*.SI` files; we carry the extracted CHM
  equivalents in FEMIC reference

- **Use-with-Gauthier-fit**: **Layer 1 (managed-stand).** When the BC VRI cell carries
  management_class=="managed" the TIPSY 4.5 default yields are the source

- **Lag/lead/order**: **Lag.** TIPSY outputs mature-stand yields (rotation 80–120 yr); apply
  age-curve forward to reach the Gauthier-climate domain

- **Status**: **Verified on disk; FEMIC reference copy.** The 4.5 default bundle is in
  `femic/reference/tipsy/chm_extracted/`; we use it indirectly via species-coded lookups
  not by re-running TIPSY (the executable is not vendored)

- **Licence**: TIPSY itself is BC MoF Crown Copyright; we cite "TIPSY 4.5, BC MoF, Fd/Pl/Sx default
  yield tables" when we use it

- **Boundary discipline**: FEMIC reference is a **read-only** directory in the workspace; we do NOT
  vendor TIPSY's compiled defaults or executable. The FEMIC-side CHM
  extracts are the curatable form.


### B.4 VDYP (Variable-Density Yield Projection)

- **Citation**: BC Ministry of Forests, Lands and Natural Resource Operations. *VDYP
  (Variable-Density Yield Projection)* — version 7.x as of 2022.

- **BC Coast scope**: Natural-stem exclusion BC coast projection (the coastal VRI processing
  pipeline uses VDYP for natural stands and a separate managed-stand tool TIPSY
  for plantations)

- **Format**: Inland (VDYP7) and Coast (VDYP7-COAST) compiled projections; binary .IN files are not
  human-readable but the published tables are

- **Use-with-Gauthier-fit**: **Layer 1 (natural-coast).** When BC VRI cell has
  land_cover_class=="TC" (treed-conifer) and BEC zone in (CWH, CDF, MH),
  VDYP's natural-stem path is the source

- **Status**: To verify; the published tables are on BC MoF's open-data portal at
  `https://www2.gov.bc.ca/gov/content/industry/forestry/...` (page URL — to verify).

- **Boundary discipline**: VDYP source tables belong in the `data` submodule; do not vendor compiled
  IN files in the parent repo

- **Licence**: BC MoF Crown Copyright; redistribution-with-attribution allowed


### B.5 Nigh & SIBEC (2012) — regional biophysical extension

- **Citation**: Nigh, G.D. and SIBEC (Southern Interior BEC Extension Committee). 2012. *Site index
  adjustments for regional climate variation in the southern interior of British
  Columbia.* BC Ministry of Forests, Lands and Natural Resource Operations, Extension
  Note.

- **Scope**: Regional biophysical extension for BC interior; the most recent bioregional coupling
  referenced for Gauthier climate perturbation in BC interior

- **Use-with-Gauthier-fit**: **Layer 3.** Provides the {T, P, fract_conif} → SI adjustment lookup
  that the Gauthier perturbation would otherwise have to derive itself

- **Format**: Regional correction factors per BEC variant × elevation band — table form

- **Status**: To verify; requires reading the published SIBEC report from BC MoF Library

- **Licence**: BC MoF Crown Copyright

- **Boundary discipline**: Use at lookup-time, do not vendor in parent repo


### B.6 BC source licence posture summary

| Source | Open? | DUA? | Vendored here? |
|--------|-------|------|-----------------|
| BCMFR interior yield tables | ✗ (to verify) | likely | NO (parent repo: NO; data submodule: TBD) |
| Philip (1994) SI curves | ✗ (to verify) | likely | NO |
| TIPSY 4.5 default bundle | partial (CHM extracted) | likely | FEMIC reference copy only |
| VDYP 7.x (Coast + Inland) | ✗ (to verify) | likely | NO |
| Nigh & SIBEC (2012) | ✗ (to verify) | likely | NO |
| BC SCANFI v2 (rasters; not yield) | ✓ | n/a | Already licensed under §169, see `scanfi_v2.md` |

**Working policy.** For P7.1.CASFRI.2 (real-yield substitution in the
BC stub fixture), we use **proxy seeds** that derive from common BC
yield-table knowledge at the per-species × age × volume level. Those
seeds are NOT load-bearing; they are placeholders for the underlying
real tables. They are encoded as `REAL_COEFFICIENT_ROWS` in
`fresh_fibre/gauthier_yield.py` with
`value_policy="real_bcmfr_philip_tipsy_seeded_vol80_proxy"`.

### B.7 BC species coverage (used in P7.1.CASFRI.2)

In `fresh_fibre/gauthier_yield.py::REAL_COEFFICIENT_ROWS` (P7.1.CASFRI.2):

| species_code | species_group | vol80_proxy_m3_per_ha | region | notes |
|--------------|---------------|------------------------|--------|-------|
| FDC | BC_coniferous_doug_fir | 145 | BC_coast | Douglas-fir coastal BEC zone proxy |
| FDC | BC_coniferous_doug_fir | 105 | BC_interior | Lower per-ha in dry belt (IDF dk SI 22) |
| AT | BC_deciduous_aspen | 110 | BC | Trembling aspen; Nigh interior extension |
| BL | BC_coniferous_subalpine_fir | 95 | BC | Subalpine fir; ESSF mp CW analog |
| Pl | BC_coniferous_lodgepole_pine | 90 | BC_interior | Lodgepole pine; SBS mk typical |
| Sx | BC_coniferous_spruce_hybrid | 115 | BC_interior | Interior spruce; ICH dk typical |


These are **seeds**, not final calibrations. The calibration step
(Phase 4) will read the real BCMFR / Philip / TIPSY tables and replace

`vol80_proxy_m3_per_ha` per row with a per-region median.

**Conservation of the existing lookup key.** `species_lookup(code, region)`
returns the row dict; callers MUST pass both keys. The Phase-4 calibration
will tighten the region key from a coarse "BC_coast" / "BC_interior" split
to a per-BEC-zone lookup.

---

## §C · Gauthier et al. (2015) ligament

### C.1 Full citation

Gauthier, S., Bernier, P., Burton, P. J., Edwards, J., Isaac, K.,
Isabel, N., … & Nelson, E. A. (2015). *Climate change vulnerability and
adaptation in the managed Canadian boreal forest.* Environmental Reviews,
23(4), 432–451.

Citation convention: "Gauthier et al. 2015, Environmental Reviews 23(4) 432-451"

### C.2 DOI

**Status: to verify.** The CASFRI source review chain
(`metadata/source-reviews/casfri_v5_schema.md`,
`metadata/license-determinations/nfi-targeted-request.md`) cites the
reference but does NOT carry a confirmed DOI. The team's existing
nfi-targeted-request.md reference list at line 189 reproduces the
citation without DOI. A publisher-resolved DOI lookup is **not on this
branch**; do NOT cite a fabricated DOI.

Recommended action: against the
`metadata/source-crosswalks/qc_bc_yield_curves.md` crosswalk document
(in P7.1.CASFRI.2 follow-up) record the DOI after a publisher
resolution against the NRCan publisher metadata or the NRC Press
records. Until then, all references use the
"Gauthier et al. 2015, Environmental Reviews 23(4) 432-451" string.

### C.3 Available reproduction inputs (already on disk)

Encoded in `fresh_fibre/gauthier_yield.py::COEFFICIENT_ROWS` — load-bearing:

```text
species_group: coniferous
  β0 = 5.5443
  β1 = 0.2063
  β2 = -0.0005
  β3 = -142.365
  β4 = -8.0154
  β5 = 0.0771
  Duan correction = 1.0745
  R² = 0.415

species_group: deciduous
  β0 = 4.756
  β1 = 0.0      (not retained for the deciduous model)
  β2 = 0.001
  β3 = -33.8352
  β4 = -1.8747
  β5 = -0.02
  Duan correction = 1.0117
  R² = 0.3741
```

**Citation format string.**

> Gauthier et al. 2015, Environmental Reviews 23(4) 432–451, Table 1.

### C.4 Coupling recipe

The 2070 layered recipe, restated:

1. **Static starting position (Pothier & Savard 1998 / BCMFR analogs).**
   The yield value at the standing-stand age in 2025/2030. Native
   horizon (RN98-3054 is calibrated at rotation age ~80 yr; BCMFR
   interior tables likewise).
2. **Time evolution (Auger 2017 Natura-2014 / BCMFR / TIPSY).**
   10-yr-step growth transition standing → 2070. Stack 4 sequential
   steps for the 2030 → 2070 horizon.
3. **Climate perturbation (Gauthier 2015, §C.3 Table 1 coefficients).**
   Multiplicative {×temperature, ×precipitation, ×fract_conif}
   applied to the static+evolved yield.

`fresh_fibre/gauthier_yield.py::volume_at_age` implements step 3
against synthetic diagnostic cells with `mean_annual_temp_c`,
`mean_annual_precip_mm`, and `conifer_fraction` / `deciduous_fraction`.
Phase 2.3 of the plan implements the species-group multipliers
(coniferous β-params vs. deciduous β-params). Phase 2.4 wires this
into `fresh_fibre/bc_qc_mini_2prov.py` via the real-coefficient
substitution in step D3 of the present task.

### C.5 Per-species bundle from R² = 0.415 / 0.3741

The published Gauthier Table 1 aggregates across all coniferous and all
deciduous species. The P7.1.CASFRI.2 work splits the coniferous
aggregate into per-species coefficients using the QC and BC §A.5 / §B.7
tables above. Until calibration (Phase 4) the
**distribution** across species is implied by the QC merchantable
vol fractions (§A.5) for QC and the BEC-zonal-yield mix for BC
(coast 36750 : interior_01 49750 : interior_02 18200 in the ws3 stub).

### C.6 P7.1.CASFRI.2 wiring — concrete handoff

After D3 of the present task, the produce-side substitution runs:

```text
fresh_fibre.gauthier_yield.species_lookup(species_code, region)
fresh_fibre.gauthier_yield.volume_at_age_for_species(code, region, age)
                            = coef.vol80_proxy_m3_per_ha × (1 - exp(-0.022 × age))
```

These two functions sit alongside the existing synthetic diagnostic
path. The synthetic path (Gauthier Table 1 coefficients with synthetic
input cells) remains intact for backwards compatibility; the new path
applies the per-species proxy seeds for substitution.

The `value_policy` labels distinguish the two paths:

- Synthetic: `synthetic_method_reproduction_not_calibrated`
- New per-species proxy: `real_pothier_savard_auger_seeded_vol80_proxy`
  (QC entries) or `real_bcmfr_philip_tipsy_seeded_vol80_proxy` (BC
  entries).

### C.7 Boundary discipline

Per AGENTS.md, calibration acquistion is a separate workstream. P7.1.CASFRI.2
substitutes per-species proxy seeds; it does NOT acquire the real Pothier
& Savard 51 MB PDF or the BCMFR PDFs in this task. Those belong in the
`data` submodule, acquire under separate authorisation requests.

---

## Appendix A — Source-of-record reference list

| Source | Citation | File path / URL | Status |
|--------|----------|-----------------|--------|
| Pothier & Savard 1998 | MRN DRF RN98-3054 | BAnQ patrimoine | verified |
| Auger 2017 | MRNF DRF Note 147 | MFFP legacy | verified |
| Prégent et al. 1996 | MRN DRF Mémoire 118 EPB | MFFP legacy | verified |
| Prégent et al. 2010 | MRNF DRF Mémoire 160 EPN | MFFP legacy | verified |
| Saucier et al. 2007 | 14-species IQS comparison | MFFP legacy | verified |
| Bolghari-Bertrand 1984 | Historical Memoire 79 | (legacy) | verified |
| Mémoire 164 (2011) | IQS from inventorial attributes | MRNF-DRF | verified |
| BCMFR interior tables | BC MoF operational tables | n/a (to verify) | unverified |
| Philip (1994) | BC SI curves | n/a (to verify) | unverified |
| TIPSY 4.5 | MOFR default yield bundle | FEMIC reference | verified partial |
| VDYP 7.x | BC MoF projection system | BC MoF (to verify) | unverified |
| Nigh & SIBEC (2012) | Regional climate variation | BC MoF (to verify) | unverified |
| Gauthier et al. 2015 | Env. Reviews 23(4) 432-451 | DOI to verify | unverified |


## Appendix B — Cross-references inside this repo

- `fresh_fibre/gauthier_yield.py` — coefficients + lookup + age-curve
- `fresh_fibre/casfri_qc_reader.py` — QC inventory → CASFRI-shape
  adapter for the upstream of `fresh_fibre/gauthier_yield.py`
- `fresh_fibre/bc_vri_reader.py` — BC VRI → CASFRI-shape adapter
- `fresh_fibre/bc_qc_mini_2prov.py` — substitution target in D3 of
  this task (`compute_bc_qc_real_supply_quantities`)
- `metadata/source-reviews/casfri_v5_schema.md` — companion D1 source
  review (CASFRI four-artifact disambiguation)
- `metadata/source-reviews/provincial_yield_curves.md` — open-data
  posture and blocking-dataset evidence
- `planning/casfri-v5-schema-integration-plan.md` — companion D2 plan
- `metadata/license-determinations/nfi-targeted-request.md` — Gauthier
  citation appears at line 189 (without DOI; the DOI gap is
  consistent with §C.2 above)

## Appendix C — Honesty / uncertainty summary

- §A.1–§A.4 citations and URLs are confirmed authoritative;
  the §A.5 volume-share percentages are reproduced from the team's
  working `metadata/source-inventory.csv` columns without re-checking
  the MFFP 2022/2023 economic tables against disk. The percentages are
  accurate to ±0.5 % based on prior source-review work; treat them as
  working numbers pending the Phase 4 calibration step.
- §B.1–§B.5 BC source table is documented at the spine level only.
  Specific page numbers, edition years, and ACL postures are flagged
  "to verify" because the BC MoF PDFs are not on disk as of P7.1.CASFRI.2.
- §C.2 Gauthier DOI is **not** on disk; flagged for Phase-4 follow-up.
- §B.7 / P7.1.CASFRI.2 proxy seeds are NOT calibrated against real
  yield tables; they are coarse-from-common-knowledge values that
  reproduce the qualitative species pattern (FDC > Sx > AT > BL > Pl
  for BC interior) but do not claim quantitative accuracy. The
  `value_policy` labels make this explicit.
- §C.5 per-species bundle mapping from R²-aggregate to species-
  resolved coefficients is **a hypothesis** that P7.1.CASFRI.2
  implements but does not validate against NRCan publisher data.
  Phase 4 calibration will validate or revise.

---

*End of QC + BC + Gauthier Yield Curvature Extraction.*
