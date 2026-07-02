# Network-Flow Seed Recovery Summary

## Source

The possibly only surviving copy of the code for the ws3-compatible forest
sector supply-chain network-flow optimization model is the GitHub gist
`gparadis/bcc357dbeaab2c8d247776424c51c25a`, which contains
`phase2_ua06451.ipynb`.

The raw notebook was downloaded to ignored local path:

- `tmp/gist-network-flow/phase2_ua06451.ipynb`

Lightweight extracted products are committed under:

- `metadata/ffcm-archive/network-flow-seed/phase2_ua06451_extracted_code.py.txt`
- `metadata/ffcm-archive/network-flow-seed/phase2_ua06451_extracted_markdown.md`
- `metadata/ffcm-archive/network-flow-seed/phase2_ua06451_cell_index.csv`

The methodological paper anchor is:

- `tmp/CIRRELT-2018-24.pdf`, section 2.1, "Network flow optimisation model
  formulation and compilation".

## Notebook Role

The notebook states that it implements phase 2 of the project: compiling the
network-flow model and running scenarios to simulate policy options. It appears
to be an executable/research-notebook implementation of the model described in
CIRRELT-2018-24 section 2.1.

The notebook is not immediately production-ready code. It is a seed for
rebuilding the model because it contains:

- Python 2 style syntax and IPython notebook magics.
- Hard-coded local project/data paths.
- Dependencies on unavailable local datasets, including Woodstock model
  artifacts and MERIS/BMMB data.
- Dependencies on `gurobipy`, `dill`, `mdbread`, `ipyparallel`, `pacal`, and
  `ws3`.
- Research comments and scenario code interleaved with model-building logic.

## Model Semantics

The recovered notebook and CIRRELT-2018-24 section 2.1 describe a linear
network-flow optimization model that emulates profit-maximizing fibre
consumption by primary processing facilities.

Core node layers:

- `src`: source nodes, mapped one-to-one to harvesting decisions in a Woodstock
  / ws3 wood-supply solution.
- `dsp`: dispatch nodes, also one-to-one with source nodes.
- `cdt`: commodity nodes, representing MERIS-style intermediate commodity
  classes.
- `prc`: processor nodes, representing mills or primary-processing facilities.
- `snk`: sink nodes, one per processor.

Core flow semantics:

- `src -> dsp` arcs represent harvested area in hectares.
- `dsp -> cdt` arcs convert harvested area to harvested commodity volumes.
- `cdt -> prc` arcs represent merchantable fibre volume flowing to processors.
- `prc -> snk` arcs represent processor sink/facility capacity flow.
- Additional sawmill co-product arcs represent chips and sawdust moving from
  sawmill processors to pulp, MDF/NSP, pellet, or other processing nodes.

Core objective semantics:

- The model maximizes net value-creation potential.
- The objective includes revenues and costs from standing tree through delivered
  primary product.
- Cost/revenue components include fixed costs, harvesting, silviculture,
  stumpage, transport, product value, and optional subsidy terms.

Core constraint semantics:

- Flow-conservation constraints connect dispatch, commodity, processor, and
  sink layers.
- Source-dispatch flow bounds simulate harvesting any subset of the Woodstock /
  ws3 solution.
- Commodity-processor bounds represent timber licence / GA allocation and
  facility eligibility.
- Processor-sink bounds represent facility capacity.
- Scenario logic modifies bounds, subsidies, facility/network configuration, or
  demand/cost assumptions.

## Rebuild Implications

The notebook should be treated as the seed for rebuilding a clean FRESH network
flow model, not as code to run directly.

Rebuild priorities:

1. Extract pure model-building functions from `compile_base_model`,
   `solve_model`, and solution-compilation functions.
2. Replace hard-coded UA 064-51, MERIS, Woodstock, and local filesystem
   assumptions with table-driven inputs from the FRESH soft-link contract.
3. Keep the five-layer node logic where useful, but represent node, arc,
   commodity, cost, capacity, and scenario data explicitly in dataframes.
4. Replace Python 2 notebook idioms with tested Python modules.
5. Decide whether to keep Gurobi as an optional solver backend or implement a
   solver-agnostic interface.
6. Use CIRRELT-2018-24 section 2.1 as the formulation narrative and the gist
   notebook as the implementation seed.
