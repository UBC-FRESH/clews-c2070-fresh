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
