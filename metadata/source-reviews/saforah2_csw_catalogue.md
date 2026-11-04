# Source Review: SAForaH2 Portal — Federated Forestry CSW Catalogue

Review date: 2026-08-09.
Author: UBC FRESH / CLEWs-C2070 forestry data maintainers.
Status: candidate (discovery-only layer; not a primary input asset).
Governance: this row is a *discovery feed*, not a dataset. Source review
covers both the harvest endpoint and the manifest-emission discipline.

## Cross-references

- Companion inventory row: `metadata/source-inventory.csv` row
  `saforah2_csw_catalogue`
- Companion harvest script: `scripts/discovery/saforah2_csw_harvest.py`
- Companion manifest output directory: `metadata/discovery-snapshots/`
- Companion Beaudoin kNN source review:
  `metadata/source-reviews/beaudoin_knn_archives.md` — the Beaudoin kNN
  archive family surfaces in CSW keyword `kNN` (~56 hits per the
  2026-08-09 probe), which is the discovery path by which this review found
  that candidate.

## 1. Document identity

| Field | Value |
|-------|-------|
| Service | SAFORAH2 Portal — NRCan/Canadian Forest Service federated forestry metadata catalogue |
| OGC service profile | OGC Catalogue Services for the Web (CSW) 2.0.2 |
| Cataloguing backend | GeoNetwork 3.x (probe-derived; the GetCapabilities doc carries the GeoNetwork 3 element set; fingerprint consistent with the 3.x line circa 2024–2026) |
| Public UX front-end | `https://saforah2.nfis.org/CSWClient/pages/search.html?lang=en` (search-only with no browsable index) |
| Programmatic backend (the live one) | `https://saforah2.nfis.org/geonetwork-main/srv/eng/csw` |
| Publisher | Natural Resources Canada — Canadian Forest Service (NRCan/CFS) |
| License shape | NRCan/CFS metadata; per-record access constraints vary — most records are OGL Canada; some are restricted or DUAs |

## 2. Endpoint discovery

Geoservers, in practice, expose CSW endpoints under a small set of conventions.
The SAForaH2 family is repackaged under `saforah2.nfis.org` and includes
a custom UX front-end at `/CSWClient/pages/search.html`. Probe-derived
neighbour-path results as of 2026-08-09:

| Candidate URL | Status |
|---------------|--------|
| `/csw` | dead |
| `/CSW` | dead |
| `/CSWClient/csw` | dead |
| `/CSWClient/services/csw` | dead |
| `/catalogue/csw` | dead |
| `/services/csw` | dead |
| `/geonetwork/srv/en/csw` | dead |
| **`/geonetwork-main/srv/eng/csw`** | **live** (CSW 2.0.2; GetCapabilities probe returned a valid capabilities doc with the GeoNetwork 3.x fingerprint) |

The `/CSWClient/pages/search.html` UX front-end is search-only with no
browsable index — discovery feeds must be programmatic against the backend.

## 3. CSW capabilities (probe-derived from GetCapabilities)

Operations confirmed via 2026-08-09 probe:

- `GetCapabilities` (HTTP GET, returns `csw:Capabilities` XML)
- `GetRecords` (HTTP GET and HTTP POST; queryable via CQL_TEXT or
  XML-encoded filter expressions)
- `GetRecordById` (HTTP GET, single record fetch)
- `DescribeRecord` (HTTP GET)

**Queryables (CQL_TEXT queryable fields):** `AnyText`, `Title`, `Subject`,
`OrganisationName`, `KeywordType`, `TopicCategory`, plus the full ISO 19115
core set (`csw:Record`'s `BBOX`, `Identifier`, `Modified`, `Type`,
`Subject`, `Title`, `Abstract`). Spatial operators supported: `BBOX`,
`Intersects`, `Within`, `Contains`. Output formats: `application/xml`
(ISO 19139), `application/json` (subset, GeoNetwork extension).

Result paging via `startPosition` and `maxRecords` query parameters;
reasonable defaults for harvesting are `resulttype=hits` for hit-count
probes and `resulttype=results` for actual record emission.

## 4. Hit-count probes

Probes via `GetRecords&resulttype=hits&elementsetname=brief&constraint=
csw:AnyText like '%25<keyword>%25'&constraintLanguage=CQL_TEXT&constraint_language_version=1.1.0`
executed 2026-08-09 (CQL_TEXT `AnyText` LIKE is case-insensitive when
`caseSensitive=false`, which is the GeoNetwork default):

| Query keyword | Hit count |
|---------------|-----------|
| `biomass` | 80 |
| `species composition` | 157 |
| `species` | (TBD — proxy via `species composition`) |
| `kNN` | 56 |
| `yield` | (TBD — leave to first script run) |
| `disturbance` | (TBD) |
| `inventory` | (TBD) |
| `land cover` | (TBD) |
| `fire` | (TBD) |

These counts are stored as historical baselines for `scripts/discovery/saforah2_csw_harvest.py`
output to compare against future runs and watch for catalogue drift.

## 5. Readiness-gate impact

The CSW catalogue is a **discovery accelerator**, not a primary input.
Expected downstream effects of quarterly harvesting:

- New candidate datasets will surface against `metadata/source-inventory.csv`
  via the harvest manifests in `metadata/discovery-snapshots/`.
- A subset of hits will likely satisfy `species_composition`,
  `residue_yield_policy`, and `national_scaling` data-side constraints
  that are not currently covered by the candidate set in
  `source-inventory.csv` (e.g. provincial landcover classification
  updates, jurisdiction-specific disturbance composites). Each new hit
  triggers a follow-up source-review and inventory-row registration.

Gate status does not change. The discovery layer is upstream of, not
itself an answer to, the readiness gates.

## 6. Acquisition strategy

The harvest is run quarterly against the live CSW endpoint using a
stdlib-only Python script:

1. **Run the harvest script** — invoke
   `./.venv/bin/python scripts/discovery/saforah2_csw_harvest.py` (or
   with an explicit `YYYY-MM-DD` argument).
2. **Output is appended** — the script writes a single manifest CSV to
   `metadata/discovery-snapshots/saforah2_<date>.csv` containing
   per-keyword records with columns
   `harvest_date,query_keyword,identifier,title,organisation,modified,abstract_snippet`.
   One row per (keyword × record) hit, including the originating keyword.
3. **Cross-reference** — the harvest manifest is then cross-referenced
   against `metadata/source-inventory.csv` to flag new candidates and
   check for catalogue drift in candidates already registered.
4. **Per-record access** — the harvest exposes identifiers and titles
   only; per-record payload access is gated through each record's
   `access_constraints` XML element. Records marked restricted are
   surfaced in the manifest with `access_mode=restricted` so the
   team can plan DUAs separately.

The harvest is fully read-only against the CSW endpoint; it does not
catalogue-mutate and does not depend on any per-record credential.

## 7. References

- OGC Catalogue Services for the Web (CSW) 2.0.2 standard,
  OGC 07-006r1 / Corrigendum.
- OGC Testbed-15 Engineering Report: *Catalogue Services across DGGS,
  OGC API Features, and SensorThings* (publicly available at
  `https://docs.ogc.org/per/19-027r2.html`). The external doc is cited
  for the canonical CSW 2.0.2 profiling language patterns and the
  standard's permissive namespace handling; the SAForaH2 implementation
  matches the standard profile.
- GeoNetwork 3.x documentation: `https://geonetwork-opensource.org/`
  (covering the CQL_TEXT queryable vocabulary and the GetCapabilities
  response shape observed 2026-08-09).
- NRCan/SAForaH2 public UX front-end (search-only, no API):
  `https://saforah2.nfis.org/CSWClient/pages/search.html?lang=en`.
