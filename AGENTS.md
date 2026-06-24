# Codex Agent Operating Notes

This repository is the UBC FRESH workspace for the forestry and biomass supply
component of CLEWs-C2070.

## Data Boundary

- Keep code, documentation, planning notes, and lightweight metadata in this
  parent repository.
- Keep large raw, intermediate, and archived data payloads in the `data`
  submodule, backed by a separate DataLad/git-annex repository.
- Do not commit downloaded GIS layers, rasters, database dumps, model outputs,
  or local cache directories to the parent repository.
- Do not commit credentials, local Arbutus/S3 profiles, bucket secrets, personal
  paths, or machine-specific configuration.

## Expected Data Pattern

The intended heavy-data repository is:

- GitHub repository: `UBC-FRESH/clews-c2070-fresh-data`
- Parent-repo path: `data`
- DataLad special remote name: `arbutus-s3`
- Heavy payload storage: DRAC Arbutus S3 object storage

Use [docs/data-repository-runbook.md](docs/data-repository-runbook.md) as the
operational guide. The bootstrap and publish side is stateful infrastructure
work; read the runbook before issuing raw `datalad`, `git annex`, or S3
commands.

## Planning Surfaces

- [ROADMAP.md](ROADMAP.md): phase plan and active project tasks.
- [CHANGE_LOG.md](CHANGE_LOG.md): concise audit trail of repo changes.
- [planning/tyler-onboarding.md](planning/tyler-onboarding.md): intern meeting
  brief and first work package.
- [metadata/source-inventory.csv](metadata/source-inventory.csv): working source
  discovery table.
- [metadata/required-datasets.yaml](metadata/required-datasets.yaml): curated
  dataset registry once candidates are accepted.

Before making non-trivial changes, update the planning surface that governs the
work. Keep new path names lowercase unless they mirror an upstream source name.
