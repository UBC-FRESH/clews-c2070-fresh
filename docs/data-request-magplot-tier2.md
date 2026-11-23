# Optional note — restricted MAGPlot and NFI ground-plot requests

| Field | Value |
|---|---|
| Status | **Optional; not submitted; not a dependency, gate, or milestone** |
| Prepared | 2026-11-23 |
| Scope | Possible evidence upgrade only |
| Companion handoff | `docs/handoff-national-facility-chain-v3.md` |

This note is informational. The national facility chain is complete without any
restricted MAGPlot Tier 2/3 record, NFI ground-plot record, data-use agreement
(DUA), request, or approval. No request has been sent and no restricted data are
held.

## 1. Position

The v3 model uses open data. Non-open-tree-list jurisdictions are represented by
the E2 open-data imputation path:

- open SCANFI v2 province species composition and NFI standard-report summaries;
- 36 imputed tree lists per province (324 stems total; 1,000 stems/ha
  expansion) with deterministic donor DBH/height draws from the measured
  BC/QC/NB/YT lists;
- labelled BC Kozak or QC Schneider donor tapers (11 of 13 jurisdictions use a
  donor taper);
- CFS branch/foliage allometry with documented species/group proxies;
- declared evidence tiers so no imputed value is presented as observed.

All 13 jurisdictions use either E1 measured or E2 imputed solid/pulp/other
product shares and total harvest residue; no donor provincial product
coefficient is applied. Within-class SAW/PANEL and pellet/bio splits, routing,
recovery fraction, and mill conversion ratios remain labelled donor
assumptions. This path is sufficient to complete the model and does not require
external approval. Restricted MAGPlot or NFI records would only upgrade the
evidence tier for some jurisdictions; they would not unblock model completion.

## 2. What a future request could add

If the project later chooses to pursue restricted records, the only expected
benefit is narrower uncertainty:

- province-specific measured product shares and harvest-residue coefficients for
  AB, SK, MB, and ON;
- NFI ground plots with site index and age for validation and covariate checks;
- a direct check on how much the national result depends on donor assumptions.

These are improvements, not prerequisites. Any future request would follow the
custodian's access terms and the repository data boundary. Raw records and exact
coordinates would stay outside the parent repository; no restricted record would
be redistributed.

## 3. Non-claims

This note does not claim that:

- access would be granted or that any restricted tier contains the needed fields;
- a DUA, request, or approval is required to complete the model;
- restricted data are needed to produce the national facility chain;
- the open-data imputation is observed data or policy-grade evidence;
- any request, if made, would change the official OSeCAN tree.

## 4. References

- `docs/handoff-national-facility-chain-v3.md` — v3 chain handoff and evidence tiers.
- `docs/model-card-national-facility-chain.md` — frozen chain method card.
- `metadata/source-reviews/magplot.md` — MAGPlot source review and access tiers.
- `metadata/source-reviews/nfi_ground_plot_data.md` — NFI ground-plot access
  posture.
- `metadata/license-determinations/nfi-access.yaml` — licence/DUA registry.
- `data/raw/magplot/MAGPlot_CONTENTS.md` — open-package table inventory and
  coverage.
- `data/raw/magplot/wfs/ACQUISITION.md` — WFS supplement coverage.
