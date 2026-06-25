# Roadmap

## Phase 1: Foundation and Model Development

Target window: May 2026 to March 2027.

Primary goal: build the national forest data foundation required for
forest-biomass supply modelling before model implementation work depends on it.

### P1.1 Repository Bootstrap

- [x] Add parent-repo operating notes and data-boundary guidance.
- [x] Add DataLad/submodule runbook for the future `data` repository.
- [x] Add Tyler onboarding brief and initial work package.
- [x] Add initial source inventory and curated dataset registry templates.
- [x] Create or confirm `UBC-FRESH/clews-c2070-fresh-data`.
- [x] Initialize the data repository as a DataLad dataset with git-annex.
- [x] Configure and smoke-test the `arbutus-s3` special remote.
- [x] Link the data repository as the parent repo's `data` submodule.

### P1.2 National Source Discovery

- Governing issue: `#1`

- [ ] Review candidate source families in `metadata/source-inventory.csv`.
- [ ] For each source, record publisher, access URL, license, spatial coverage,
  temporal coverage, update cadence, data format, and acquisition status.
- [ ] Promote accepted sources into `metadata/required-datasets.yaml`.
- [ ] Open issues for sources needing access requests, license review, or
  manual acquisition.

### P1.3 Data Archive and Provenance

- [x] Define the `data` repository directory contract.
- [ ] Archive raw source payloads with checksums and acquisition manifests.
- [ ] Track preprocessing scripts or notebooks in the parent repo when they are
  source code, and write outputs to the DataLad dataset.
- [x] Validate a fresh clone can retrieve at least one small annex-backed
  artifact from Arbutus S3.

### P1.4 First Derived Products

- [ ] Create a national administrative/provincial boundary baseline layer.
- [ ] Create a source crosswalk for inventory, disturbance, transport,
  industrial facility, protected-area, and carbon/climate layers.
- [ ] Produce the first modelling-readiness assessment for WS3/FEMIC expansion.

## Detailed Next Steps Notes

1. Tyler should work through `planning/source-triage-queue.md`, beginning with
   `scanfi_v2`, `nfi_standard_reports`, `canlad`, `nrn`, and
   `biomass_inventory_business`.
2. Tyler's first pass should update `metadata/source-inventory.csv`, not download
   large payloads directly into the parent repo.
3. Once candidate sources are reviewed, promote only accepted datasets into
   `metadata/required-datasets.yaml`.
4. When the first real dataset is accepted, acquire it under `data/raw/`,
   record the manifest under `data/manifests/`, copy annexed payloads to
   `arbutus-s3`, and update the parent repo's curated registry.
