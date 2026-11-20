# Forest Ledger — v1.0.0 static publication

Static-only public copy of the CLEWS-C2070 FRESH forestry/biomass scenario
explorer. Release label `clews-c2070-fresh-v1`, tag `v1.0.0`, commit
`b69e2588a089b8532bbc74afe77baaf069e83401` (2026-11-20).

> **Static demonstration — no live solver, no backend. Not C2070 official.
> Not energy-policy advice.** This is a FRESH-side provisional screening
> explorer, not a legal AAC/THLB determination, forecast, investment case, or
> operational recommendation.

## Run locally

```bash
cd ui_publications/v1.0.0
python3 -m http.server 8000
```

Open <http://127.0.0.1:8000/>. The page must display **“Static mode · no
backend”**; create, queue, and the draft `policy_test_v1` selector are
disabled. All precomputed views and CSV exports remain available. No CDN or
network access is required.

## Bundle

| Item | Value |
|---|---:|
| Scenarios / families / Pareto-eligible | 53 / 18 / 1 |
| Dispatch outputs | 53 |
| Data-manifest files | 63 (8,732,120 bytes) |
| Data bundle SHA-256 | `064c1e82af2f3e4eb02fa824a99f8002b80e7941223b325894c91238805b4738` |
| Data manifest SHA-256 | `2a924d6cc7dbbf9be36e31aae4d9924b397a93ec20e2d0f5127b54e27dc5e102` |

Canonical `fleet_capacity__screening_119m_fleet`: objective
**1,445,655.44 M$**, cumulative 2021–2070 emissions **424.710127 Mt CO₂**,
2050 emissions **0.0 Mt CO₂**. `policy_test_v1` is a **draft interface test**
(1,458,305.16 M$; 1,024.824 Mt CO₂ cumulative; 0.490203 Mt CO₂ in 2050;
`policy_grade=false`), not policy and not a release artifact.

## Files

- `index.html`, `app.js`, `api.js`, `styles.css` — the UI.
- `publication-mode.js` — blocks all same-origin `/api` requests so the copy
  stays static even if hosted behind an API-capable origin.
- `vendor/` — ECharts 5.5.1 (Apache-2.0), MapLibre GL JS 4.7.1 (BSD-3-Clause),
  Fraunces and IBM Plex fonts (SIL OFL 1.1), with upstream licence texts.
- `public/data/` — 53 scenario outputs, 18 families, spatial layers, inputs,
  and the data manifest.
- `manifest.json` — payload hashes and byte counts.
- `PUBLICATION.json` — machine-readable release record and validation result.
- `PUBLICATION.md` — full release, provenance, licence, caveat, and QA record.

Do not add a live runner, credentials, solver output, or mutable session data
to this directory. Host deployment and Cloudflare Access are separate
operations. See `PUBLICATION.md` for the full boundary and caveat statement.
