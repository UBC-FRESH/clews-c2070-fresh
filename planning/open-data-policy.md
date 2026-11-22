# Open-data publication policy

| Field | Value |
|---|---|
| Document | `planning/open-data-policy.md` |
| Date | 2026-11-22 |
| Status | Active for derived national harvest-basis artifacts |
| Applies to | `data/derived/national/harvest_basis/**` and the code that builds it |
| First application | `open_data_net_value/model_inputs/` |

## 1. Definition of "open" in this project

For this project, **open** means:

> open code **plus** the actual derived model-input tables used, with source
> citations.

Publishing a model script without its inputs, or publishing inputs without the
code that consumes them, does not satisfy this policy. A publication must let a
third party verify what the model consumed, rerun the committed code on those
exact inputs, and reproduce the stated headline without obtaining restricted or
raw copyrighted files.

## 2. What is published

A conforming publication includes:

1. **Code** — the committed builder, model and reproduction scripts, at a
   recorded commit.
2. **Derived model inputs** — the exact rows, parameters and mappings the model
   reads. Selection filters, excluded rows and exclusion reasons are explicit.
3. **Source register** — source ID, title, publisher, URL, retrieval date,
   licence/terms and citation for every raw source relied on.
4. **Integrity manifest** — SHA-256 and row count for every published bundle
   file, the headline values, and provenance commits for parent artifacts.
5. **Reproduction command** — a fail-closed script that validates the manifest,
   runs the committed code against the bundled inputs, and checks the headline.

Derived inputs are published under the repository's existing derived-data
terms. They carry all model labels and caveats from the parent artifact.

## 3. What is not published

Raw copyrighted reports are **cited, not redistributed**. This includes:

- BC Government appraisal manuals and silviculture tables;
- BC log-market reports;
- TLA/ILA/NWLA/MNP rate guides;
- FPInnovations/FERIC reports;
- BC CVSE regulations and posters;
- Quebec MRNF survey reports.

The source register records URL, SHA-256, retrieval date and licence/terms.
Anyone reproducing the model must obtain those raw files from the rights holder
or publisher. The published derived tables are sufficient to reproduce the
existing headline without the raw PDFs.

Restricted, confidential, licence-restricted or personal data must never appear
in a bundle. A bundle containing such data fails publication review.

## 4. Required labels

Every open-data publication manifest and README must state:

```
input_publication=derived_model_inputs
raw_source_redistribution=false
evidence=model_estimate
observed=false
policy_grade=false
net_value_status=model_estimate
```

`observed`, `policy_grade` and combined BC+QC totals remain false unless a
separate, explicitly approved evidence layer changes them. Publishing inputs
does not upgrade a model estimate into an observed or policy-grade result.

## 5. Fail-closed verification

The reproduction script must exit non-zero on any of:

- missing, extra unlisted or hash-mismatched bundle file;
- wrong row count or malformed required column;
- raw/forbidden file (`.pdf`, office document, archive) inside the bundle;
- source row with `raw_redistributed` other than `false`;
- price cell marked used but flagged implausible, or excluded without a reason;
- cost parameter or source ID that does not resolve in the bundle;
- zone/region/rate-group mapping inconsistent with the model constants;
- headline or core-output hash mismatch against the published artifact.

A publication is not accepted until the reproduction command and focused tests
pass on the committed worktree.

## 6. Change control

- Do not edit published model outputs to match a new bundle. Rebuild the parent
  artifact through its committed builder, then regenerate the bundle.
- Do not silently widen a selection filter. Add the row, mark it
  `used_in_model=false`, and record an exclusion reason.
- Do not pool price vintages or report bases to increase sample size.
- Do not convert CAD/ha, CAD/km or CAD/each allowances to CAD/m3 without a
  published conversion.
- Do not change model, UI or public OSeCAN behavior as part of an input
  publication unless separately specified and reviewed.

## 7. First application: BC open-data net value

The first conforming bundle is:

```
data/derived/national/harvest_basis/open_data_net_value/model_inputs/
```

It publishes the primary 3-month 2025-12 price panel and the separate 1-month
sensitivity panel, the 945-row logging/haul cost grid with parameter and source
IDs, the seven Interior basic silviculture rows (with all 15 Coast CAM CAD/ha
rows explicitly excluded), the QC 2022 SEPM procurement cost-spread reference,
the zone/region mapping and the source register.

Reproduce with:

```bash
.venv/bin/python scripts/national/reproduce_open_data_net_value.py
```

The expected headline is median 48.84 CAD/m3, range -23.38 to 86.85 CAD/m3,
168/945 negative base rows, South West ground 64.40, North West ground 57.76,
and QC cost spread 0.81 CAD/m3.
