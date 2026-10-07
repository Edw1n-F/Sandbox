# T1 — Flood & Heat Risk Sandbox for Hong Kong

A 3D planning sandbox for climate-adaptation scenarios: load a study area, put a
development plan on it, and see where water collects and where heat is trapped.

**Study area:** Hung Shui Kiu / Ha Tsuen New Development Area (HSK/HT), northern
New Territories — 113.982–114.005 °E, 22.425–22.455 °N, roughly 786 ha.

> **This is a prototype, not an engineering tool.** It is built to demonstrate a
> decision chain, not to produce numbers you can defend in a statutory
> submission. The UI says so on screen, every generated raster carries a
> `.json` sidecar recording its assumptions, and
> [`docs/OCT6_STATUS.md`](docs/OCT6_STATUS.md) keeps an explicit real /
> placeholder / assumed split. Please read the
> [Limitations](#limitations--what-this-is-not) section before quoting any figure.

---

## Table of contents

- [What works today](#what-works-today)
- [Tech stack](#tech-stack)
- [Running it locally](#running-it-locally)
- [Architecture](#architecture)
- [Design decisions and why](#design-decisions-and-why)
- [Data and overlays](#data-and-overlays)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Limitations: what this is not](#limitations--what-this-is-not)

---

## What works today

| Capability | Status | Verified by |
|---|---|---|
| REST API — 9 endpoints per spec §3 | Working | 11 contract tests |
| Scene → plan → intervention cascade delete | Working | contract test |
| camelCase wire contract with `sceneId`/`planId` aliases | Working | contract test |
| Single error envelope (`detail` **and** `error`) | Working | contract test |
| 3D viewer: real terrain, imagery, boundary, camera | Working | headless-browser render, zero console errors |
| **Real elevation** (~90 m, Open-Meteo global model) | Working | control-point assertions on every fetch; resolution measured, not assumed |
| **Real land cover** (ESA WorldCover 10 m, CC-BY) | Working | 2 tiles, windowed COG read |
| Wet-grid overlay (CN runoff → storage → level-pool fill) | Working, screening-grade | monotonic in rainfall |
| Heat-exposure overlay (relative index, 0–1) | Working, screening-grade | — |
| **Interventions** — derived sites, plan selection, styled 3D rendering | Working, screening-grade | 2 tests: stated area vs geometry, draft status + provenance |
| Zero-credential operation (no Cesium Ion token) | Working | app boots with empty `.env.local` |

**Not working / not built:** PostGIS + MinIO store, a physics engine, flow
routing, a validated Curve Number table, Hato flood validation, AI modules. See
[Limitations](#limitations--what-this-is-not).

## Tech stack

Chosen for one overriding reason each: **the demo has to run on a laptop, with
no credentials, no Docker and no paid services.**

### Backend — Python 3.13 / FastAPI

| Choice | Why this and not the alternative |
|---|---|
| **FastAPI** | The spec's contract is OpenAPI-first, with validation errors that must carry a FastAPI `detail` array *and* a custom `{"error": {...}}` envelope. FastAPI gives the first for free and Pydantic v2 gives typed request/response models, so a wire contract is enforced rather than hoped for. |
| **Pydantic v2 + pydantic-settings** | `extra="forbid"` on response models is what caught the `sceneId`/`id` bug during the build. Settings come from `.env` with validated, *anchored* paths. |
| **Store mode as configuration** | `STORE_MODE=file` writes JSON to disk; `STORE_MODE=postgis` is the target. Swapping is confined to `app/db.py` + `app/services/scene_service.py` — no route, schema or frontend change. See [ADR-020](#1-store-mode-is-configuration-not-architecture). |
| **numpy + Pillow** | The overlay maths is array work. No GDAL in the critical path. |
| **rasterio** *(only for one script)* | Reading ESA WorldCover GeoTIFF as a windowed COG. Its wheels bundle GDAL, so no Homebrew GDAL is needed. Nothing else imports it. |

### Frontend — React 18 / TypeScript / CesiumJS 1.146

| Choice | Why this and not the alternative |
|---|---|
| **CesiumJS** | The spec asks for a 3D globe. Cesium is the only mature open-source option, and it runs without a token if you disable the Ion base layer. |
| **No Cesium Ion** | `baseLayer: false` plus an explicit `OpenStreetMapImageryProvider`. The default Viewer pulls Bing through Ion and renders a broken globe without a token. This way `VITE_CESIUM_ION_TOKEN` is empty and the demo still works. |
| **`CustomHeightmapTerrainProvider`** | Cesium's stock terrain provider wants quantized-mesh from a tiling service. We have a single 2.4 × 3.3 km terrain-RGB raster instead, so this first-class API serves it — no ion, no tile server, no token. |
| **Zustand** | Two small stores (`scene`, `plan`) with selectors. Redux's ceremony buys nothing at this size. |
| **MUI** | Gives accessible switches, sliders and dialogs for free, and the panel looks like an analysis tool rather than a demo. |
| **`SingleTileImageryProvider` for overlays** | Continuous fields are images, not thousands of polygons: smaller payload, better frame rate. Extrusion is reserved for discrete features. |

### Deliberately not used

Docker, PostgreSQL/PostGIS, MinIO, GDAL, `alembic`, a physics engine, any
paid or token-gated API. Each is documented with the reason in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#deliberately-not-used).

---

## Running it locally

> **This guide is tested, not aspirational.** Every step below was run against a
> clean `git archive` export of the repository — a fresh clone with no `.env`, no
> `node_modules` and no caches. `preflight.py` reported *Ready*, 24/24 tests
> passed, and both overlays regenerated from the committed data. If a step here
> breaks, that is a bug in the repo rather than a surprise for you.

### Prerequisites

| Requirement | Version tested | Notes |
|---|---|---|
| Python | **3.13** | 3.10+ works. Avoid 3.9 — the code uses `X \| None` syntax. |
| Node.js | **22** | 20+ should be fine. |
| npm | 10 | Ships with Node 22. |
| GDAL / Docker / Postgres | **not needed** | Deliberately. |

No API keys, no accounts, no `.ion` token.

### Step 1 — clone and configure

```bash
git clone https://github.com/Edw1n-F/T1.git
cd T1

# The backend reads backend/.env. Every value has a working default EXCEPT
# STATIC_BASE_URL, which must match the /static mount or overlay and terrain
# URLs will 404.
cp backend/.env.example backend/.env
```

`backend/.env` should then contain at minimum:

```ini
STORE_MODE=file
DATA_DIR=./data
STATIC_BASE_URL=/static
```

> **`STATIC_BASE_URL=/static` is not optional.** With it empty the API returns
> `/rasters/wetgrid_hsk.png` for a file served at
> `/static/rasters/wetgrid_hsk.png`. The symptom is not a 404 in your face — the
> layer just never appears, which reads as "the toggle is broken".

### Step 2 — backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Then check the whole environment in one shot:

```bash
python scripts/preflight.py
```

This verifies the interpreter, the packages, `STATIC_BASE_URL`, and the data
files — and prints the `VITE_SCENE_ID` you need in step 6. It exits non-zero if
the project cannot run, so it is also the first thing to run when something
misbehaves. It specifically catches the case where a stale `backend/.venv`
(3.10, no numpy) is shadowing the interpreter you *think* you are using, which
otherwise surfaces as a confusing `ModuleNotFoundError` from inside a script.

### Step 3 — fetch the open data (optional but recommended)

The repository already ships a derived DEM and land-cover grid, so you can skip
this. Re-run it to refresh from the upstream sources:

```bash
cd backend

# Real land cover: ESA WorldCover 10 m v200, CC BY 4.0. ~7 s.
python scripts/fetch_landcover.py

# Real elevation: Open-Meteo elevation API. ~3 min,
# because the free API throttles on request frequency and the fetcher paces
# itself. Progress is cached, so an interrupted run resumes for free.
python scripts/fetch_dem.py
```

Both are public, key-less, and print their own provenance. Neither writes
anything if the response fails its sanity checks.

### Step 4 — note the scene id

A fresh clone already contains a seeded scene, so **you can skip this step** and
just read the id:

```bash
python scripts/preflight.py | tail -2      # prints VITE_SCENE_ID
```

Or re-run the seeder, which is **idempotent** — it updates the existing baseline
scene in place rather than creating a second one:

```bash
python scripts/seed_scene.py                # add --reset to delete and re-seed
```

The id comes from `backend/data/scene_hsk.json`, the locked study area, so the
extent is machine-derived and reproducible rather than hand-typed.

### Step 5 — generate the overlays

```bash
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
```

Writes `backend/data/rasters/*.png` plus a `.json` sidecar per raster listing
every constant, assumption and source. Both scripts attach their output to the
seeded scene automatically, so the viewer's overlay switches become live.

### Step 5b — derive interventions (recommended)

The spec's model is **scene → plan → intervention**, and the interventions level
shipped empty. This script fills it — and derives every site from the analysis
rather than placing arbitrary polygons:

```bash
python scripts/seed_interventions.py --reset
```

- **Flood storage** in the deepest connected basins of the wet grid
- **Retention / heat mitigation** on the largest contiguous vegetated blocks
- **Drainage alignment** traced steepest-descent from the deepest basin

Each feature records the raster it came from in `params.basis`, and all are
`status="draft"` — the engine refuses to compute on draft geometry (ADR-030).
Re-running with `--reset` replaces only plans this script created.

### Step 6 — frontend

```bash
cd ../frontend
npm install
cp .env.local.example .env.local     # if it is not already present
```

Put the scene id from step 4 in `frontend/.env.local`:

```ini
VITE_CESIUM_ION_TOKEN=
VITE_API_BASE_URL=
VITE_SCENE_ID=<the id from seed_scene.py>
```

Then:

```bash
npm run dev
```

Vite prints a URL — usually <http://localhost:5173>. Open it.

> The dev server proxies **both** `/api` and `/static` to
> `http://localhost:8000` (see `vite.config.ts`). If you see a layer silently
> failing to appear, check that proxy first.

### Step 7 — run the two servers side by side

The backend must be running too. In a second terminal:

```bash
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --reload --port 8000
```

Confirm it is healthy:

```bash
curl -s localhost:8000/health
```

### Step 8 — verify the whole chain

```bash
SCENE_ID=$(python -c "import json,glob,os;print(os.path.splitext(os.path.basename(glob.glob('data/scenes/*.json')[0]))[0])")

curl -s "localhost:8000/api/scenes/$SCENE_ID"          | head -c 400
curl -s "localhost:8000/api/scenes/$SCENE_ID/terrain"  | head -c 400
curl -s "localhost:8000/api/scenes/$SCENE_ID/plans"    | head -c 400

# Overlays and the DEM raster must be reachable, not just listed.
curl -s -o /dev/null -w "wet grid  %{http_code} %{size_download}b\n" \
  "localhost:8000/static/rasters/wetgrid_hsk.png"
curl -s -o /dev/null -w "dem rgb   %{http_code} %{size_download}b\n" \
  "localhost:8000/static/dem_terrain_rgb.png"

# Interactive API docs
open http://localhost:8000/docs
```

You should see the HSK/HT area with real relief, and toggling **Wet grid** and
**Heat exposure** should paint over the study area.

### Step 9 — run the tests

```bash
cd backend
python scripts/preflight.py      # environment; exits non-zero if not runnable
python tests/test_contract.py     # 11/11 — API contract
python tests/test_constants.py    # 13/13 — constants, DEM, raster/feature invariants

cd ../frontend
npx tsc --noEmit                 # must be clean
```

### Run it all at once (optional)

```bash
# terminal 1
cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --reload

# terminal 2
cd frontend && npm run dev
```

---

## Architecture

### The chain

```
open data (fetch_*.py)
  → backend/data/                     dem_hsk.npy, landclass_hsk.npy, rasters/*.png
    → scene record                    scene_hsk.json  →  POST /api/scenes  → sceneId
      → GET /api/scenes/{sceneId}     center / bbox / defaultCamera
      → GET /api/scenes/{sceneId}/terrain
             terrainUrl  → terrain-RGB raster → CustomHeightmapTerrainProvider
             imageryUrl  → OpenStreetMapImageryProvider
             wetGridUrl  → SingleTileImageryProvider
             heatmapUrl  → SingleTileImageryProvider
        → Cesium Viewer renders
      → GET /api/scenes/{sceneId}/plans
        → GET /api/plans/{planId}/interventions  → GeoJSON FeatureCollection
```

Every arrow is an HTTP call. The frontend never touches the filesystem or a
database; the backend never knows what Cesium is.

### Layers

| Layer | Path | Responsibility |
|---|---|---|
| Routers | `backend/app/routers/` | HTTP only. Parse, delegate, serialise. No business logic, no filesystem. |
| Schemas | `backend/app/schemas/` | The wire contract. `RowModel` translates storage ↔ wire. |
| Services | `backend/app/services/` | All business logic: cascade deletes, bbox normalisation, overlay resolution. |
| Store | `backend/app/db.py` | Reads and writes documents. The *only* module that knows the store is files. |
| Config | `backend/app/config.py` | Settings, with paths anchored to `backend/`, never to the cwd. |
| Raster scripts | `backend/scripts/` | Fetch and analysis. Pure functions over arrays; they attach results to a scene at the end. |
| API client | `frontend/src/api/` | One `fetch` wrapper, one module per resource, typed. |
| Stores | `frontend/src/state/` | Zustand. `sceneStore` and `planStore` only. |
| View | `frontend/src/view/` | Cesium. `SandboxViewer` owns the Viewer; `terrainLoader`, `demProvider` and `interventionLayer` are separate concerns. |
| Components | `frontend/src/components/` | Panel, layer switches, camera, sliders. No Cesium imports except `CameraController`. |

### API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `POST` | `/api/scenes` | Create a scene from a study area |
| `GET` | `/api/scenes/{sceneId}` | Read a scene |
| `PUT` | `/api/scenes/{sceneId}` | Update a scene |
| `DELETE` | `/api/scenes/{sceneId}` | Delete a scene **and cascade** its plans and interventions |
| `GET` | `/api/scenes/{sceneId}/terrain` | Terrain + imagery + overlay URLs, bbox, centre |
| `GET` | `/api/scenes/{sceneId}/plans` | Plans under a scene |
| `PUT` | `/api/plans/{planId}` | Create or update a plan |
| `GET` | `/api/plans/{planId}/interventions` | Interventions as a GeoJSON `FeatureCollection` |

---

## Design decisions and why

### 1. Store mode is configuration, not architecture

The target is PostGIS + MinIO. This host has no Docker, no Postgres and no
Homebrew, so the store is JSON on disk.

The point of doing it this way is that **`STORE_MODE` is a switch, not a
rewrite.** The boundary is drawn at exactly two modules — `app/db.py` and
`app/services/scene_service.py`. No route, no schema and no frontend code knows
which store is active. A contract test runs the full suite against file mode;
the same suite runs unchanged against PostGIS when it exists.

Had the routes known about storage, "temporarily" would have become permanent by
the third endpoint.

### 2. Degrees on the wire, metres in the maths

Every coordinate that crosses the API is EPSG:4326, because the spec mandates it
and because Cesium and GeoJSON both speak it. But degrees are not a unit of
length, and summing rainfall over "a degree" is meaningless.

So there is exactly **one** place where degrees become metres:
`build_grid()` in `backend/app/services/geo_import.py`. It converts using a local
equirectangular approximation about the extent centre — at a 3 km extent the
error against HK1980 Grid is far below the 5 m cell size. Every raster script
calls it and nothing else converts units.

For volume reporting, swap HK1980 Grid (EPSG:2326). The comment says so at the
choke point.

### 3. `RowModel` — one translation point for the wire contract

Storage uses `id` (the spec's DDL) while the API uses `sceneId` / `planId`. This
is an **alias, not a rename**: storage keeps the column name from the schema.

Both live in `RowModel.from_row`, so there is one function to read and one to
change. The alternative — a `@computed_field` or scattered `Field(alias=...)` —
works until something bypasses it, and then storage IDs leak onto the wire
without anyone noticing. `extra="forbid"` on the response models is what turned
that from a silent leak into a startup error during the build.

### 4. The spec contradicts itself in six places, and code resolves it

`技术文档` §3 and §4 disagree. Rather than pick a reading and hope, each
conflict is resolved in one named place:

| Conflict | Resolution |
|---|---|
| §3.2.2 `bbox` example has 2 coordinates — not a valid ring | Normalised to a closed ring on ingress; the validator repairs short input |
| §4.2.1 `defaultCamera` has `lon`/`lat`; §3.2.1 does not | Model is the **union**; `lon`/`lat` default to the scene centre, so the frontend always gets a complete camera |
| DB column `id` vs API `sceneId`/`planId` | Alias via `RowModel` (§3 above) |
| `plan_type` CHECK allows 5 values; nothing creates `economic`/`environmental` | Left legal and unreachable, noted in `models/plan.py` |
| Both docs reference a `migrations/` directory that does not exist | **Still missing.** Unnecessary in file mode. Flagged, not faked |
| §3.5 mandates both a FastAPI `detail` array and `{"error": {...}}` | **Both are emitted.** The frontend has one error branch |

### 5. Real open data, with the source refused if it lies

The single most important decision in the data pipeline: **a data source that
cannot prove itself is rejected, not trusted.**

The first DEM source tried was AWS's `elevation-tiles-prod` terrarium tiles. It
returns HTTP 200 with plausible-looking PNGs — and the pixels are not
terrarium-encoded. Everest decoded to ~1,000,000 m and the South Atlantic to
792,940 m. Both the `terrarium` and `normal` paths failed the same check. It
would have produced a beautiful, silent, wrong landscape.

So `fetch_dem.py` asserts **Everest ≈ 8,000–9,500 m and the open ocean ≈ 0 m**
before it will write a single file. A bad response fails loudly. The same script
records the rejected bucket in its own output metadata so nobody re-derives it.

The second lesson is quieter and cost more to catch. The DEM was requested on a
**1 arc-second** lattice, which is the textbook answer for SRTM. The API does
not serve SRTM at 1 arc-second — it serves a ~90 m global model. The response
was a 78 × 110 array holding only **28 × 37 distinct samples**: three-fold
oversampling, where the *data was correct* and the *metadata was wrong*.

Nothing errored. The hillshade looked plausible. It only became visible by
counting distinct values. The oversampling has since been removed
losslessly, and every resolution claim in this repo now says **~90 m** —
because that is what was measured. Re-sampling finer would have duplicated the
same values again, not resolved them. Details in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

### 6. Honesty is a feature, not a disclaimer

Every raster writes a `.json` sidecar next to its `.png` listing every constant,
every assumption and its source. The viewer reports a failed overlay in the
panel instead of swallowing it. The UI states the prototype limitation on
screen.

This is not decoration. Three separate bugs presented as "the toggle does
nothing" — an unregistered Vite alias, a StrictMode-destroyed viewer, and an
image that failed to decode. The first two were invisible to the type checker.
Making failure visible is what turned a guess into a diagnosis.

### 7. Rasters are generated artefacts, not source

No PNG is committed as a source of truth. Each is reproducible:

```bash
python scripts/fetch_landcover.py                  # → landclass_hsk.npy
python scripts/fetch_dem.py                       # → dem_hsk.npy
python scripts/make_wetgrid.py --rainfall 450      # → wetgrid_hsk.png
python scripts/make_heatmap.py                     # → heatmap_hsk.png
```

---

## Data and overlays

### Sources

| Layer | Source | Licence | Resolution | Status |
|---|---|---|---|---|
| Elevation | [Open-Meteo elevation API](https://open-meteo.com/) | open | **~90 m** (measured) | Real |
| Land cover | [ESA WorldCover 10 m v200](https://esa-worldcover.org/) (2021) | CC BY 4.0 | 10 m | Real |
| Base imagery | OpenStreetMap standard tiles | ODbL | — | Real |
| Study-area extent | OSM place anchors (Nominatim) | ODbL | — | **Provisional** — see below |

The study area was chosen over Deep Bay because HSK/HT has the stronger public
evidence base (approved EIA, OZP S/HSK/3, LegCo study). Its extent is derived
from machine-retrieved OSM anchors and recorded in
`backend/data/scene_hsk.json` with the queries and OSM ids used.

> **The extent is a rectangular operational extent, not the gazetted NDA
> boundary.** Replace it with the sheet extent of OZP S/HSK/3. Every raster
> would shift if it changed, which is exactly why it is recorded rather than
> typed into a script.

Full provenance, the ESA→our-class mapping, and the rejected sources are in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

### The wet grid

A **screening view**, not a flood prediction. The pipeline, in the order the
baseline storage ledger requires:

1. **Generation** — SCS-CN runoff depth per cell from rainfall and the class
   Curve Number (hydrologic soil group D, assumed).
2. **Retention** — subtract what standing water and wetland already hold, plus a
   depression allowance elsewhere. Land cover sets how much *runs off*; explicit
   storage sets how much can be *held*; **no cell is counted in both.**
3. **Allocation** — solve for the water-surface elevation that holds the
   remaining volume, by bisection. A level pool, so the picture is a pond rather
   than a uniform smear, and the printed volume matches the picture.

Verified monotonic in rainfall: 0 / 200 / 450 / 800 mm gives
0 / 8.7 / 17.0 / 27.2 % wet area.

There is **no flow routing and no sink conditioning** — water is placed by
level, not routed downhill. That is the difference between this and a flood
model, and it is the main reason this is a screening view.

### The heat overlay

Land class → relative heat-exposure index on a 0–1 scale → RGBA ramp.

It is **not degrees Celsius** and **not** a modelled air-temperature field. It
needs no terrain, which is why it is the overlay that can honestly ship today.
`--heatwave` is a *display* stretch, not a scenario, so a screenshot taken under
the preset cannot be mistaken for different conditions.

### Both scripts register themselves

Each script attaches its output to the seeded scene, so `GET /terrain` starts
returning real URLs and the viewer switches light up. That is what makes §4.1's
"reserved fields" testable rather than aspirational.

**The sliders do not trigger a recomputation.** They write `envParams` into the
frontend store only; re-running the script is a manual step. The panel says so.

---

## Testing

```bash
cd backend
python tests/test_contract.py     # 11/11 — API contract
python tests/test_constants.py    # 13/13 — constants, DEM, raster/feature invariants
cd ../frontend && npx tsc --noEmit
```

**`test_contract.py`** — every endpoint, the cascade delete, the `sceneId` alias,
bbox ring closure, and that the error envelope carries both shapes.

**`test_constants.py`** exists because the source documents name two constants
that *fail silently*. One of them was caught in practice: written as
`S = 25400/CN − 2540` it returns negative retention for any Curve Number above
~91, which makes the denominator of `Q` negative and produced a first run
reporting **5.4 × 10¹⁸ m³** of runoff — while rendering a perfectly
plausible-looking image. The correct form is `−254`, now pinned against the
TR-55 worked example.

Two more guards the same instinct: the committed `dem_terrain_rgb.png` must
decode back to `dem_hsk.npy` within terrain-RGB's own 0.1 m quantum (the Python
encoder and the browser decoder are two implementations of one formula in two
languages, and if they drift the terrain renders subtly wrong with nothing
erroring), and the DEM's height range must be plausible for a coastal Hong Kong
floodplain — a DEM that decodes to 800 km is a broken source, not a broken night.

**`scripts/preflight.py` guards the environment itself, which is where the
most common failure actually lives.

A note on verification: **a clean `tsc` did not mean the frontend worked.** Three
runtime bugs passed the type checker, including a `tsconfig` path alias that
TypeScript resolved and Vite did not. Frontend changes here are verified by
driving a real headless-Chromium render and asserting zero console errors, not
by the compiler.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Overlay switches are greyed out | The scene has no `wetGridUrl` / `heatmapUrl` | Run `make_wetgrid.py` and `make_heatmap.py`, then reload the scene |
| Switch is enabled but nothing appears | The PNG never reached the backend | Check `STATIC_BASE_URL=/static` in `backend/.env` **and** that `/static` is in the Vite proxy |
| "An overlay failed to load" in the panel | Image failed to decode, or the request 404'd | Open the console; `curl` the URL from `/terrain` against port 8000 |
| Blank globe, "Cesium is not defined" | Cesium's static assets not copied | Ensure `vite-plugin-cesium` is in `vite.config.ts` plugins |
| `401`/`403`-looking globe, or Bing imagery missing | Cesium Ion token expected | Leave `VITE_CESIUM_ION_TOKEN` empty; the app sets `baseLayer: false` |
| Terrain is flat | `terrainUrl` is null | Run `fetch_dem.py`; the fetcher registers the raster on the scene |
| `fetch_dem.py` exits with HTTP 429 | The free API throttles on request frequency | Just re-run it — every batch is cached, so it resumes where it stopped |
| `ModuleNotFoundError: rasterio` | Deps not installed in the active venv | `pip install -r requirements.txt`; check `which python` |
| Two Python versions fighting | `backend/.venv` (3.10) is stale and lacks numpy | Use one interpreter; `source backend/.venv/bin/activate` first |

> **Known environment wart:** there are two Python environments on this
> machine — `backend/.venv` (3.10, missing numpy and rasterio) and a managed
> 3.13. Only the 3.13 one can run the raster scripts. Standardising this is an
> outstanding task; see [`docs/OCT6_STATUS.md`](docs/OCT6_STATUS.md).

---

## Project layout

```
T1/
├── README.md                     ← you are here
├── CLAUDE.md                     repo conventions for AI agents
├── docs/
│   ├── OCT6_STATUS.md            dated evidence log: real / placeholder / assumed
│   ├── ARCHITECTURE.md           design decisions in depth
│   ├── DATA_SOURCES.md           provenance, licences, rejected sources
│   ├── 技术文档.docx              the specification (Chinese, of record)
│   └── _技术文档_clean.txt        extracted text, for grepping
├── backend/
│   ├── app/
│   │   ├── main.py               FastAPI app, CORS, /static mount
│   │   ├── config.py             settings; paths anchored to backend/
│   │   ├── db.py                 the only module that knows storage is files
│   │   ├── models/               storage shape (incl. the spec DDL)
│   │   ├── schemas/              the wire contract + RowModel
│   │   ├── services/             business logic
│   │   └── routers/              HTTP only
│   ├── scripts/
│   │   ├── preflight.py          is this machine able to run it? exits 1 if not
│   │   ├── preflight.py          can this machine run it? exits 1 if not
│   │   ├── seed_scene.py         create the scene from the locked study area
│   │   ├── seed_interventions.py derive intervention sites from the analysis
│   │   ├── fetch_landcover.py    ESA WorldCover  → landclass_hsk.npy
│   │   ├── fetch_dem.py          Open-Meteo → dem_hsk.npy (+ terrain-RGB)
│   │   ├── _overlay_common.py    shared grid, land classes, CN, resampling
│   │   ├── make_wetgrid.py       CN runoff → storage → level-pool fill
│   │   └── make_heatmap.py       land class → index → RGBA
│   ├── tests/                    contract + constants
│   ├── data/                     the file store (git-tracked, see below)
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── api/                  fetch wrapper + one module per resource
    │   ├── state/                Zustand: sceneStore, planStore
    │   ├── view/
    │   │   ├── SandboxViewer.tsx owns the Cesium Viewer
    │   │   ├── terrainLoader.ts  imagery + terrain binding, with honest fallback
    │   │   ├── demProvider.ts    terrain-RGB → CustomHeightmapTerrainProvider
    │   │   └── interventionLayer.ts  discrete features (extrusion)
    │   ├── components/           panel, layer switches, camera, sliders
    │   ├── App.tsx  main.tsx  index.css
    ├── vite.config.ts            @ alias + /api and /static dev proxies
    ├── .env.local.example
    └── package.json
```

### What is committed, and what is not

`backend/data/` is committed, so a fresh clone runs without fetching anything.
Twelve files, all of them meaningful:

| File | What it is |
|---|---|
| `scene_hsk.json` | the locked study area, with its provenance |
| `scenes/<id>.json`, `plans/<id>.json` | the seeded scene and baseline plan |
| `dem_hsk.npy`, `dem_terrain_rgb.png`, `dem_hsk.json` | real elevation + its manifest |
| `landclass_hsk.npy`, `landcover_hsk.json` | real land cover + its manifest |
| `rasters/wetgrid_hsk.*`, `rasters/heatmap_hsk.*` | the two overlays + manifests |

Not committed, and regenerated on demand:

| Path | Why |
|---|---|
| `backend/data/dem_cache/` | Transient HTTP cache — one JSON file per API batch, keyed on coordinates so an interrupted `fetch_dem.py` resumes for free. Gitignored. Safe to delete. |
| `backend/.env`, `frontend/.env.local` | Machine-local. Copy from the `.example` templates. |
| `backend/.venv/`, `frontend/node_modules/` | Standard build artefacts. |
| `docs/*.docx`, `docs/_*_extracted.txt` | Kept for provenance; the `.docx` is the specification of record. |

---

## Limitations: what this is not

Stated plainly, because the specification asks for a tool that does not
overclaim, and because a plausible wrong number is worse than no number.

**Not a flood model.** The wet grid allocates water by level, not by routing. No
flow accumulation, no sink conditioning, no drainage network, no tide
boundary — the Hong Kong 0.146 m mCD↔mPD datum offset is a named constant with
no tide series behind it yet. It shows *where water would collect over plausible
topography*, nothing more.

**The Curve Number table is unverified.** Hydrologic soil group D is *assumed*
for HSK/HT and has not been independently checked. Runoff volume scales directly
with CN, so this is the single assumption most worth attacking next.

**The study-area extent is provisional.** A machine-derived operational
rectangle, not the gazetted NDA boundary.

**The heat overlay is a relative index, not a temperature.** And
`--heatwave` is a display transform, not a scenario.

**The land cover is a single 2021 epoch.** It does not show change since then,
which is a real gap for an area under construction.

**Vertical datum is unhandled.** Open-Meteo returns orthometric heights (mPD).
No vertical datum shift, no LiDAR, no bare-earth correction — and if vegetation
or structures bias elevation upward, a *deficit* estimate is biased too.

**No physics engine, no AI module, no PostGIS.** All out of scope for this phase
and all named in the task plan.

### Next steps, in priority order

1. **Verify the Curve Number table** independently (HSG for HSK/HT). Biggest
   single lever on the numbers.
2. **Replace the study-area extent** with the OZP S/HSK/3 sheet extent.
3. **Standardise the Python interpreter** — there are two, and only one runs the
   raster scripts.
4. **Add flow routing and sink conditioning** to turn the wet grid from a
   screening view into something defensible.
5. **Stand up PostGIS + MinIO** and re-run the contract suite against it.
6. **Validate against a Hato flood record**, which is what the task plan's
   comparison step assumes exists.

---

## Credits

- [CesiumJS](https://cesium.com/platform/cesiumjs/) — 3D globe, Apache-2.0
- [OpenStreetMap](https://www.openstreetmap.org/copyright) — base imagery, ODbL
- [ESA WorldCover](https://esa-worldcover.org/) — 10 m land cover, CC BY 4.0
- [Open-Meteo](https://open-meteo.com/) + [SRTM](https://www2.jpl.nasa.gov/srtm/)
  — elevation
- [USDA NRCS TR-55](https://www.nrcs.usda.gov/) — the SCS-CN method
- Specification: `docs/技术文档.docx`
