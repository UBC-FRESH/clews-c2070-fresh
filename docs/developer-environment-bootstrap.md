# Developer Environment Bootstrap

This is the CLEWS-C2070 FRESH fresh-clone bootstrap path, following the
FEMIC/FreshForge materialization pattern.

## Install

Use a repo-local virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[workflow]"
```

For the full ws3 smoke-test lane, install the local editable ws3 checkout after
the workflow dependencies:

```bash
python -m pip install -r requirements-ws3-smoke.txt
```

Use `.venv/bin/...` explicitly in agent terminal sessions so diagnostics target
the project environment rather than the global shell. `git-annex` is an
OS-level prerequisite. Install it before running the materialization workflow:

```bash
git annex version
.venv/bin/datalad --version
.venv/bin/python -m freshforge --version
```

## Inspect And Plan

FreshForge validation, inspection, and planning are non-mutating:

```bash
.venv/bin/python -m freshforge providers
.venv/bin/python -m freshforge validate examples/freshforge/materialization_smoke_workflow.yaml
.venv/bin/python -m freshforge plan examples/freshforge/materialization_smoke_workflow.yaml
.venv/bin/python -m freshforge validate examples/freshforge/data_materialization_workflow.yaml
.venv/bin/python -m freshforge plan examples/freshforge/data_materialization_workflow.yaml
```

## Materialize Lightweight Data Paths

The default executable workflow initializes the `data` submodule, enables the
`arbutus-s3` special remote, and materializes only lightweight `metadata` and
`manifests` paths. It intentionally does not materialize `data/raw`.

```bash
.venv/bin/python -m freshforge run examples/freshforge/data_materialization_workflow.yaml \
  --workdir runtime/freshforge \
  --namespace data-bootstrap
export CLEWS_EXTERNAL_DATA_ROOT=$PWD/data
```

## Boundaries

Maintainer actions that create or publish the DataLad repository, configure the
Arbutus bucket, or push annex objects remain in
`docs/data-repository-runbook.md`. Collaborator materialization should use the
FreshForge workflow above rather than hand-written one-off command sequences.
