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
- [Generative AI](#generative-ai)
- [3D city model, and editing it](#3d-city-model-and-editing-it)
- [Online 3D assets](#online-3d-assets)
- [Data and overlays](#data-and-overlays)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Limitations: what this is not](#limitations--what-this-is-not)

---

## What works today

| Capability | Status | Verified by |
|---|---|---|
| REST API — the spec's §3 surface, the reserved AI slot, and the analysis endpoints | Working | 23 contract tests |
| Scene → plan → intervention cascade delete | Working | contract test |
| camelCase wire contract with `sceneId`/`planId` aliases | Working | contract test |
| Single error envelope (`detail` **and** `error`) | Working | contract test |
| 3D viewer: real terrain, imagery, boundary, camera | Working | headless-browser render, zero console errors |
| **Real elevation** (~90 m, Open-Meteo global model) | Working | control-point assertions on every fetch; resolution measured, not assumed |
| **Real land cover** (ESA WorldCover 10 m, CC-BY) | Working | 2 tiles, windowed COG read |
| Wet-grid overlay (CN runoff → storage → level-pool fill) | Working, screening-grade | monotonic in rainfall |
| Heat-exposure overlay — land class **+ sky view + cast shadow**, a relative 0–1 index | Working, screening-grade | a lone thin tower reads SVF 0.9913; a uniform block reads 0.0 |
| **Heat anchor** — the cited band that index is read against | Working | HKO Lau Fau Shan, July [26.4, 32.7] °C; reproducible via `fetch_heat_anchor.py --check` |
| **Interventions** — derived sites, plan selection, styled 3D rendering | Working, screening-grade | 2 tests: stated area vs geometry, draft status + provenance |
| **Generative AI** — natural language → grounded 3D edits | **Live.** Local `qwen2.5:3b-instruct` via Ollama, no key; deterministic fallback if absent | 30 tests, incl. real-HTTP round trips |
| **AI 3D placement** — new massing on real land cover | Working, screening-grade | 3 blocks × 60 m rendered, pixel-verified, selectable |
| **AI 3D alteration, scoped** — height / density / orientation, by region or site | Working | 1,232 of 2,639 buildings for "western blocks"; west/east visible in the render |
| **3D city model** — 2,639 massing envelopes, extruded and **directly editable** | Working, provisional massing | seeded; click-to-select + per-building height, browser-verified |
| **Online 3D assets** — glTF fetched from Khronos at runtime | Working | all 3 URLs verified 200 + CORS |
| **Terrain conditioning** — Priority-Flood + D8 flow accumulation | Working | 3 tests; 10.3% of DEM cells are depressions |
| **Design metrics** — height dispersion, site coverage, ground-level void, shade, each tied to a published AVA rule | Working | every metric names its source rule in the response |
| **Wind: least-cost-path corridors** per direction (Dijkstra on a `1 + cellFAD` cost surface) | Working, **geometry not flow** | 8 directions in 0.70 s; corridor cost monotone in FAD |
| **Wind: per-direction FAD ratio** | Working | `1.0` for identical schemes, `>1` where blockage rises |
| **Drainage: D8 fill-and-spill routing + enforced mass balance + HAND** | Working, screening-grade | residual **6.5e-15** relative; a deliberately leaked cell makes the assertion **raise** |
| **Scenario evaluation** — three axes at once, with **no combined score** | Working | raising the western blocks 40% taller gives more shade *and* more blockage |
| **Scenario comparison** — ordering under **stated** weights | Working | refuses to rank without weights; the three axes stay visible; `combinedScore` stays `null` |
| Zero-credential operation (no Cesium Ion token) | Working | app boots with empty `.env.local` |

**Not working / not built:** PostGIS + MinIO store, a physics engine, a
validated Curve Number table, Hato flood validation, and the ability to
*delete* an individual building — the AI can place blocks and change their
height, density and orientation, not remove them. Two drainage readings
deliberately coexist: the routed cascade and the level-pool headline, which are
not merged because they are different pictures of the same water. See
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

> **This guide is tested, not aspirational.** Every step below was run against
> a clean `git archive` export of the repository — a fresh clone with no `.env`,
> no `node_modules` and no caches. `preflight.py` reported *Ready*, 59/59 tests
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
python tests/test_constants.py    # 18/18 — constants, DEM, raster/feature invariants
python tests/test_ai_plan.py      # 30/30 — grounding, scope, placement

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
| View | `frontend/src/view/` | Cesium. `SandboxViewer` owns the Viewer; `terrainLoader`, `demProvider`, `cityModel` and `interventionLayer` are separate concerns. |
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
| `POST` | `/api/scenes/{sceneId}/ai-plan` | **Natural language → a draft plan** (the spec's reserved AI slot) |
| `GET` | `/api/scenes/{sceneId}/ledger` | The buffer deficit, and what stored interventions do to it |
| `GET` | `/api/scenes/{sceneId}/sea-level` | Official HKO 2100 projections run through the ledger |
| `GET` | `/api/scenes/{sceneId}/scenarios` | Three published CEDD development programmes, encoded |
| `GET` | `/api/scenes/{sceneId}/design-metrics` | Geometry metrics, each tied to a published AVA rule |
| `GET` | `/api/scenes/{sceneId}/ventilation` | Frontal area density per wind direction |
| `GET` | `/api/scenes/{sceneId}/ventilation/corridors` | Least-cost-path ventilation corridors, per direction |
| `GET` | `/api/scenes/{sceneId}/heat-field` | The blended heat field, sky view, and the cited anchor band |
| `GET` | `/api/scenes/{sceneId}/drainage/routed` | D8 fill-and-spill routing, mass balance and HAND |
| `POST` | `/api/scenes/{sceneId}/scenario/evaluate` | Three separate axis deltas — and **no combined score** |
| `POST` | `/api/scenes/{sceneId}/scenario/compare` | Order scenarios **only under stated weights**; refuses without them |

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
python scripts/fetch_heat_anchor.py                # → reference/heat_anchor.json
python scripts/fetch_heat_anchor.py --check        # asserts the committed file still reproduces
```

### 8. Three axes, never one score — and the tool refuses to grade

The AVA policy behind Hong Kong's own guidance (Ng & Cheng, 2009) states that
"whether the better design option meets a standard cannot be known", and TC 1/06
§6 gives no absolute benchmark. So this tool **compares and never grades**:
`verdict` is `null` on every response and nothing prints a pass or fail.

The obvious thing to do with three axes — weight them and add them up — is the
one thing refused, for three reasons. The units are incommensurable (a fraction
of ground, a dimensionless blockage, cubic metres). The ventilation axis has no
threshold to normalise against. And the axes **genuinely conflict**: making the
western blocks 40% taller gives more shade *and* more wind blockage at the same
time. A single number would settle that trade-off silently, in whichever
direction the weights happened to lean.

So `scenario/evaluate` returns three independent axes with their own units, and
`scenario/compare` **refuses to rank without weights**. Given weights it will
order — but it echoes them back, names the total `weightedFavourability` (never
`score`), and keeps the three raw axes and their per-axis ranks on **every** row,
so the cost of the weights is always visible. A default weight set would quietly
become this project's opinion about which axis matters, which is the judgement
the guidance says cannot be settled.

Two more things the same instinct drives: the routed drainage cascade asserts
`runoff in == stored + discharged` and **raises** rather than reporting a flag —
a leak should be loud, not a field someone can ignore — and there is a second
identity (`generation == local_retention + runoff_in`) so a double count cannot
hide inside a balanced cascade.

---

## Generative AI

`POST /api/scenes/{sceneId}/ai-plan` turns a sentence into 3D interventions that
appear in the scene. The panel has a box for it: type, press Generate, the plan
is created, selected, and rendered.

```
"add two water reservoirs and a green space on the largest open blocks"
   -> plan created, 3 draft features, rendered in 3D
```

### The design decision that matters

**The model chooses from a menu. It never emits a coordinate.**

Asked to "add a reservoir", an LLM will confidently return plausible-looking
coordinates anywhere on the map. In a planning tool that is worse than useless,
because it looks like a site investigation. So the model is given the candidate
sites — derived from the DEM, the ESA WorldCover land cover and the computed wet
grid — and asked to *select among them* by opaque id. The geometry is attached
afterwards from the real analysis.

That turns an open-ended generation problem into a selection problem, which is
what language models are actually reliable at, and it makes hallucinated
geometry **structurally impossible** rather than filtered out. A test asserts the
catalogue handed to the model contains no coordinates at all, so the guarantee
cannot quietly erode.

### Grounding, not invention

Every proposed site carries `params.basis` naming the raster it came from, and
`params.rationale` from the model. The vocabulary is the spec's own
(§4.1): `绿地` green space, `透水铺装` permeable paving, `蓄水池` water reservoir,
`道路` road.

If the analysis has not been run, the module **refuses** and says which script to
run. It will not produce geometry with nothing to site it on.

### Two providers

| Provider | What it is | Credentials |
|---|---|---|
| `claude` | Anthropic Messages API, forced tool-use | `ANTHROPIC_API_KEY` |
| `openai-compatible` | Any `/chat/completions`: **Ollama** (live, local), OpenRouter, DeepSeek, Moonshot, DashScope, Groq, vLLM | none for Ollama |
| `heuristic` | Deterministic intent parser — **not AI**, the baseline and the fallback | none |

`auto` uses the LLM when a key is present and falls back otherwise, and the
response always names the provider that ran. Asking for `llm` without a key is a
**422**, not a silent downgrade — a demo that quietly stops being AI is worse than
one that says so.

The heuristic provider is not a stub. It is the non-AI baseline: an LLM output is
only worth having if there is something to compare it against.

### Status and honesty

Every feature is `source="ai"` and `status="draft"`, per ADR-030 — the engine
refuses to compute on draft geometry, which is the correct posture for
machine-proposed sites. Generated features travel the **same**
validate → store → render path as hand-drawn ones, which §3.3.2 requires
("用户绘制与 AI 方案落地共用同一套管线"), so nothing downstream special-cases AI
output.

The LLM path is exercised on every test run through an injected transport that
replays a recorded response, so it is not untested code waiting for a key.

### Running it live, with no key at all

Ollama serves an OpenAI-compatible API, so a real model needs no vendor account:

```bash
# runtime (no Homebrew, no Docker)
curl -L -o ollama.tgz https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
mkdir -p ~/.local/bin/lib/ollama && tar -xzf ollama.tgz -C ~/.local/bin/lib/ollama
cp ~/.local/bin/lib/ollama/ollama ~/.local/bin/ollama && chmod +x ~/.local/bin/ollama
ollama serve &

ollama pull qwen2.5:3b-instruct        # ~1.9 GB
```

Then in `backend/.env`:

```ini
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:3b-instruct
```

Check it, and make one real call:

```bash
python scripts/llm_probe.py --instruction "make the district 25% taller"
curl -s localhost:8000/health | grep -A6 '"llm"'      # isAi: true
```

`llm_probe.py` never prints a key — only whether one is set and its length. It
validates the configured model id against `GET /v1/models` rather than trusting a
hard-coded string, so a rotated id is caught before the demo rather than during
it.

### Two things a small local model forced us to fix

**The model must not choose the tool.** Both tools were originally advertised
with a permissive `tool_choice`, on the reasoning that a system-prompt rule
would be enough. `qwen2.5:3b-instruct` settled that: asked *"make the district
25% taller to reduce flood risk"* with both tools available, it returned **no
tool call at all** and the feature vanished silently. Offering one tool is a
guarantee; a prompt rule is a request. Routing is now decided in code by
`route_tools()`, and the model is never asked.

**Tool-calling is not universal, structured output is.** The same model returns
nothing at all for the massing schema. So the OpenAI-compatible provider falls
back to JSON-schema-constrained output
(`response_format: {"type": "json_schema"}`), which the same model satisfies
instantly and correctly. The grounding guarantee is untouched either way,
because it lives in `_materialise`, not in the tool plumbing.

Decoding is pinned to `temperature: 0`. At default temperature the same sentence
gave different answers on successive runs, and a demo must show the plan you
rehearsed.

### Try it

```bash
cd backend
python scripts/ai_plan.py "add two water reservoirs and a green space" --dry-run
python scripts/ai_plan.py "make the district 25% taller"
python scripts/llm_probe.py
```

**What it does not do:** it does not reason about flood hydraulics, and it does
not design a district. It places *interventions* and it places and reshapes
*buildings* — five operations, all grounded in the analysis. It cannot invent a
footprint, and it cannot move a block.

```bash
cd backend
python scripts/ai_plan.py "add three 60 m residential towers in the east"
python scripts/ai_plan.py "make the western blocks 30% taller"
python scripts/ai_plan.py "rotate the northern blocks 20 degrees"
python scripts/ai_plan.py "add two water reservoirs and a green space"
python scripts/llm_probe.py
```

## 3D city model, and editing it

The sandbox has a 2,639-building massing model that you can change directly, and
that the language model can change for you.

**Where the geometry comes from — and what was tried first.** OSM building
footprints were the first choice and are **unreachable from this host**. Overpass
returned `504` from the main endpoint and `000` from four mirrors, with an empty
result from the one instance that answered; Microsoft's GlobalML Building
Footprints now returns `409 Public access is not permitted on this storage
account`; a Geofabrik Hong Kong extract is ~90 MB and its PBF parser will not
build here either. All of that is recorded in `citymodel_meta.json` so nobody
re-derives it.

So the massing is **procedural, derived from real land cover**: the ESA
WorldCover 10 m `paved` class says where the ground *is* built up, and blocks go
only there. The land-use input is real; the footprints are not surveyed, heights
are an assumed distribution (11–131 m, mean 35 m, four classes), and every
feature records `height_source: ASSUMED_DISTRIBUTION_NOT_SURVEYED`. The panel
carries a **provisional** chip for exactly this reason.

**Why geometry and not a glTF massing model.** A mesh would be one draw call but
would be *opaque* — you cannot change a building's height inside it. Cesium
entities are per-feature property bags, so a height edit is a property
assignment: no rebuild, no re-upload, no regeneration. Changing the model is
the point.

**Editing by hand, three ways:**

| Gesture | What it does |
|---|---|
| **+25% / −20% height** | Scales every building. The scenario question — "what if the district were taller?" |
| **Click a building** | Selects it (highlighted), then type a height in metres |
| **Reset** | Back to the generated heights, orientations and block count |

Verified in a browser: mean height 35.3 m → 40.2 m for "western blocks 30%
taller", which reaches 1,232 of 2,639 buildings — and in the render the western
half is visibly taller while the eastern half is untouched. The generator is
seeded, so the same command twice produces an identical file.

**Editing by language, five ways.** Every one of these is grounded — the model
names a region or a catalogue site, never a coordinate:

| Instruction | Operation | What it does |
|---|---|---|
| "make the district 25% taller" | `scale_all` | Every building's height × 1.25 |
| "set the buildings in the west to 60 m" | `set_site` | Absolute height, one region |
| "make the eastern blocks 30 m taller" | `set_site` | A **change** in metres, not an absolute — reading it as absolute would silently set every building to 30 m |
| "rotate the northern blocks 20 degrees" | `rotate` | Turns the footprints about their own centroids, in metres |
| "add three 60 m residential towers in the east" | `place` | **Adds** three real footprints |
| "reset the building heights" | `reset` | Back to generated |

### Scope: the part that was missing, and why it mattered

`place` is placement. The other four are alteration — and alteration is
meaningless without *where*.

The first version of this had no way to say "the western blocks". The region word
was parsed, then discarded, and `scale_all` came back for every building in the
district. The screen filled with a taller, plausible, entirely wrong skyline, and
the panel cheerfully reported success. It is the worst failure mode in this
project: not an error, not a wrong number, but a 3D result that answers a
question nobody asked.

So every operation now carries a **scope**, and it is resolved in the backend
from three sources and nowhere else:

- a **region** — one of ten named fractions of the locked study-area bbox
  (`west`, `east`, `north`, `south`, `centre`, and the four corners). Plain
  arithmetic over a constant the project already owns, so there is nothing for a
  model to hallucinate.
- a **site id** from the grounded catalogue — `water_reservoir#0`,
  `permeable_paving#2` — whose polygon the analysis already derived.
- nothing, meaning the whole study area.

A region we do not recognise produces **no edit at all**, never a fallback to
"everything". A test asserts that directly, because the fallback is the bug.

### Where a placed block comes from

The model chooses a count, a height and a region. It never chooses a coordinate.
The footprints are generated by `placement_footprints()` from the same ESA
WorldCover `paved` mask and the same block pitch `make_citymodel.py` uses — and
cells already occupied by an existing envelope are excluded, so a new tower lands
on unbuilt paved land rather than inside an existing block. If a scope has no
unbuilt land left, the filter is dropped and `params.basis` says so.

The result is a real, selectable, editable entity in the same model as the
generated ones: click it, type a height, and it changes like any other. The panel
reports how many blocks a plan added, and how many buildings an alteration
reached — because a 3D view looks identical whether an operation hit 400
buildings or all 2,639.

> **Placed blocks are not a design.** They sit on real land cover, at a height
> the model asked for, in a region the model named. That is a scenario, not a
> scheme.

## Online 3D assets

Three glTF models load **straight from the Khronos glTF sample set on GitHub
raw** — no token, no proxy, no binary in the repo. That source was chosen
because it serves `Access-Control-Allow-Origin: *`, which is what lets a browser
fetch it directly; every URL was confirmed to return `200` with a CORS header
before being written down.

Only the **small** models are used. The set contains some excellent 3–9 MB
assets, and shipping a 9 MB lantern to decorate a flood sandbox is a worse
engineering choice than a 1.6 KB box:

| Asset | Size | Used for |
|---|---|---|
| `Box.glb` | 1.6 KB | survey marker, north basin |
| `BoxTextured.glb` | 6 KB | survey marker, west block |
| `SimpleSparseAccessor.gltf` | 1.7 KB | survey marker, south channel |

The layer reports how many loaded, so a dead URL is visible rather than silent.


---

## Data and overlays

### Sources

| Layer | Source | Licence | Resolution | Status |
|---|---|---|---|---|
| Elevation | [Open-Meteo elevation API](https://open-meteo.com/) | open | **~90 m** (measured) | Real |
| Land cover | [ESA WorldCover 10 m v200](https://esa-worldcover.org/) (2021) | CC BY 4.0 | 10 m | Real |
| Base imagery | OpenStreetMap standard tiles | ODbL | — | Real |
| Heat anchor | [HKO open data — Lau Fau Shan station](https://data.weather.gov.hk/weatherAPI/opendata/opendata.php) (`CLMTEMP`/`CLMMAXT`/`CLMMINT`) | HKO open data (GovHK Terms of Use) | daily station record, ~20 km from the site | **Cited band** — *air* temperature, July 2015–2024; a stated band, not a site measurement |
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

The wet grid is a **level-pool** allocation: water is placed by water-surface
level, not routed downhill, and the **headline deficit** (`bufferDeficitM3`,
`wetgrid_hsk.npy`) comes from this path. A **separate** D8 fill-and-spill routing
cascade now exists (`routed_depth_hsk.npy`,
`GET /api/scenes/{id}/drainage/routed`, mass-balanced to 1e-15 relative) as an
**additive screening surface** — it does **not** replace or correct the headline
number, and it is not a flood prediction. The two are different pictures of the
same water and are deliberately not merged.

### The heat overlay

**The final choice: real air temperature, cited, as a band the relative index is
read against — not a temperature field.**

The field itself is unchanged: land class (hand-authored) + sky view + cast
shadow → a **relative heat-exposure index on a 0–1 scale** → RGBA ramp. It is
**not degrees Celsius** and **not** a modelled air-temperature field. What is new
is that the scale now states the band it is read against, from committed, cited
data (`backend/data/reference/heat_anchor.json`):

- **Real, not synthesized.** Source: **HKO open data — Lau Fau Shan (LFS)
  station daily air temperature**,
  `https://data.weather.gov.hk/weatherAPI/opendata/opendata.php`
  (`dataType=CLMTEMP|CLMMAXT|CLMMINT`, `station=LFS`), retrieved **2026-10-09**.
- **Air temperature, not skin.** Every skin-temperature source (Landsat 8/9 C2 L2
  ST, MODIS MOD11, ECOSTRESS) is credential-gated and was unreachable from this
  host. Skin runs 10–20 °C above air on a hot paved surface and is **not**
  claimed.
- **The band: [26.4, 32.7] °C.** Index 0.0 ↔ 26.4 °C (a typical July night, the
  mean daily minimum); index 1.0 ↔ 32.7 °C (a typical July afternoon, the mean
  daily maximum). Mean of the daily min/max over **July 2015–2024**, missing
  values excluded.
- **Honest caveat.** This is a **stated band for a Hong Kong summer, not a
  measurement of this site** — the LFS station is ~20 km away, and it is a 10-year
  July climatology, not the site's air temperature on any day.
- Cross-checked against the HKO 1991–2020 normals (HKO HQ, July 26.9 / 31.6) and
  the Open-Meteo ERA5 archive at the site centroid (July mean-of-daily-max
  30.99 °C). Neither is the anchor.

The anchor is a **committed file, never fetched at request time** (deterministic);
it is reproduced by `backend/scripts/fetch_heat_anchor.py`. The overlay is
rendered at a **summer solar day (day 196, 15 July)** to match the July band.
`--heatwave` is still a *display* stretch, not a scenario.

### Both scripts register themselves

Each script attaches its output to the seeded scene, so `GET /terrain` starts
returning real URLs and the viewer switches light up. That is what makes §4.1's
"reserved fields" testable rather than aspirational.

**The sliders do not trigger a recomputation.** They write `envParams` into the
frontend store only; re-running the script is a manual step. The panel says so.

---

## Testing

There is no pytest in the venv — every suite is a plain script that prints
`N/N passed` and exits non-zero on any failure, so a red suite fails a CI run:

```bash
cd backend
for f in tests/test_*.py; do ./.venv/bin/python "$f"; done   # 404 checks, 11 suites

# individually:
python tests/test_design_metrics.py      #  79 — sky view, shadow, per-cell FAD
python tests/test_scenario.py            #  54 — three axes, no combined score, compare()
python tests/test_ai_plan.py             #  39 — grounding, scope, placement, hostile responses
python tests/test_ventilation.py         #  40 — frontal area density, corridors, FAD ratio
python tests/test_ledger.py              #  39 — the storage ledger and the routed cascade
python tests/test_building_fit.py        #  32 — per-building candidate scoring
python tests/test_published_scenarios.py #  28 — three CEDD programmes
python tests/test_hydrology.py           #  26 — depressions, fill-and-spill, mass balance, HAND
python tests/test_contract.py            #  23 — API contract
python tests/test_constants.py           #  18 — constants, DEM, raster/feature invariants
python tests/test_sea_level.py           #  15 — HKO projections
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

A note on verification: **a clean `tsc` did not mean the frontend worked.** Four
runtime bugs passed the type checker, including a `tsconfig` path alias that
TypeScript resolved and Vite did not. Frontend changes here are verified by
driving a real headless-Chromium render and asserting zero console errors, not
by the compiler.

**The 3D editing path is verified the same way, and it earned its keep.** Two
bugs in it were invisible to every other check in this repository:

- **Click-to-select never worked.** `GeoJsonDataSource` copies `properties.title`
  or `properties.name` onto `entity.name` and nothing else, and these features
  have neither — their key is `properties.id`, which Cesium does not copy. So
  `picked.id.name` was always `undefined`, every click resolved to "no building",
  and the panel's "click a building to select it" did nothing at all. 915 clicks
  across the district selected nothing. The fix is a reverse map keyed on entity
  identity; after it, nine clicks selected one.
- **The AI's scope was discarded.** "Make the western blocks 30% taller" produced
  a correct `scale_all` for the whole district. No test could see it, because the
  result was a valid feature carrying a valid multiplier.

The browser check therefore asserts what the *viewer* reports, not what the
backend asked for — "1,232 of 2,639 buildings" — because a 3D view looks the same
either way.

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
    │   │   ├── cityModel.ts      the editable 3D massing model; scoped AI ops
    │   │   └── interventionLayer.ts  discrete features (extrusion)
    │   ├── components/           panel, layer switches, camera, sliders
    │   ├── App.tsx  main.tsx  index.css
    ├── vite.config.ts            @ alias + /api and /static dev proxies
    ├── .env.local.example
    └── package.json
```

### What is committed, and what is not

`backend/data/` is committed, so a fresh clone runs without fetching anything.
Every file below is meaningful — nothing here is a placeholder someone forgot:

| File | What it is |
|---|---|
| `scene_hsk.json` | the locked study area, with its provenance |
| `scenes/<id>.json`, `plans/<id>.json` | the seeded scene and baseline plan |
| `dem_hsk.npy`, `dem_terrain_rgb.png`, `dem_hsk.json` | real elevation + its manifest |
| `landclass_hsk.npy`, `landcover_hsk.json` | real land cover + its manifest |
| `rasters/wetgrid_hsk.*`, `rasters/heatmap_hsk.*` | the two overlays + manifests |
| `rasters/flow_receiver_hsk.npy`, `flow_raw_elev_hsk.npy`, `flow_filled_hsk.npy` | the D8 routing graph, the raw surface, and the Priority-Flood surface the receiver was derived from |
| `rasters/routed_depth_hsk.npy` | the routed fill-and-spill depth — a screening surface beside the level-pool headline, not a replacement |
| `reference/heat_anchor.json` | the cited air-temperature band the heat index is read against |
| `reference/hsk_ava_reference.json` | the published AVRG73 wind regime and velocity ratios (carried, never recomputed) |

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

**Not a flood model.** The wet grid allocates water by level, not by routing, and
that level-pool path still produces the headline deficit. A D8 fill-and-spill
cascade and a HAND cross-check now exist as an **additive screening surface**,
but they are screening-level, they do not replace the level-pool headline, and
they are not a flood prediction. No tide boundary — the Hong Kong 0.146 m
mCD↔mPD datum offset is a named constant with no tide series behind it yet. It
shows *where water would collect over plausible topography*, nothing more.

**The Curve Number table is unverified.** Hydrologic soil group D is *assumed*
for HSK/HT and has not been independently checked. Runoff volume scales directly
with CN, so this is the single assumption most worth attacking next.

**The study-area extent is provisional.** A machine-derived operational
rectangle, not the gazetted NDA boundary.

**The heat overlay is a relative index, not a temperature.** It is anchored to a
cited air-temperature band (HKO Lau Fau Shan, July [26.4, 32.7] °C), but that is
a stated band for a Hong Kong summer, not a measurement of this site.
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
4. **Reconcile the two drainage readings** — the routed fill-and-spill cascade
   exists and balances, but the headline deficit still comes from the level-pool
   path. Decide which is the headline, or show both.
5. **Replace the ~20 km-away anchor** with a site-specific source (a nearby
   station, or a Landsat/MODIS LST scene once credentials exist), and keep the
   air-vs-skin distinction.
6. **Stand up PostGIS + MinIO** and re-run the contract suite against it.
7. **Validate against a Hato flood record**, which is what the task plan's
   comparison step assumes exists.

---

## Credits

- [CesiumJS](https://cesium.com/platform/cesiumjs/) — 3D globe, Apache-2.0
- [OpenStreetMap](https://www.openstreetmap.org/copyright) — base imagery, ODbL
- [ESA WorldCover](https://esa-worldcover.org/) — 10 m land cover, CC BY 4.0
- [Open-Meteo](https://open-meteo.com/) + [SRTM](https://www2.jpl.nasa.gov/srtm/)
  — elevation
- [Hong Kong Observatory](https://www.hko.gov.hk/) — open-data air-temperature
  record for the heat anchor (Lau Fau Shan station), and the 2100 sea-level
  projections; GovHK Terms of Use
- [USDA NRCS TR-55](https://www.nrcs.usda.gov/) — the SCS-CN method
- Ng & Cheng (2009), *Building and Environment* 44:1478–1488 — the AVA policy
  behind the design rules every geometry metric cites
- AVRG73 (Planning Department / CEDD) — the published HSK wind regime and velocity
  ratios, carried as published and never recomputed
- Specification: `docs/技术文档.docx`
