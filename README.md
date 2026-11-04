# CLEWs-C2070 FRESH

UBC FRESH workspace for the forestry and forest-biomass feedstock component of
CLEWs-C2070.

The near-term objective is to assemble a reproducible, well-documented national
data foundation for a future forest estate and biomass supply modelling system:

- identify candidate input data layers;
- resolve authoritative public sources and licenses;
- archive raw source artifacts in a DataLad-backed `data` submodule;
- record metadata, checksums, and provenance;
- preprocess raw layers into secondary and tertiary modelling products; and
- prepare national-scale inputs for WS3, FEMIC/FHOPS-style preprocessing, carbon
  accounting, and CLEWs energy-system integration.

## Repository Layout

- `docs/`: operator runbooks and project notes.
- `metadata/`: machine-readable dataset inventories and source registries.
- `planning/`: onboarding notes, meeting briefs, and work packages.
- `data/`: planned git submodule pointing at `UBC-FRESH/clews-c2070-fresh-data`
  once that DataLad repository is created and linked.

Heavy geospatial layers and model artifacts belong in `data`, not in this
parent repository.

## Start Here

For Tyler's first work package, use:

- [planning/tyler-onboarding.md](planning/tyler-onboarding.md)
- [metadata/source-inventory.csv](metadata/source-inventory.csv)
- [metadata/required-datasets.yaml](metadata/required-datasets.yaml)

For the data repository and Arbutus/DataLad pattern, use:

- [docs/data-repository-runbook.md](docs/data-repository-runbook.md)
- [docs/developer-environment-bootstrap.md](docs/developer-environment-bootstrap.md)

## Developer Bootstrap

Use a repo-root local virtual environment. DataLad and FreshForge should come
from `.venv`, while `git-annex` remains an OS-level prerequisite:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[workflow]"
.venv/bin/python -m freshforge --version
.venv/bin/datalad --version
git annex version
```

For the full ws3 smoke-test lane, add the editable local `../ws3` checkout:

```bash
python -m pip install -r requirements-ws3-smoke.txt
```

Use FreshForge to inspect and run the collaborator materialization workflow:

```bash
.venv/bin/python -m freshforge validate examples/freshforge/data_materialization_workflow.yaml
.venv/bin/python -m freshforge plan examples/freshforge/data_materialization_workflow.yaml
.venv/bin/python -m freshforge run examples/freshforge/data_materialization_workflow.yaml \
  --workdir runtime/freshforge \
  --namespace data-bootstrap
export CLEWS_EXTERNAL_DATA_ROOT=$PWD/data
```

The default workflow materializes only `data/metadata` and `data/manifests`.
Use selective `datalad get` calls for large layers only when a workflow
explicitly requires them; avoid materializing `data/raw` by default.

## v0.1.0a1 Conceptual Alpha Release Status

**Release**: `v0.1.0a1` (Conceptual Alpha) — 2026-08-09

### What Works (Diagnostic Capabilities)
- BC mini-network graph construction and LP solve with 4 diagnostic supply lanes (synthetic, ws3-smoke, SCANFI-diagnostic, SCANFI+Gauthier-diagnostic)
- `fresh_fibre` Python module: schema validation, unit registry, contract checks, ws3 export bridge
- Tiny real-sampled SCANFI diagnostic fixture (6 cells, 5 authorized SCANFI v2 2025 rasters)
- CanLaBS v2 raw dNBR provenance extracted for all 6 windows (10 new columns in diagnostic inventory)
- CanLaD disturbance layers acquired and archived (CanLaD, NFDB fire polygons/points, NBAC)
- NFI standard reports and NFDP harvest tables accepted and promoted to `required-datasets.yaml`
- 10 jurisdiction-specific forest road sources added as candidates
- FreshForge/DataLad materialization workflow for public-safe metadata/manifests

### What Is Explicitly Diagnostic / Quarantined (NOT Production Evidence)
- **All 6 SCANFI heuristic windows**: `SOFTWARE_FIXTURE_NOT_EVIDENCE` — heuristic 100,000-ha squares, not statistically defensible sample of BC
- **Yield policy**: Gauthier method reproduction at 0.75 m³/ha placeholder only; no calibration, no species×grade disaggregation, no multi-rotation WS3
- **Residue yield**: No calibrated policy; `placeholder_residue_not_calibrated`
- **Cost surfaces**: NRN coarse baseline only; 10 forest road candidates not acquired
- **Climate predictors**: None acquired; ERA5 candidate only
- **Species composition**: SCANFI spsCC_* layers deferred (123 GB)
- **NFI ground plots**: Not accessible; standard reports summary validation only
- **National scaling**: BC mini only; no provincial inventories/yield curves
- **Facility layer**: Legacy 2013 database only; no current authoritative source
- **CanLaBS v2 provenance gate**: Local/server size mismatch must be resolved

### Production Readiness Gates
| Gate | Status | Required For |
|------|--------|--------------|
| `nfi_access` | blocked | national biomass calibration, composition/age validation |
| `climate_predictors` | blocked | Gauthier climate response, ws3 growth, disturbance-climate |
| `residue_yield_policy` | blocked | residue supply, biomass availability, cost allocation |
| `calibrated_cost_surfaces` | blocked | LP cost minimization, delivered cost screening, facility optimization |
| `species_composition` | blocked | SCANFI species, Gauthier yield curves, product-grade |
| `national_scaling` | blocked | national supply modelling, inter-provincial trade, carbon accounting |
| `facility_layer` | blocked | demand nodes, capacity constraints, commodity cascade |
| `disturbance_calibration` | in_progress | ws3 estate initialization, age class, disturbance intervals |

**7 blocked, 1 in_progress, 0 resolved**

See `metadata/readiness-gates.yaml` and `planning/phase-7-production-readiness-memo.md` for details.

### Verification
- All 56 tests pass (`pytest tests/ -q`)
- `git diff --check` clean (parent repo and data submodule)
- `python -m compileall fresh_fibre tests` — no errors
- Branch pushed to origin

**Governance**: No artifact, metric, or claim from the diagnostic lanes may be used to support BC-scale or national estimates, calibrations, policy/investment decisions, or inferential statements. The seven blocked gates define the mandatory work program for any future production release.
