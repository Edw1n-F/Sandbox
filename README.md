# T1 — Flood & Heat Risk Sandbox for Hong Kong

A 3D planning sandbox for climate-adaptation scenarios: load a study area, put a
development plan on it, and see where water collects and where heat is trapped.

**Study area:** Hung Shui Kiu / Ha Tsuen New Development Area (HSK/HT), northern
New Territories. The analysis window is 113.9922–113.9994 °E, 22.431–22.4363 °N,
about 43.5 ha, cropped to the Open3Dhk tile coverage on 13 Oct 2026.

> **This is a prototype, not an engineering tool.** It is built to demonstrate a
> decision chain, not to produce numbers you can defend in a statutory
> submission. The UI says so on screen, every generated raster carries a
> `.json` sidecar recording its assumptions, and
> [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) separates what is real from
> what is assumed. Please read the
> [Limitations](#limitations-what-this-is-not) section before quoting any figure.

---

## Contents

- [What works today](#what-works-today)
- [Tech stack](#tech-stack)
- [Running it locally](#running-it-locally)
- [Architecture](#architecture)
- [Generative AI](#generative-ai)
- [3D city model, and editing it](#3d-city-model-and-editing-it)
- [Online 3D assets](#online-3d-assets)
- [Data and overlays](#data-and-overlays)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Limitations: what this is not](#limitations-what-this-is-not)

---

## What works today

| Capability | Status | Verified by |
|---|---|---|
| REST API — the spec's §3 surface, the reserved AI slot, and the analysis endpoints | Working | `test_contract` |
| Scene → plan → intervention cascade delete | Working | `test_contract` |
| camelCase wire contract with `sceneId`/`planId` aliases | Working | `test_contract` |
| Single error envelope (`detail` **and** `error`) | Working | `test_contract` |
| 3D viewer: real terrain, imagery, boundary, camera | Working | headless-browser render, zero console errors |
| **Real elevation** (5 m Open3Dhk/LandsD terrain tiles, 117 x 148, 3.4–17.3 m) | Working | `terrain_source = open3dhk-tiles`; it is the tile-mesh surface, not a surveyed bare-earth release |
| **Real land cover** (ESA WorldCover 10 m, CC-BY) | Working | 2 tiles, windowed COG read |
| Wet-grid overlay (CN runoff → storage → level-pool fill) | Working, screening-grade | monotonic in rainfall |
| Heat-exposure overlay — a **°C surface-temperature field anchored to observed Landsat LST**, moved off the site mean by land class + sky view + cast shadow + wind shelter | Working, screening-grade | a lone thin tower reads SVF 0.9913; a uniform block reads 0.0. Air is an *estimate* (surface − 12 °C) |
| **Heat anchor** — the cited band that index is read against | Working | HKO Lau Fau Shan, July [26.4, 32.7] °C; reproducible via `fetch_heat_anchor.py --check` |
| **Interventions** — derived sites, plan selection, styled 3D rendering | Working, screening-grade | stated area vs geometry, draft status + provenance |
| **Generative AI** — natural language → grounded 3D edits | **Live.** Local `qwen2.5:3b-instruct` via Ollama, no key; deterministic fallback if absent | real-HTTP round trips replayed on every run |
| **Chat** — ask why, get an answer card whose numbers are checked against the run | **Live.** OpenRouter `:free` model; no key needed to fall back | verified against a real key: 2/2 cards `source: model`, one fully grounded, one flagged a derived 95 % |
| **Streamed steps** — the reader watches the three steps happen | Working | `run_started` → 3 × `step_started`/`step_finished` → `delta` → `grounding` → `answer` → `done` |
| **Number grounding** — every figure in a card traced back to the run | Working | invented "42,000 m³" and "99.9" are caught and named |
| **AI 3D placement** — new massing on real land cover | Removed (stage 1) | the `place` op and procedural generator are deleted |
| **AI 3D alteration, scoped** — height / density / orientation, by region or site | Removed (stage 1) | superseded by real Open3Dhk buildings |
| **3D city model** — real Open3Dhk buildings + terrain, rendered as 3D Tiles | Working | 396 real footprints (`buildings_hsk.json`), heights from tile surface − terrain |
| **Plan Project toggle** — overlay a government case (MTR HSK station GLB) and swap the 3D Tiles mesh for a planning-doc 2.5D massing, hiding boxes under the GLB | Working | 390 boxes drawn, 6 hidden by the station footprint; GLB footprint precomputed by `scripts/compute_plan_project_footprint.py` |
| **Online 3D assets** — glTF fetched from Khronos at runtime | Working | all 3 URLs verified 200 + CORS |
| **Terrain conditioning** — Priority-Flood + D8 flow accumulation | Working | `test_hydrology`; 11.7% of DEM cells are depressions |
| **Design metrics** — height dispersion, site coverage, ground-level void, shade, each tied to a published AVA rule | Working | every metric names its source rule in the response |
| **Wind: least-cost-path corridors** per direction (Dijkstra on a `1 + cellFAD` cost surface) | Working, **geometry not flow** | 8 directions in 0.70 s; corridor cost monotone in FAD |
| **Wind: per-direction FAD ratio** | Working | `1.0` for identical schemes, `>1` where blockage rises |
| **Drainage: D8 fill-and-spill routing + enforced mass balance + HAND** | Working, screening-grade | residual ~1e-15 relative; a deliberately leaked cell makes the assertion **raise** |
| **Scenario evaluation** — three axes at once, with **no combined score** | Working | raising the western blocks 40% taller gives more shade *and* more blockage |
| **Scenario comparison** — ordering under **stated** weights | Working | refuses to rank without weights; the three axes stay visible; `combinedScore` stays `null` |
| Zero-credential operation (no Cesium Ion token) | Working | app boots with empty `.env.local` |

Counts are deliberately absent from this table: the measured number for each suite
lives in [`STATUS.md`](STATUS.md) and nowhere else. Two copies of a count is two
copies that disagree.

**Not working / not built:** PostGIS + MinIO store, a physics engine, a
validated Curve Number table, Hato flood validation, and the ability to
*delete* an individual building — the AI can place blocks and change their
height, density and orientation, not remove them. Two drainage readings
deliberately coexist: the routed cascade and the level-pool headline, which are
not merged because they are different pictures of the same water. See
[Limitations](#limitations-what-this-is-not).

## Tech stack

Chosen for one overriding reason each: **the demo has to run on a laptop, with
no credentials, no Docker and no paid services.**

### Backend — Python 3.13 / FastAPI

| Choice | Why this and not the alternative |
|---|---|
| **FastAPI** | The spec's contract is OpenAPI-first, with validation errors that must carry a FastAPI `detail` array *and* a custom `{"error": {...}}` envelope. FastAPI gives the first for free and Pydantic v2 gives typed request/response models, so a wire contract is enforced rather than hoped for. |
| **Pydantic v2 + pydantic-settings** | `extra="forbid"` on response models is what caught the `sceneId`/`id` bug during the build. Settings come from `.env` with validated, *anchored* paths. |
| **Store mode as configuration** | `STORE_MODE=file` writes JSON to disk; `STORE_MODE=postgis` is the target. Swapping is confined to `app/db.py` + `app/services/scene_service.py` — no route, schema or frontend change. See [ARCHITECTURE Decision 1](docs/ARCHITECTURE.md#decision-1--store-mode-is-configuration-not-architecture). |
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

Docker, PostgreSQL/PostGIS, MinIO, GDAL, `alembic`, a physics engine and any
paid or token-gated API are deliberately not used, each with a stated reason, in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#deliberately-not-used).

---

## Running it locally

> **This guide is tested, not aspirational.** Every step below was run against
> a clean `git archive` export of the repository — a fresh clone with no `.env`,
> no `node_modules` and no caches. `preflight.py` reported *Ready* and both
> overlays regenerated from the committed data. If a step here breaks, that is a
> bug in the repo rather than a surprise for you.

### Prerequisites

| Requirement | Version tested | Notes |
|---|---|---|
| Python | **3.14** | 3.11+ works. Avoid 3.9 — the code uses `X \| None` syntax. `scipy>=1.16.1` needs 3.14 wheels; 1.13 has none. |
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
python -m venv .venv
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

### Step 5b — intervention data

There is no derived-intervention seed. The old `seed_interventions.py` authored its
sites against the retired 786 ha extent, so every polygon fell outside the current
43.5 ha grid and scored exactly zero; it and its output were purged on 14 Oct 2026.
Interventions are authored through the API (`POST /scenes/{id}/ai-plan`) or drawn by
hand, and the baseline plan alone is committed.

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
> `http://localhost:8001` (see `vite.config.ts`). If you see a layer silently
> failing to appear, check that proxy first.

### Step 7 — run the two servers side by side

The backend must be running too. In a second terminal:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8001
```

Confirm it is healthy:

```bash
curl -s localhost:8001/health
```

### Step 8 — verify the whole chain

```bash
SCENE_ID=$(python -c "import json,glob,os;print(os.path.splitext(os.path.basename(glob.glob('data/scenes/*.json')[0]))[0])")

curl -s "localhost:8001/api/scenes/$SCENE_ID"          | head -c 400
curl -s "localhost:8001/api/scenes/$SCENE_ID/terrain"  | head -c 400
curl -s "localhost:8001/api/scenes/$SCENE_ID/plans"    | head -c 400

# Overlays and the DEM raster must be reachable, not just listed.
curl -s -o /dev/null -w "wet grid  %{http_code} %{size_download}b\n" \
  "localhost:8001/static/rasters/wetgrid_hsk.png"
curl -s -o /dev/null -w "dem rgb   %{http_code} %{size_download}b\n" \
  "localhost:8001/static/dem_terrain_rgb.png"

# Interactive API docs
open http://localhost:8001/docs
```

You should see the HSK/HT area with real relief, and toggling **Wet grid** and
**Heat exposure** should paint over the study area.

### Step 9 — run the tests

```bash
cd backend
python scripts/preflight.py                    # environment; exits non-zero if not runnable
for f in tests/test_*.py; do                   # exactly what CI runs
  if grep -q __main__ "$f"; then PYTHONUTF8=1 python "$f"; else PYTHONUTF8=1 python -m pytest -q "$f"; fi
done

cd ../frontend
npx tsc --noEmit                               # must be clean
npm run lint && npm test                       # eslint --max-warnings 0, vitest
```

The backend suites (`backend/tests/`) are mostly standalone scripts that print `N/N passed` and exit
non-zero on any failure; `test_overlay_masking` is pytest-style with no `__main__`.
**`pytest tests/` on its own is not enough:** it collects nothing from the
standalone scripts, so it skips most of the suites and still reports green. The
loop above runs each file the way it was written, and it is the loop
`.github/workflows/ci.yml` runs.
**The counts are in
[`STATUS.md`](STATUS.md)** — one table, measured, and the only place they are
written down.

### Run it all at once (optional)

```bash
# terminal 1
cd backend && python -m uvicorn app.main:app --reload --port 8001

# terminal 2
cd frontend && npm run dev
```

---

## Architecture

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

Four decisions tell you what kind of tool this is before you read any code. The
argument for each, and the alternative that was rejected, is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — sixteen numbered decisions, the
single home for design rationale.

- **Store mode is a switch, not a rewrite.** The target is PostGIS + MinIO; this
  host has neither, so the store is JSON on disk with the boundary at exactly two
  modules. No route, schema or frontend code knows which store is active.
- **Honesty is a feature, not a disclaimer.** Every raster writes a `.json`
  sidecar listing its constants and sources, and a failed overlay is reported in
  the panel instead of swallowed. Three separate bugs presented as "the toggle
  does nothing", and two were invisible to the type checker — making failure
  visible is what turned a guess into a diagnosis.
- **Rasters are generated artefacts, not source.** No PNG is a source of truth;
  each is reproducible, and `fetch_heat_anchor.py --check` is a drift gate that
  fails if the committed file stops agreeing.
- **Three axes, never one score — and the tool refuses to grade.** TC 1/06 §6
  gives no absolute benchmark, so nothing prints a pass or fail: `verdict` is
  `null`, `combinedScore` is `null`, and `scenario/compare` refuses to rank
  without weights. The units are incommensurable and the axes genuinely conflict
  — raising the western blocks 40% taller gives more shade *and* more wind
  blockage at once — so a single number would settle that trade-off silently.

- **Why it is built this way** — the sixteen decisions, in depth:
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Which module does what, and the layer rules** —
  [`CLAUDE.md`](CLAUDE.md#layer-responsibilities) (backend) and
  [`docs/FILE_STRUCTURE.md`](docs/FILE_STRUCTURE.md) (frontend).

### API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `POST` | `/api/scenes` | Create a scene from a study area |
| `GET` `PUT` `DELETE` | `/api/scenes/{sceneId}` | Read, update, or delete a scene — **delete cascades** to its plans and interventions |
| `GET` | `/api/scenes/{sceneId}/terrain` | Terrain + imagery + overlay URLs, bbox, centre |
| `GET` | `/api/scenes/{sceneId}/plans` | Plans under a scene |
| `PUT` | `/api/plans/{planId}` | Create or update a plan |
| `GET` | `/api/plans/{planId}/interventions` | Interventions as a GeoJSON `FeatureCollection` |
| `POST` | `/api/scenes/{sceneId}/ai-plan` | **Natural language → a draft plan** (the spec's reserved AI slot) |
| `POST` | `/api/scenes/{sceneId}/run` | Apply the active plan and return baseline-vs-scenario stats + re-rendered overlays |
| `POST` | `/api/scenes/{sceneId}/chat`, `/chat/stream` | Grounded answer card; the streamed variant sends SSE step events |
| `GET` | `/api/scenes/{sceneId}/ledger` | The buffer deficit, and what stored interventions do to it |
| `GET` | `/api/scenes/{sceneId}/sea-level` | Official HKO 2100 projections run through the ledger |
| `GET` | `/api/scenes/{sceneId}/scenarios` | Three published CEDD development programmes, encoded |
| `GET` | `/api/scenes/{sceneId}/design-metrics` | Geometry metrics, each tied to a published AVA rule |
| `GET` | `/api/scenes/{sceneId}/ventilation` | Frontal area density per wind direction |
| `GET` | `/api/scenes/{sceneId}/ventilation/corridors` | Least-cost-path ventilation corridors, per direction |
| `GET` | `/api/scenes/{sceneId}/heat-field` | The blended heat field, sky view, and the cited anchor band |
| `GET` | `/api/scenes/{sceneId}/drainage/routed` | D8 fill-and-spill routing, mass balance and HAND |
| `GET` | `/api/scenes/{sceneId}/drainage/hourly` | Hourly storm simulation — rain and drainage per hour, cited reference storms, an enforced whole-simulation mass balance, and the DSD 134 mm/h design standard as the stated capability proxy |
| `POST` | `/api/scenes/{sceneId}/scenario/evaluate` | Three separate axis deltas — and **no combined score** |
| `POST` | `/api/scenes/{sceneId}/scenario/compare` | Order scenarios **only under stated weights**; refuses without them |

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
cannot quietly erode. ([Decision 11](docs/ARCHITECTURE.md#decision-11--the-model-selects-sites-it-never-emits-coordinates).)

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

Every feature is `source="ai"` and `status="draft"` — the engine refuses to
compute on draft geometry, which is the correct posture for machine-proposed
sites. Generated features travel the **same** validate → store → render path as
hand-drawn ones, which §3.3.2 requires ("用户绘制与 AI 方案落地共用同一套管线"),
so nothing downstream special-cases AI output.

The LLM path is exercised on every test run through an injected transport that
replays a recorded response, so it is not untested code waiting for a key.

### Chat: an answer you can audit

`POST /api/scenes/{sceneId}/chat` answers a question **about a run that already
happened**, and `/chat/stream` streams it. Three things make it trustworthy
rather than merely fluent:

1. **It explains, it does not compute.** The run's own numbers are handed over as
   a FACTS block. Nothing is recalculated inside a Q&A — recomputing would turn a
   fast reply into a full simulation *and* let the answer drift from the numbers
   on screen. ([Decision 16](docs/ARCHITECTURE.md#decision-16--the-chat-explains-a-run-it-never-computes-one).)
2. **The answer is a card, not a paragraph.** Title, one-sentence answer, cause,
   next step, stats, confidence with a reason, caveats, follow-ups. The same
   fields are always present, so a cause the numbers do not support shows as
   `cause: null` instead of as prose that quietly omits it.
3. **Every number is checked by code, not by the model.** `grounding.py` traces
   each figure back to the run, at any scale or rounding. A card that fails is
   sent back once with the offending numbers named, and if it still fails the
   card is shown **with** the warning rather than without it.

With no model configured the identical card is assembled by code from the run's
own figures and labelled `source: "template"`. That is not a degraded mode to
hide — every number in it is one the run actually produced, which is more than a
hallucinated answer can claim.

**Configuration** (in `backend/.env`). `ANTHROPIC_API_KEY` wins over the router,
so leave it empty unless Anthropic is what you want:

```ini
ANTHROPIC_API_KEY=
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-...
LLM_MODEL=google/gemma-4-31b-it:free
LLM_JSON_MODE=auto                                # off if the model rejects response_format
LLM_FALLBACK_MODELS=nvidia/nemotron-3-ultra-550b-a55b:free
```

`LLM_FALLBACK_MODELS` matters because a free tier is a shared pool: a `:free`
model throttled upstream is the normal case, not an emergency. Measured latency
and the throttling behaviour are in [`STATUS.md`](STATUS.md) (gap 10).

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
curl -s localhost:8001/health | grep -A6 '"llm"'      # isAi: true
```

`llm_probe.py` never prints a key — only whether one is set and its length. It
validates the configured model id against `GET /v1/models` rather than trusting a
hard-coded string, so a rotated id is caught before the demo rather than during
it.

A small local model also forced two structural changes, both recorded in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#decision-15--verify-the-reference-implementation-then-reimplement-if-it-will-not-install):
tool routing is decided **in code**, not by the model (a 3B model asked with two
tools available returned no tool call at all and the feature vanished silently),
and the OpenAI-compatible path falls back to JSON-schema-constrained output
because tool-calling is not universal. Decoding is pinned to
`temperature: 0` — at the default the same sentence gave different answers on
successive runs, and a demo must show the plan you rehearsed.

### Try it

```bash
cd backend
python scripts/ai_plan.py "add two water reservoirs and a green space" --dry-run
python scripts/ai_plan.py "add three 60 m residential towers in the east"
python scripts/ai_plan.py "make the western blocks 30% taller"
python scripts/ai_plan.py "rotate the northern blocks 20 degrees"
python scripts/llm_probe.py
```

**What it does not do:** it does not reason about flood hydraulics, and it does
not design a district. It places *interventions* and it places and reshapes
*buildings* — five operations, all grounded in the analysis. It cannot invent a
footprint, and it cannot move a block.

## 3D city model, and editing it

The sandbox renders the **real Open3Dhk buildings and terrain** as 3D Tiles
(`buildings3DUrl` → `city_model/tileset.json`, terrain via `terrain(TB)/tileset.json`).
There is no procedural massing: the 2,639-square `citymodel_hsk.json` placeholder
and its generator `make_citymodel.py` were removed in stage 1.

**Where the geometry comes from.** Footprints come from **Overture Maps** (369 kept)
fused with **27 tile-derived blobs** — cells where the Open3Dhk tile surface stands
≥7 m above the tile terrain but Overture recorded nothing (mostly trees/sheds are
filtered out at <40 m²). Building height is the **90th percentile of tile surface
minus tile terrain** inside each footprint, so it is measured from the real tiles,
not an assumed distribution. The provenance is recorded in `buildings_meta.json`
(`status: DERIVED_FROM_OPEN3DHK_AND_OVERTURE`). The footprint GeoJSON
(`buildings_hsk.json`) is the *analysis* contract that heat, shade, SVF and wind
read; the `.b3dm` meshes are the *render*.

**Editing.** Building geometry is not hand-editable in this stage, and the
natural-language path cannot add a building: `place` is no longer in the parser or
the tool schema. Interventions become land-cover and storage changes (stage 3), not
new massing. `scenario.apply_massing` can still *score* a `place` op handed to it
directly through `POST /scenario/evaluate`, but no instruction reaches it — the
front door is closed and the capability is dormant.

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

Full provenance, the ESA→our-class mapping, control points and the rejected
sources are in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

### The wet grid, in one paragraph

A **screening view, not a flood prediction**. SCS-CN runoff depth per cell from
rainfall and the class Curve Number (hydrologic soil group D, assumed); then
retention subtracted — land cover sets how much *runs off*, explicit storage sets
how much can be *held*, and **no cell is counted in both**; then a level-pool
solve by bisection for the water-surface elevation that holds the remainder, so
the picture is a pond rather than a uniform smear and the printed volume matches
the picture. Verified monotonic in rainfall. The **headline deficit**
(`bufferDeficitM3`) comes from this path; a separate D8 fill-and-spill cascade
exists as an **additive** screening surface and does not replace or correct it.
The reasoning and the citations are in
[`docs/research/ch03_drainage.md`](docs/research/ch03_drainage.md).

### The heat overlay, in one paragraph

An observed Landsat LST scene anchors the site mean (41.26 °C, scene
2022-09-03); land class + sky view + cast shadow + wind shelter then move each
cell **in °C away from that mean**. The output is a screening surface (skin)
temperature field, and its sidecar says so:
`SURFACE_TEMPERATURE_ANCHORED_TO_LST`.
It is **not** a calibrated air-temperature measurement. Air temperature is an
*estimate* (surface − 12 °C), never a modelled field.
It is also read against a cited band from committed data
(`backend/data/reference/heat_anchor.json`): **26.4 °C ↔ 32.7 °C**, the mean daily
min/max over July 2015–2024 at HKO **Lau Fau Shan**. That is a stated band for a
Hong Kong summer, ~20 km away, not a measurement of this site; it is *air*
temperature, never skin. Cross-checked against the HKO 1991–2020 normals and the
ERA5 archive at the site centroid, neither of which is the anchor.
`--heatwave` is a *display* stretch, not a scenario. Detail in
[`docs/research/ch01_urban_heat.md`](docs/research/ch01_urban_heat.md) and
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) §4.

### Both scripts register themselves

Each script attaches its output to the seeded scene, so `GET /terrain` starts
returning real URLs and the viewer switches light up.

**The sliders do not trigger a recomputation.** They write `envParams` into the
frontend store only; re-running the script is a manual step. The panel says so.

---

## Testing

```bash
cd backend
PYTHONUTF8=1 python -m pytest tests/     # or: for f in tests/test_*.py; do python "$f"; done
cd ../frontend && npx tsc --noEmit && npm run lint && npm test

# Browser suites, run with both servers up (they exit 2 if the servers are missing):
#   npm i --no-save playwright && npx playwright install chromium
node scripts/verify-3d.mjs && node scripts/verify-ux.mjs
```

The backend suites (`backend/tests/`) are mostly standalone scripts that print `N/N passed` and exit
non-zero on any failure; `test_overlay_masking` is pytest-style with no `__main__`,
which is why `pytest tests/` above is the recommended form. **The per-suite counts and
what each one covers live in [`STATUS.md`](STATUS.md)**, which is the only place they
are written down; a count repeated in a second file is a count that goes stale in it.

What this suite exists to catch, in one example: the source documents name
constants that *fail silently*. Written as `S = 25400/CN − 2540` the retention
term goes negative above CN ≈ 91, which makes the denominator of `Q` negative —
and the first run reported **5.4 × 10¹⁸ m³** of runoff while rendering a
perfectly plausible image. The correct form is `−254`, now pinned against the
TR-55 worked example. The same instinct guards the DEM: the committed
`dem_terrain_rgb.png` must decode back to `dem_hsk.npy` within terrain-RGB's own
0.1 m quantum, because the Python encoder and the browser decoder are two
implementations of one formula in two languages. History in
[`CHANGELOG.md`](CHANGELOG.md).

**A clean `tsc` did not mean the frontend worked.** Four runtime bugs passed the
type checker, including a `tsconfig` path alias that TypeScript resolved and Vite
did not. Frontend changes here are verified by driving a real headless-Chromium
render and asserting what the *viewer* reports, not
by the compiler and not by what the backend was asked for.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Overlay switches are greyed out | The scene has no `wetGridUrl` / `heatmapUrl` | Run `make_wetgrid.py` and `make_heatmap.py`, then reload the scene |
| Switch is enabled but nothing appears | The PNG never reached the backend | Check `STATIC_BASE_URL=/static` in `backend/.env` **and** that `/static` is in the Vite proxy |
| "An overlay failed to load" in the panel | Image failed to decode, or the request 404'd | Open the console; `curl` the URL from `/terrain` against port 8001 |
| Blank globe, "Cesium is not defined" | Cesium's static assets not copied | Ensure `vite-plugin-cesium` is in `vite.config.ts` plugins |
| `401`/`403`-looking globe, or Bing imagery missing | Cesium Ion token expected | Leave `VITE_CESIUM_ION_TOKEN` empty; the app sets `baseLayer: false` |
| Terrain is flat | `terrainUrl` is null | Run `tiles_to_rasters.py`; the script registers the raster on the scene. (`fetch_dem.py` still exists for the Open-Meteo fallback, but the shipped DEM is the 5 m Open3Dhk tiles) |
| `fetch_dem.py` exits with HTTP 429 | The free Open-Meteo API throttles on request frequency | Just re-run it — every batch is cached, so it resumes where it stopped |
| `ModuleNotFoundError: rasterio` | Deps not installed in the active venv | `pip install -r requirements.txt`; check `which python` |
| Two Python versions fighting | A stale `backend/.venv` (3.10) lacks numpy and rasterio | Use one interpreter; delete the stale venv or activate the right one |

> **Known environment wart:** a stale `backend/.venv` (3.10, missing numpy and
> rasterio) can shadow the interpreter you think you are using. Only the managed
> 3.14 resolves the raster stack. `scripts/preflight.py` reports which interpreter
> it is running under and exits 1 if the raster stack is unavailable, so run it
> before debugging anything else. On Windows also set `PYTHONUTF8=1`, or three
> suites die on a cp950 decode error before running a check.

---

## Project layout

```
T1/
├── README.md                     ← you are here: what it is, how to run it
├── STATUS.md                     what actually works, measured, + the live gap list
├── CHANGELOG.md                  every non-trivial change, newest first
├── CLAUDE.md                     repo conventions and layer rules for AI agents
├── docs/
│   ├── ARCHITECTURE.md           the 16 design decisions, in depth
│   ├── DATA_SOURCES.md           provenance, licences, rejected sources
│   ├── RUNG_AND_BUSINESS.md      rung declaration + business model
│   ├── FILE_STRUCTURE.md         frontend module map
│   ├── research/                 the five cited research chapters
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
│   │   ├── agent/                the chat: llm client, answer card, grounding
│   │   └── routers/              HTTP only
│   ├── scripts/
│   │   ├── preflight.py          is this machine able to run it? exits 1 if not
│   │   ├── seed_scene.py         create the scene from the locked study area
│   │   ├── tiles_to_rasters.py   Open3Dhk tiles → 5 m DTM + flow rasters
│   │   ├── build_buildings.py    Open3Dhk + Overture → buildings_hsk.json
│   │   ├── build_landclass.py    WorldCover + trees + buildings → land class
│   │   ├── fetch_landcover.py    ESA WorldCover → landclass_hsk.npy
│   │   ├── fetch_dem.py          Open-Meteo → dem_hsk.npy (+ terrain-RGB)
│   │   ├── _overlay_common.py    shared grid, land classes, CN, resampling
│   │   ├── make_wetgrid.py       CN runoff → storage → level-pool fill
│   │   └── make_heatmap.py       LST-anchored surface temperature → anomaly RGBA
│   ├── tests/                    standalone suites — counts in STATUS.md
│   ├── data/                     the file store (see docs/FILE_STRUCTURE.md)
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── api/                  fetch wrapper + one module per resource
    │   │   └── chatApi.ts        postChat + streamChat (SSE over fetch)
    │   ├── state/                Zustand: sceneStore, planStore
    │   ├── theme.ts              one MUI dark theme; signal colours shared
    │   │                         with the 3D tactical renderer
    │   ├── view/
    │   │   ├── SandboxViewer.tsx owns the Cesium Viewer
    │   │   ├── cameraRig.ts      bbox framing, orbit, zoom, gesture bindings
    │   │   ├── terrainLoader.ts  imagery + terrain binding, with honest fallback
    │   │   ├── demProvider.ts    terrain-RGB → CustomHeightmapTerrainProvider
    │   │   ├── tilesetLayer.ts   the real Open3Dhk 3D Tiles layer
    │   │   ├── viewModes.ts      survey / tactical scene dressing
    │   │   └── interventionLayer.ts  discrete features (extrusion)
    │   ├── components/           ChatPanel, HudPanel, StatsPanel, SlidersPanel,
    │   │                         ProjectCatalog, ViewButtons, SceneNotice,
    │   │                         SliderField, LoadingScreen, AppErrorBoundary
    │   └── App.tsx  main.tsx  index.css
    ├── scripts/                  _verify-common.mjs + verify-3d / verify-ux
    ├── vite.config.ts            @ alias + /api, /static and /health dev proxies
    ├── .env.local.example
    └── package.json
```

`backend/data/` is a **runtime store** with two committed fixtures, so a fresh
clone runs without fetching anything; everything else under it is residue from
running the suites or poking the API. Which files are committed, why a raster is
a render rather than data, and what is safe to delete are stated once, in
[`docs/FILE_STRUCTURE.md`](docs/FILE_STRUCTURE.md) and the annotations in
`.gitignore`.

---

## Limitations: what this is not

Stated plainly, because the specification asks for a tool that does not
overclaim, and because a plausible wrong number is worse than no number.

**Not a flood model.** The wet grid allocates water by level, not by routing, and
that level-pool path still produces the headline deficit. A D8 fill-and-spill
cascade and a HAND cross-check now exist as an **additive screening surface**,
but they are screening-level, they do not replace the level-pool headline, and
they are not a flood prediction. No tide boundary — the Hong Kong 0.146 m
mCD↔mPD datum offset is a named constant with no tide series behind it yet.

**The Curve Number table is unverified.** Hydrologic soil group D is *assumed*
for HSK/HT and has not been independently checked. Runoff volume scales directly
with CN, so this is the single assumption most worth attacking next.

**The study-area extent is provisional.** A machine-derived operational
rectangle, not the gazetted NDA boundary.

**The heat overlay is a screening °C field, not a measurement.** It is anchored to
an observed Landsat LST scene and to a cited HKO air-temperature band (Lau Fau
Shan, July [26.4, 32.7] °C), but that band is a stated figure for a Hong Kong
summer, not a measurement of this site. The geometry terms (land class, sky view,
shade, wind shelter) move cells in °C away from the site mean. Air temperature is
an *estimate* (surface − 12 °C), not a modelled field.
`--heatwave` is a display transform, not a scenario.

**The land cover is a single 2021 epoch.** It does not show change since then,
which is a real gap for an area under construction.

**Vertical datum is unhandled.** Open-Meteo returns orthometric heights (mPD).
No vertical datum shift, no LiDAR, no bare-earth correction — and if vegetation
or structures bias elevation upward, a *deficit* estimate is biased too.

**No physics engine, no PostGIS.** Both out of scope for this phase and both
named in the task plan.

**The chat cannot invent a number the run did not produce — but it can still be
wrong about what the number means.** Grounding checks arithmetic, not
interpretation. The chat also holds no thread (a reload loses the conversation)
and never recomputes a scenario.

**The city model is massing, not buildings,** and the AI's suggestions are
scenarios, not designs. See [3D city model](#3d-city-model-and-editing-it).

**Everything still open, ordered by what would hurt a user first, is in
[`STATUS.md`](STATUS.md)** — that list is the single backlog, and it is not
repeated here.

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
