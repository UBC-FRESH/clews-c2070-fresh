# Gauthier 2015 Diagnostic Yield Fixture

This directory contains a lightweight diagnostic reproduction of the yield
method described in Gauthier et al. (2015). It does not contain raw NFI
reference pixels, NFI/Beaudoin rasters, SCANFI rasters, private correspondence,
or calibrated production yield curves.

The fixture encodes the published Table 1 coefficients for coniferous and
deciduous gross merchantable volume equations, evaluates them against synthetic
diagnostic input cells, locally scales composite curves to synthetic observed
volume at observed age, and estimates the age at which each cell reaches
`100 m3/ha`.

Outputs are method-reproduction artifacts only. The generated candidate yield
rules must not replace `diagnostic_yield_rules.csv` until required NFI/SCANFI
inputs, reference-pixel access, and validation policy are accepted.
