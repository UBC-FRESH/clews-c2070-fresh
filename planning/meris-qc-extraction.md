# MERIS QC cost/revenue extraction — sample and field crosswalk

| Field | Value |
|---|---|
| Date | 2026-11-20 |
| Status | `screening_only`; `policy_grade=false`; sample extraction, not a full provincial dataset |
| Scope | Quebec BMMB/MFFP MERIS 3.3.0: archive, ACCDB tables, cost/revenue fields, ledger mapping |
| Data artifact | `data/derived/national/harvest_basis/meris_qc_sample/` |
| Parent design | `planning/forest-cost-revenue-ledger.md` |
| Access | Public download; read-only `mdbtools` 1.0.0; no password or Access UI needed |

**Bottom line.** MERIS is an analysis and simulation tool, not a register of
actual harvest. The back-end (ACCDB, 264 tables) exposes three useful layers:
(1) current administered stumpage rates by 191 tarification zones and 14 species
groups (`tbl_redevances_F1234`, 2025-09-19); (2) supply-cost parameters by zone
(road, haul, loading, scaling, administration) plus treatment rates
(`tbl_couts_zones`, `tbl_Liste_Traitements`); and (3) an economic value
decomposition in $2023 (`tbl_Zones_RedevBenefRentSal_F1234`). It does **not**
contain an observed delivered log price, actual permit volumes, or a
tarification-zone-to-UA crosswalk. The revenue table
`tbl_essences_revenus_EssDhp` is a placeholder: all 1,824 `SCIAGE` and `PATE`
values are `0.0100`. The only complete revenue/cost arithmetic is a shipped
demo simulation, which reconciles to the reported profit but is explicitly not
policy data.

## 1. MERIS archive/DB

- Page: `https://bmmb.gouv.qc.ca/analyses-economiques/outils-d-analyse/`
  (redirects to `https://encheres.forets.gouv.qc.ca/analyses-economiques/outils-d-analyse/`).
- Zip: `https://encheres.forets.gouv.qc.ca/media/29260/meris.zip`, 41,762,907
  bytes, SHA-256 `145431a93b466525751e08f3fe73437de3aee49f3f6d78dc9d77378769f9faa3`.
- The archive contains `MERIS_BdTbl.accdb` (70,893,568 bytes, SHA-256
  `033f8cc1f428430cd66d247e022ef78ab76b3d78cedae00df498177b5c087857`), the
  compiled `MERIS.accdr` UI (85 MB), `Import-MERIS.xlsm`, `MERIS_Aide.doc`,
  `Conversion-Redevances.xlsm`, `Notes_Versions.txt` and `Shell.bat`.
- The database is **ACCDB**, not the `.mdb` named in the brief. `mdbtools`
  1.0.0 (`mdb-tables`, `mdb-count`, `mdb-export`) reads it directly. No
  password, encryption, lock, or Access UI was encountered.
- `mdb-schema` 1.0.0 fails on ACCDB with `Invalid backend type`. Field lists
  are from `mdb-export` headers and `MERIS_Aide.doc`; exact declared types and
  indexes are not available through this tool.
- Version: MERIS 3.3.0, Access 2016 (`Shell.bat`); DB release 2025-09-23.
- Archive/DB hashes and member sizes: `meris_qc_sample/provenance.json`.

## 2. Tables/schema/fields

264 tables were listed; 20 key tables were counted. Full list in
`tables_all.txt`, counts in `tables_key.csv`.

| Table | Rows | Fields that matter |
|---|---:|---|
| `tbl_Liste_ZonesRedev` | 191 | `Zone_Redev`, `Secteur` |
| `tbl_redevances_F1234` | 2,674 | `Zone_Redev`, `CategorieEssence`, `Redev_Der`, `Redev_f1..f4`, `Redev_Pate`, `Redev_Autre`, `RdvVariable`, `DateMaj` |
| `tbl_Zones_RedevBenefRentSal_F1234` | 2,674 | `Rdv_*`, `Bnf_*`, `Rsl_*`, `Aut1_*`, `Aut2_*`, `Tot_*` for DER/F1/F2/F3/F4/PATE/AUTRE |
| `tbl_couts_zones` | 191 | `chemin_entretien_dol_m3`, `chemin_constr_prim_sec_dol_m3`, `chemin_constr_tertiaire_dol_ha`, `transport_usine_*`, `transport_marche_*`, `transport_fardier_dol_m3`, `chargement_dol_m3`, `dechargement_dol_m3`, `mesurage_dol_m3`, `admin_dol_m3`, `autre*` |
| `tbl_Liste_Traitements` | 94 | `Traitement`, `StrTypeTrait`, `Unite_Mesure`, `Categorie`, `Taux_EXE`, `Taux_PS`, `Autre_cout`, `Valeur_Tot` |
| `tbl_Equations_Recolte` | 45 | `Essence`, `Type_equation`, equation text, `1_C1..2_C11` |
| `tbl_Profil_Rentabilite_EqGraph` | 30 | `Volume_Tige`, `Cout_CT`, `Cout_CT_FT`, `Cout_CPplus`, `Cout_CPplus_FT`, `Cout_CPmoins`, `Cout_CPmoins_FT` |
| `tbl_Essences_b` | 45 | `Essence`, `Type`, `Regroupement`, `GroupeProdFPInterface` |
| `tbl_essences_revenus_EssDhp` | 1,824 | `Type` (courant/tendance), `ESSENCE`, `DHP`, `SCIAGE`, `PATE`, `ORIGINE`, `InclureRedev` |
| `tbl_770rq_Resultats_Rentabilite` | 54 | `M3ha_Tot_Net`, `Valeurs_Produits`, `Redevances`, `Frais_Variables`, `Frais_Fixes`, `Benefice_Net_m3_net` |
| `tbl_775rq_Resultats_Rentabilite_Frais` | 54 | `Recolte_Debardage`, `Transport_Usine`, `Transport_Marche`, `Chargement`, `Dechargement`, `Chemin_Construction_*`, `Entretien_Chemin`, `Mesurage`, `Administration`, `Tot` |
| `tbl_Rentabilite` | 18 | 64 fields; scenario, volume by product, `Valeurs_Produits`, `Redevances`, `Credits`, cost breakdown, `Benefice_Net_*`, inclusion flags |
| `tbl_parametres_rentabilite` | 24 | `Categorie`, `Parametres`, `Inclus_Rentabilite_*` |
| `tbl_Profil_Rentabilite` | 5 | sawmill profiles and variable-cost equation coefficients |
| `tbl_Volume_Prescription` | 0 | empty |
| `tbl_unifiee_assigne_zones` | 1 | not a provincial assignment table |
| `tbl_unifiee_assigne_traitements` | 1 | not a provincial assignment table |
| `tbl_Compile_Global` | 0 | empty |

### Units and semantics

- `tbl_redevances_F1234`: CAD/m3, current administered rates, all
  `DateMaj=2025-09-19`, `RdvVariable=0` for all 2,674 rows. Seven product
  classes per species group: DER, F1, F2, F3, F4, PATE, AUTRE.
- `tbl_Zones_RedevBenefRentSal_F1234`: CAD/m3 in **$2023** (manual §4.8.1).
  `Rdv` = redevance tendance; `Bnf` = profit of harvesting/processing firms;
  `Rsl` = labour rent; `Aut1` = CPRS adjustment of 25 $/m3 net in 3.3.0;
  `Tot` = sum. This is an economic value decomposition, not a market price.
- `tbl_couts_zones`: CAD/m3 except `chemin_constr_tertiaire_dol_ha` and
  `autre*_fixe_dol_ha`, which are CAD/ha. Costs are industrial-survey averages
  by tarification zone (manual §4.5.5), not audited actuals. No date field.
- `tbl_Liste_Traitements`: 40 `DolHa`, 19 `Dol1000Plants`, 1 `Dol1000Micro`;
  34 harvest treatments have no rate because harvest cost is equation-based.
  `Valeur_Tot` includes `Taux_EXE + Autre_cout` (e.g. P110: 491 + 352 = 843).
- `tbl_Equations_Recolte` / `tbl_Profil_Rentabilite_EqGraph`: harvest +
  skidding + bucking cost is a logarithmic function of m3/tige, cutting mode
  (CT, CP+, CP−) and stand type (tolerant hardwood or not). It is not a fixed
  zone rate.
- Revenue placeholder: `tbl_essences_revenus_EssDhp` has 912 `courant` and 912
  `tendance` rows, 12 species, DBH 10–118; every `SCIAGE` and `PATE` value is
  `0.0100`. It cannot support a revenue estimate.
- `Conversion-Redevances.xlsm` is the non-Access source of the current rates:
  sheet `Données brutes 3eme trim 2025` has raw VMBSP by species/quality/zone;
  `Correspondance` has the ABCD → PETRO weights; `Matrice F1-2-3-4` and
  `Export` match `tbl_redevances_F1234`.

## 3. QC cost/revenue sample

The sample is in `data/derived/national/harvest_basis/meris_qc_sample/`.
It is one zone (151) plus raw context rows for zones 151/192/888.

### 3.1 Actual parameter rows (17 ledger rows)

`forest_cost_revenue_sample.csv` — all 17 rows pass
`workflows.national.harvest.cost_revenue.load_ledger`.

| Component | Value | Source / derivation |
|---|---:|---|
| `stumpage_base` BOJ DER | 18.6386 | `tbl_redevances_F1234`, 2025-09-19 |
| `stumpage_base` BOJ F1 | 8.2792 | same |
| `stumpage_base` BOJ PATE | 0.2500 | same |
| `stumpage_base` SAB F3 | 7.4400 | same |
| `stumpage_base` EPB PATE | 0.2500 | same |
| `stumpage_base` BOJ F1 2023 | 20.0000 | `tbl_Zones_RedevBenefRentSal_F1234`, `Rdv_F1`, `CAD_real_2023`, `model_derived:standing` |
| `haul` BOJ sawlog | 17.10 | 15.20 mill transport + 0.90 fardier + 1.00 unloading |
| `road` BOJ sawlog | 0.90 | 0.10 maintenance + 0.80 primary/secondary construction |
| `overhead` BOJ sawlog | 6.10 | 5.40 operator administration + 0.70 scaling |
| `harvest` BOJ sawlog | 2.80 | loading only; **partial**; confidence low |
| `silviculture` BPR | 1,026.00 CAD/ha | `tbl_Liste_Traitements`, `DolHa`, provincial rate |

All stumpage rows are mirrored (`crown +1`, `forest_sector −1`, `mirror=1`).
The sample deliberately has no `delivered_log_revenue`: revenue is missing, so
net value is not estimated. `coverage=sample`; the denominator is 2,674
zone-species rows for rates, 191 zones for costs, 94 treatments for
silviculture.

### 3.2 Shipped demo block (7 ledger rows)

`forest_cost_revenue_demo.csv` shows how the MERIS simulation output maps, not
a Quebec stand. Source: `Simulation - 1`, zone 192, `2-Courant`, `1-Base`,
`CPRS`; `M3ha_Tot_Net=158.08856`.

| Component | CAD/m3 | Derivation |
|---|---:|---|
| `delivered_log_revenue` | 178.534659 | `28,224.2871 / 158.08856` |
| `stumpage_total` | 1.755121 | `277.4645 / 158.08856` |
| `harvest` | 31.968926 | `(4,790.5029 + 263.4186) / 158.08856` |
| `road` | 10.353922 | `(131.7093 + 1,472.2 + 32.9273) / 158.08856` |
| `haul` | 43.621321 | `(4,501.1729 + 2,131.4404 + 98.7820 + 164.6366) / 158.08856` |
| `overhead` | 4.373964 | `(576.2281 + 115.2456) / 158.08856` |
| **Residual** | **86.461404** | matches `Benefice_Net_m3_net=86.4614` |

The demo is flagged `demo_simulation=1; not_a_transaction=1`. It should not be
used for policy, calibration, or a provincial average.

### 3.3 Financial vs economic mode

- **Financial mode** (`Rentabilité financière`): revenue at product value minus
  supply costs, transfers to the State (`redevances`) and transport to market;
  the manual’s Annexe A formula is `Bénéfice = Revenus − CA − CT − CM`
  (supply, transfer, market transport). `tbl_Rentabilite` carries the detailed
  financial/economic scenario rows.
- **Economic mode** (`Rentabilité économique`): the same physical scenario with
  the `Rdv/Bnf/Rsl/Aut1/Tot` matrix in $2023; it includes factor incomes and the
  CPRS adjustment and is not a cash flow. `tbl_770rq_*` is the economic result
  set.
- The current administered rates (`tbl_redevances_F1234`) are 2025 nominal; the
  economic matrix is $2023. They must not be mixed in one total.

## 4. Ledger mapping/gaps

`field_mapping.csv` is the full 49-row crosswalk; `gaps.csv` is the 14-row gap
register. The mapping follows the ledger’s 16-field schema and sign matrix:

| MERIS field | Ledger component | Unit | `value_basis` | Confidence |
|---|---|---|---|---|
| `Redev_Der/F1..F4/Pate` | `stumpage_base` (Crown + mirror) | CAD/m3 log | `administered_rate:standing` | medium |
| `Rdv_*` ($2023) | `stumpage_base` (economic mode) | CAD/m3 log | `model_derived:standing` | low |
| `chemin_entretien`, `chemin_constr_prim_sec` | `road` | CAD/m3 log | `published_average:roadside` | medium |
| `transport_usine_*`, `transport_fardier`, `dechargement` | `haul` | CAD/m3 log | `published_average:mill_gate` | medium |
| `chargement` | `harvest` (loading only) | CAD/m3 log | `published_average:roadside` | low |
| `mesurage`, `admin` | `overhead` | CAD/m3 log | `published_average:roadside` | medium |
| `tbl_Liste_Traitements.Valeur_Tot` (DolHa) | `silviculture` | CAD/ha | `appraisal_allowance:roadside` | medium |
| `tbl_770.Valeurs_Produits` | `delivered_log_revenue` | CAD/m3 log | `model_derived:mill_gate` | low, demo only |
| `tbl_770.Redevances` | `stumpage_total` | CAD/m3 log | `model_derived:standing` | low, demo only |

### Explicit gaps

1. **No observed revenue (G01).** `tbl_essences_revenus_EssDhp` is a
   placeholder (all 0.0100). `tbl_Zones_RedevBenefRentSal_F1234` is an economic
   value decomposition; `tbl_770` is a shipped demo. No `delivered_log_revenue`
   can be booked from MERIS actuals.
2. **No quality dimension in the ledger (G02).** MERIS has DER/F1/F2/F3/F4/
   PATE/AUTRE per species; the ledger’s product enum has one `sawlog`. The
   9-field key would collide. The sample uses only F1 and one F3 (different
   species); F2/F4/AUTRE are not ingested. Add a `quality` field or product
   variants before full extraction.
3. **Harvest is equation-based (G03).** `tbl_Equations_Recolte` and
   `tbl_Profil_Rentabilite_EqGraph` require m3/tige, cutting mode and stand
   type. The sample books only loading (2.80) and marks it partial.
4. **Silviculture density (G04).** 19 rates are per 1,000 plants and 1 per
   1,000 microsites; plants/ha and microsites/ha are not in the rate table.
5. **Tertiary road unit (G05).** 920–1,833 CAD/ha; the ledger allows
   `CAD_per_ha` only for silviculture.
6. **No UA crosswalk (G06).** 191 tarification zones; the DB only has
   `Zone_Redev` and `Secteur`. The sample uses `QC-ZONE_151`, not `QC-UA_*`.
7. **No actual volume (G07).** `tbl_Volume_Prescription` is empty; there is no
   harvest-declaration table. MERIS cannot supply coverage or volume weighting.
8. **Year/currency (G08).** Current rates 2025 nominal; economic matrix $2023.
9. **Factor incomes (G09).** `Bnf`, `Rsl`, `Aut1` are not ledger components;
   mapping them to revenue or cost would double count value added.
10. **Cost year (G10).** `tbl_couts_zones` has no date; release year is 2025.
11. **Schema dump (G11).** `mdb-schema` fails on ACCDB; types are inferred.
12. **Revenue placeholder (G12).** Do not use `tbl_essences_revenus_EssDhp`.
13. **Admin semantics (G13).** `admin_dol_m3` is operator administration, not
    Crown `admin_fee`.
14. **Sample coverage (G14).** One zone; no coverage share.

## 5. Tests/commits/status

- Ledger parse: both `forest_cost_revenue_sample.csv` (17 rows) and
  `forest_cost_revenue_demo.csv` (7 rows) load through
  `workflows.national.harvest.cost_revenue.load_ledger`.
- Test suite: `python -m pytest tests/test_forest_cost_revenue.py -q` →
  **17 passed**.
- Data commit: `a070a4f04a6db072ca4427a2b0a60e51ed30677f` (submodule `data`, branch
  `feature/osecan-zonal-bc-supply`).
- Parent commit: this report's commit; inspect with `git log -1 -- planning/meris-qc-extraction.md`.
- Public OSeCAN: **not touched**. The only remotes are
  `UBC-FRESH/clews-c2070-fresh` and `UBC-FRESH/clews-c2070-fresh-data`; no
  OSeCAN remote is configured.
- Working-tree state is recorded in the final orchestrator report. Unrelated
  untracked work from other agents (BC BCTS files, docs PDF, etc.) was not
  staged.

## 6. Next (full extraction or report path)

**Recommended path: report, not full extraction yet.** The MERIS tables are
readable, but three missing dimensions block a policy-grade ledger: observed
revenue, quality class, and actual volume/UA crosswalk. A full 191-zone export
would multiply a rate table that cannot yet be volume-weighted or joined to the
harvest allocation.

If the team wants the full parameter export now, the smallest next step is:

1. Export all 191 zones × 14 species groups from `tbl_redevances_F1234` and
   `tbl_couts_zones` (2,674 + 191 rows) as staging CSVs, not ledger rows.
2. Add a `quality` field (or `sawlog_f1..f4` product variants) to the ledger
   schema and validator; decide whether `AUTRE` is a product or a residual.
3. Obtain BMMB auction/delivered sale results or mill settlements for
   `delivered_log_revenue`; do not use `tbl_essences_revenus_EssDhp`.
4. Obtain the tarification-zone → UA/region crosswalk and a volume/tige
   distribution for the harvest cost equation.
5. Then normalize to `data/derived/national/forest_cost_revenue/` and run the
   full ledger checks; keep `coverage=sample` until volume coverage exists.

**Fallback path:** if only a narrative is needed, use this report and
`meris_qc_sample/README.md`. The sample’s 17 actual parameter rows are enough
to show the field semantics and the sign/mirror convention; the demo block is
enough to show the full arithmetic, provided it remains labelled demo.

## 7. Full extraction (2026-11-20)

The full parameter export is now published in
`data/derived/national/harvest_basis/meris_qc_full/`; see
`QC_MERIS_DECK_REPORT.md` there for the field-level report. It does **not**
change the sample’s warning that MERIS is not a register of actual harvest.
The important correction to the sample review is that the revenue placeholder
is the base table `tbl_essences_revenus_EssDhp`; the three shipped profile
tables `_Profil_2/3/4` contain actual species × DBH model values for 12 SEPM
species (1,824 rows each, two series, 10–160 cm DBH). The three profile tables
are byte-identical in this release.

The full deck contains:

| Deck | Grain | Rows | Key finding |
|---|---|---:|---|
| `meris_price_deck.csv` | species × product × series | 630 (45 × 7 × 2) | 28 hardwood species with a positive current value; `PATE` is the 0.0100 placeholder; the 12 SEPM species are zero here and covered by the DBH curves |
| `meris_price_deck_wide.csv` | species | 45 | same values in source layout |
| `meris_revenue_curves.csv` | profile × species × type × DBH | 5,472 (1,824 × 3) | 12 SEPM species; `SCIAGE=PATE` in every row; profiles 2/3/4 identical |
| `meris_zone_values.csv` | zone × group × product | 18,718 (2,674 × 7) | `Tot=Rdv+Bnf+Rsl+Aut1+Aut2` holds exactly; $2023 |
| `meris_zone_values_wide.csv` | zone × group | 2,674 | source layout |
| `meris_royalties.csv` | zone × group | 2,674 | 2025-09-19 administered rates; `RdvVariable=0`; group taxonomy differs from the zone-value table |
| `meris_costs.csv` | zone | 191 | supply-cost parameters; CAD/m3 except tertiary/other fixed CAD/ha; no date |
| `meris_silviculture.csv` | treatment | 94 | 40 `DolHa` usable; 19 per 1,000 plants and 1 per 1,000 microsites need density |

The ledger block `forest_cost_revenue_qc_model.csv` has 740 rows: 127
`delivered_log_revenue` rows (current series, positive values only), 191
`road`, 191 `overhead`, 191 `harvest` (loading only, partial), and 40
`silviculture` rows. Every row is `prov_qc_meris`, `forest_sector`,
`model_input=1`, `not_observed=1`, with `volume_m3=1` as a rate denominator.
The block is **not a net-value estimate**: actual volume, haul, full harvest,
stumpage join, and a quality dimension are still missing.

## References

- `data/derived/national/harvest_basis/meris_qc_full/QC_MERIS_DECK_REPORT.md`
- `data/derived/national/harvest_basis/meris_qc_full/manifest.json`
- `data/derived/national/harvest_basis/meris_qc_sample/README.md`
- `data/derived/national/harvest_basis/meris_qc_sample/provenance.json`
- `data/derived/national/harvest_basis/meris_qc_sample/field_mapping.csv`
- `data/derived/national/harvest_basis/meris_qc_sample/gaps.csv`
- `planning/forest-cost-revenue-ledger.md` §3–§4 (schema, QC ingestion rules)
- `workflows/national/harvest/cost_revenue.py` (validator)
