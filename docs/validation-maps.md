# Quick validation maps

`scripts/render_validation_map.py` renders lightweight maps for human inspection
of model geometry, sampled windows, and workflow smoke-test artifacts. It is an
optional prototype/supervision tool; it is not part of the production model
contract.

## Recommended basemap

The default is Natural Earth's 1:10m layers:

- administrative-1 province boundaries;
- country outlines and coastline;
- lakes; and
- a small set of populated places for orientation.

Natural Earth data are public domain. The renderer uses pale fills and thin,
low-contrast reference lines so the model overlay remains dominant. Archives are
cached under `~/.cache/clews-c2070-basemaps` (or the
`CLEWS_BASEMAP_CACHE` environment variable), never committed to this repo.

Natural Earth source: <https://www.naturalearthdata.com/>.

For more detailed regional orientation, install the optional `contextily`
dependency and use `--basemap osm`. That mode uses OpenStreetMap tiles and
adds `© OpenStreetMap contributors` attribution to the figure. OSM tile usage
must follow the selected tile provider's usage policy; it is intentionally not
the default for offline/reproducible smoke tests.

## Example

From the repository root:

```text
.venv/bin/python scripts/render_validation_map.py \
  --output tmp/validation-maps/scanfi-six-cells.png
```

The script reads the inventory CSV and the per-cell window manifests, extracts
`window_bounds_lcc`, and transforms the boxes from the authoritative SCANFI
source Lambert CRS to longitude/latitude for display. The source CRS is an
unregistered NAD83 Lambert Conformal Conic definition with `lat_0=0`,
`lon_0=-95`, `lat_1=49`, and `lat_2=77`; it must not be substituted with
EPSG:3978. A custom inventory or manifest root can be supplied with
`--inventory` and `--manifest-root`.

## Supervision rule

A map is a QA artifact, not evidence that the coordinates are correct. Before
interpreting a map geographically, verify that each raster's CRS and bounds
transform to the intended jurisdiction. The initial six-cell rendering exposed
that the derived rasters had been incorrectly tagged EPSG:3978, which placed
the windows in roughly 92°E–110°E. The repair preserved the numeric windows but
rewrote all 30 derived TIFF CRS tags and six manifests from the SCANFI source
WKT. The corrected map now places all six windows in British Columbia; the
geographic audit is part of the validation step and must remain in future
materialization runs.
