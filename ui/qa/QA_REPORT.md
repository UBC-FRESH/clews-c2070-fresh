# UI QA report — iteration 0

**Date:** 2026-11-19 18:52 UTC
**Scope:** headless visual QA of `ui/` at repo commit state (working tree unchanged)
**Result:** **BLOCKED — no headless browser binary or Playwright package is installed.**
No visual results were produced or inferred.

## Browser/tool availability

| Tool | Status |
|---|---|
| `chromium` | missing |
| `chromium-browser` | missing |
| `google-chrome` / `google-chrome-stable` | missing |
| `firefox` / `webkit` engines | missing |
| `playwright` (CLI) | missing |
| `playwright` (Python module) | missing (`ModuleNotFoundError`) |
| Playwright browser cache (`~/.cache/ms-playwright`, `/root`, `/home/*`) | absent |
| `selenium` / `pyppeteer` | missing |
| `wkhtmltoimage` / `wkhtmltopdf` / `cutycapt` | missing |
| `node` | present (`/usr/bin/node`) |
| `npx` | present, but Playwright package is not installed |

A filesystem sweep for `chrome`, `chromium`, and `headless_shell` executables found
none outside package caches. No browser binary exists to drive.

## What was verified instead (non-visual)

Static server was started and all referenced resources were fetched over HTTP.

```bash
cd /srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh
python3 -m http.server 8011 --bind 127.0.0.1 -d ui &
# then: curl every root asset, vendor asset, font, and file under ui/public/data
```

- **HTTP audit:** 71/71 URLs returned `200` — 14 root/vendor/font assets plus all
  57 files under `ui/public/data/`.
- **Server log:** 73 requests, zero `404`, `403`, or `500` responses.
- **Manifest contract:** `manifest.json` reports `schema_version: iteration0/1`,
  `validation.ok: true`, `scenario_count: 47`, `output_count: 47`.
- **JS syntax:** `node --check ui/app.js` passed.
- **Static DOM contract:** all 62 `$('id')` element references in `app.js` resolve
  to an `id` in `index.html`; zero missing.
- **Working tree:** no UI code or data files modified by this QA pass.

These checks reduce the risk of missing-asset and wiring errors, but they do
**not** validate rendering, layout, fonts, charts, MapLibre canvas output,
responsive behaviour, or runtime console errors.

## Screenshots (paths/sizes)

No screenshots were captured. Expected paths, once a browser exists:

- `ui/qa/desktop.png` — 1440x1200, full page
- `ui/qa/mobile.png` — 390x844
- `ui/qa/dispatch.png` — after selecting a scenario
- `ui/qa/map.png` — after selecting a trade-off point and opening Map

## Console/404/visual issues

- Console errors/warnings: **not collected** (no browser).
- 404s: **none** in the HTTP audit above.
- Visual/layout issues: **not assessed** — reporting any would be fabrication.

## Exact commands once a browser is available

### Option A — Playwright (preferred; enables console capture, clicks, full-page shots)

```bash
cd /srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh
python3 -m http.server 8011 --bind 127.0.0.1 -d ui &
pip install playwright && playwright install chromium   # heavyweight; only with approval

python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w, h in [("desktop", 1440, 1200), ("mobile", 390, 844)]:
        pg = b.new_page(viewport={"width": w, "height": h})
        errs = []
        pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.goto("http://127.0.0.1:8011/", wait_until="networkidle")
        pg.wait_for_timeout(2500)
        pg.screenshot(path=f"ui/qa/{name}.png", full_page=True)
        print(name, "console:", errs)
        pg.close()
    b.close()
PY
```

### Option B — Chromium CLI (screenshots only; no console capture)

```bash
chromium --headless=new --disable-gpu --hide-scrollbars \
  --virtual-time-budget=6000 --window-size=1440,1200 \
  --screenshot=ui/qa/desktop.png http://127.0.0.1:8011/

chromium --headless=new --disable-gpu --hide-scrollbars \
  --virtual-time-budget=6000 --window-size=390,844 \
  --screenshot=ui/qa/mobile.png http://127.0.0.1:8011/
```

Then repeat with Playwright for tab click-through (Overview, Dispatch,
Trade-off, Map, OSeCAN Links, Inputs), one scenario selection, one trade-off
point, and Dispatch/Map captures.

## Status/commit

- **Not committed.** No screenshots were generated, and UI code/data were not
  touched. The only new file is this report.
- Suggested ignore entry if screenshots are later treated as artefacts:
  `ui/qa/*.png`

## Remaining QA gap

Everything visual remains unverified:

1. Desktop/mobile layout, overflow, sticky rail, typography and local WOFF2 fonts.
2. ECharts Dispatch rendering, legend, zone filter, year sliders, tooltips.
3. Trade-off scatter rendering, family colours, selection behaviour.
4. MapLibre canvas, layer toggles, 385 RSYC tiles, 11 zone gaps, popups.
5. Tab navigation and scenario/trade-off selection end-to-end.
6. Runtime console errors/warnings (ECharts/MapLibre warnings included).
