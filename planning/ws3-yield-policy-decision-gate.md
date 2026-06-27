# ws3 Yield Policy Decision Gate

## Purpose

This note defines the next yield-policy gate for replacing the current
synthetic placeholder supply quantities. The selected primary path is to
reproduce the merchantable-volume yield method described in Gauthier et al.
(2015) before treating any ws3-side yield input as accepted.

## Primary Path: Gauthier et al. 2015

The Gauthier method estimates pixel-level gross merchantable volume
(`m3/ha`) curves from forest age, historical mean annual temperature,
historical mean annual precipitation, and coniferous/deciduous composition.
The published method:

- parameterized coniferous and deciduous curves from 30,090 NFI photoplot
  reference pixels;
- retained 13,856 pixels after composition filtering and robust outlier
  handling;
- applied curves to forest-property raster pixels;
- merged coniferous and deciduous curves into a composite pixel curve;
- scaled each pixel curve to measured volume at measured age;
- estimated age to commercial maturity at `100 m3/ha`;
- treated pixels with maturity age over 200 years as unproductive.

## Current Diagnostic Spike

`metadata/ffcm-archive/prototype-inputs/gauthier-diagnostic/` contains a
lightweight method-reproduction fixture. It encodes the published Table 1
coefficients, runs the equations on synthetic diagnostic cells, and produces
candidate yield rules for review.

This fixture does not contain raw NFI reference pixels, NFI/Beaudoin rasters,
SCANFI rasters, private correspondence, or calibrated production yield curves.

## Acceptance Gate

The Gauthier path can replace `diagnostic_yield_rules.csv` only when:

- reference-pixel access and reuse rights are resolved;
- national raster inputs or accepted SCANFI substitutes are selected;
- coniferous/deciduous aggregation rules are explicit;
- temperature and precipitation predictor sources are selected;
- the scaling step is reproduced from real measured volume and age fields;
- validation against NFI standard reports or another accepted benchmark is
  documented;
- ws3 yield-table or supply-proxy semantics are selected.

## Secondary Candidates

RSYC remains a strong executable fallback for biomass yield curves, especially
because the local parameter CSV and tile grid are already available in `tmp/`.
MAGPlot and provincial yield curves remain candidates for later calibration or
validation work.
