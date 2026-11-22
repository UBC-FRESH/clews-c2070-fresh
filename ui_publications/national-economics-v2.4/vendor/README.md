# Vendored browser assets

These files are committed so the iteration-0 UI runs without a build step or
runtime CDN dependency.

- `echarts.min.js` — Apache ECharts 5.5.1, Apache-2.0.
  Source: `https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js`
- `maplibre-gl.js`, `maplibre-gl.css` — MapLibre GL JS 4.7.1, BSD-3-Clause.
  Source: `https://unpkg.com/maplibre-gl@4.7.1/dist/`
- `fonts.css` + `fonts/*.woff2` — Fraunces variable (SIL OFL 1.1) and IBM Plex
  Sans/Mono Latin subsets (SIL OFL 1.1), downloaded from Google Fonts.
- `*.LICENSE.txt` — upstream licence texts.

No CDN is requested at runtime.
