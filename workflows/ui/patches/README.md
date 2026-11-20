# OSeCAN model patches

These patches are applied in order to public `deltae/C2070` commit
`40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` by
`workflows.ui.setup_osecan_model`. They are FRESH-side changes and are not
pushed to the public OSeCAN repository.

| Patch | Files | Purpose |
|---|---|---|
| `01_emission_factors.patch` | `src/osecan/data/build_params.py` | Scale the fuel-input CO2 factor by the plant `InputActivityRatio`, giving `EmissionActivityRatio` and emission-cap UDC multipliers the output-activity basis OSeMOSYS uses. Gas `NGS` becomes `0.051 x 2.70 = 0.1377`; coal `COA` becomes `0.090 x 2.90 = 0.261` Mt/PJ output. |
| `02_zonal_bc.patch` | `src/osecan/model_structure.py`, `src/osecan/sets_n_ratios.py` | Add `BCWB1` / `BCWB2` fuels and `SUPBCWB1` / `SUPBCWB2` supplies, and switch only the B1/B2 `PWRBCW` plants from the national `BCW` pool to their zone fuel. Other zones keep `BCW`. |
| `03_tiered_supply.patch` | `src/osecan/model_structure.py`, `src/osecan/sets_n_ratios.py`, `src/osecan/data/build_params.py` | Add the four `SUPBCWT1`-`T4` technologies feeding the national `BCW` pool. Base `build-params` creates their names and output ratios but deliberately leaves caps and costs empty; the FRESH supply-curve scenario builder supplies the adopted tier caps and variable costs. |

The patch SHA-256 digests are embedded in `setup_osecan_model.PATCHES`. A
mismatch aborts the rebuild before any patch is applied.
