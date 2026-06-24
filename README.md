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

## First Bootstrap Commands

After the `data` submodule exists:

```bash
git submodule update --init --recursive
git -C data annex enableremote arbutus-s3
datalad get -r data/raw
```

Use selective `datalad get` calls for large layers. Avoid materializing the
entire archive unless a workflow explicitly requires it.
