# Data Repository Runbook

This runbook describes the intended `data` submodule pattern for
CLEWs-C2070 FRESH.

The parent repository holds code, documentation, and lightweight metadata. The
`data` path should be a git submodule pointing to a separate DataLad repository:

- parent path: `data`
- GitHub repository: `UBC-FRESH/clews-c2070-fresh-data`
- DataLad special remote: `arbutus-s3`
- heavy payload storage: DRAC Arbutus S3 object storage

## Safety Rules

- Keep all credentials outside the repository.
- Do not paste access keys into tracked files, issue comments, commit messages,
  notebooks, logs, or shell transcripts.
- Do not archive data with unclear license terms until the license field in the
  metadata registry has been reviewed.
- Prefer selective retrieval with `datalad get <path>` over materializing the
  full archive.

## Maintainer Bootstrap

Run this only after the GitHub repository and Arbutus S3 bucket authority are
confirmed.

```bash
datalad create -c text2git clews-c2070-fresh-data
cd clews-c2070-fresh-data
mkdir -p raw interim processed metadata manifests
```

Suggested data repository layout:

- `raw/`: immutable source payloads exactly as acquired.
- `interim/`: normalized or extracted source products before modelling logic.
- `processed/`: modelling-ready products.
- `metadata/`: dataset-level metadata, checksums, and source manifests.
- `manifests/`: acquisition logs and generated file inventories.

After local credentials are loaded into the shell environment, initialize the
special remote. Replace placeholders with the approved bucket name.

```bash
git annex initremote arbutus-s3 \
  type=S3 \
  encryption=none \
  bucket=<bucket-name> \
  public=yes \
  publicurl=https://object-arbutus.cloud.computecanada.ca/<bucket-name> \
  host=object-arbutus.cloud.computecanada.ca \
  protocol=https \
  port=80 \
  requeststyle=path \
  autoenable=true \
  chunk=1GiB \
  storageclass=STANDARD
```

Publish metadata to GitHub and annex objects to Arbutus:

```bash
git annex copy --to arbutus-s3 --all
git config remote.origin.datalad-publish-depends arbutus-s3
datalad create-sibling-github -d . \
  --github-organization UBC-FRESH \
  --name origin \
  --publish-depends arbutus-s3 \
  --existing reconfigure \
  clews-c2070-fresh-data
git push origin main
git push origin git-annex
```

## Link the Parent Repository

From the parent repository root:

```bash
git submodule add https://github.com/UBC-FRESH/clews-c2070-fresh-data.git data
git commit -m "Add DataLad data submodule"
```

## Collaborator Bootstrap

From the parent repository root:

```bash
git submodule update --init --recursive
git annex version
datalad --version
git -C data annex enableremote arbutus-s3
datalad get data/metadata
```

Retrieve payloads only as needed:

```bash
datalad get data/raw/<dataset-id>
datalad get data/processed/<product-id>
```

## Dataset Intake Checklist

For each candidate dataset:

1. Add or update the row in `metadata/source-inventory.csv`.
2. Confirm source publisher, access path, license, coverage, temporal range,
   update cadence, and limitations.
3. Decide whether the source should be mirrored in DataLad or regenerated from
   a stable public API/download endpoint.
4. Acquire raw data into `data/raw/<dataset-id>/`.
5. Record acquisition date, command/manual steps, source URL, and contact notes.
6. Compute checksums for raw payloads.
7. Promote accepted datasets into `metadata/required-datasets.yaml`.

## Smoke Test

After the first small artifact is annexed and published:

```bash
cd ..
datalad clone https://github.com/UBC-FRESH/clews-c2070-fresh-data.git data-smoke
cd data-smoke
git annex enableremote arbutus-s3
datalad get metadata
git annex whereis metadata
```
