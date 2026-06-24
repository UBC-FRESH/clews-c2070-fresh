# Tyler Onboarding

Date prepared: 2026-06-24.

## Project Context

CLEWs-C2070 links climate, land, energy, water, and socio-economic modelling for
Canada through 2070. The UBC FRESH contribution is the forestry and
forest-biomass supply component: a national-scale, spatial-temporal modelling
framework capable of estimating sustainable forest biomass availability, cost,
carbon implications, and feedstock dynamics.

The first phase is data infrastructure, not model coding. The immediate product
is a reproducible national data repository that explains what sources exist,
where they came from, what they contain, their limitations, and how they can be
used in a future WS3/FEMIC/FHOPS biomass supply workflow.

## Tyler's Initial Role

May-August 2026 allocation: about one day per week.

Primary responsibilities:

- identify candidate national, provincial, and territorial data sources;
- record metadata and licensing status;
- flag sources needing access requests or license review;
- test small downloads where appropriate;
- document preprocessing needs; and
- help separate raw source layers from modelling-ready derived products.

## First Meeting Agenda

1. Project purpose and modelling target.
2. Difference between source discovery, raw archive, and derived model products.
3. Data repository pattern: parent repo plus DataLad-backed `data` submodule.
4. Metadata standard and how to update `metadata/source-inventory.csv`.
5. First source families to investigate.
6. Weekly handoff rhythm and definition of done for a dataset entry.

## First Work Package

Start with the rows already listed in `metadata/source-inventory.csv`.

For each row, fill in or verify:

- authoritative publisher;
- source landing page;
- direct download/API/service endpoint if available;
- license name and reuse constraints;
- spatial coverage;
- temporal coverage;
- update cadence;
- format and approximate size;
- whether login/manual request is required;
- known limitations;
- recommended use in the modelling framework; and
- next action.

Do not download large data into the parent repository. If a test download is
needed before the DataLad submodule exists, use a local scratch directory outside
the repo or wait until the `data` repository is linked.

## Suggested First Priorities

1. National Forest Inventory and SCANFI products.
2. National/provincial forest inventory and forest cover layers.
3. Fire, harvest, insect, and other disturbance products.
4. Road/rail/port and forest-resource road layers.
5. Forest industry facility and biomass residue datasets.
6. Protected areas, management constraints, tenure/management unit boundaries,
   and Indigenous land/governance constraints.
7. Carbon/climate datasets relevant to LibCBM/GCBM-style accounting.

## Definition of Done for a Source Entry

A source entry is ready for review when it has enough information for a PI or
maintainer to decide one of these outcomes:

- accept into `metadata/required-datasets.yaml`;
- reject with rationale;
- defer pending license/access clarification; or
- split into multiple more precise dataset entries.
