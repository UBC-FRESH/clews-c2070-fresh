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

## Fresh Clone Bootstrap

Use a repository-root local virtual environment for Python, DataLad,
FreshForge, ws3 smoke runs, and test commands. Agent terminal sessions should
prefer explicit `.venv/bin/...` commands instead of probing the global shell
path and reporting missing executables that are expected to live in the project
environment.

Baseline setup from the repo root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-prototype.txt
python -m pip install -r requirements-ws3-smoke.txt
```

If the FreshForge/DataLad workflow files are present on the active branch,
install the workflow dependency set and run workflow commands from `.venv`:

```bash
python -m pip install -e ".[workflow]"
.venv/bin/python -m freshforge --version
.venv/bin/datalad --version
git annex version
```

`git-annex` is an OS-level prerequisite. DataLad should be installed through
the repo-local `.venv`.

## Development Workflow

This repository follows the UBC-FRESH phase/task/subtask workflow used across
FEMIC and FreshForge:

- One roadmap phase maps to one GitHub parent issue and one feature branch.
- One roadmap task maps to one child issue linked from the parent issue body.
- Subtasks normally stay as checklist items inside the child issue body.
- Use at most three issue levels: phase, task, implementation subtask.
- Keep `ROADMAP.md`, `CHANGE_LOG.md`, planning notes, issue bodies, issue
  comments, PR descriptions, and commits synchronized.
- Before making non-trivial implementation changes, update the governing
  roadmap/planning surface first.
- Before starting a task, confirm `gh auth status`, inspect the governing
  issue/PR state, and create or activate the appropriate child issue.
- Commit early and thematically, ideally at roadmap task or coherent subtask
  granularity.
- When a task status changes materially, post a GitHub progress comment on the
  active child issue and, when relevant, the parent phase issue.
- Close a child issue only after repo changes, docs/planning updates,
  checklist reconciliation, and verification are complete.
- Do not close a parent phase issue until its PR has merged back to `main`.
- Keep new file and directory names lowercase unless mirroring an upstream
  source name or archival payload.

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
