# CLAUDE.md

Guidance for AI agents working in this repository.

> **Read [`README.md`](README.md) first** — it has the setup guide, tech stack
> and troubleshooting. This file is the conventions and the "don't get this
> wrong" list.

## Project

A 3D geospatial sandbox for flood and heat-risk scenarios in Hong Kong. The
current phase delivers the chain **geographic data → storage → scene management
API → CesiumJS 3D frontend**. Physics, AI modules and PostGIS are out of scope
for now.

Study area: **Hung Shui Kiu / Ha Tsuen (HSK/HT)**, an analysis window of ~43.5 ha
cropped to the Open3Dhk tile coverage on 13 Oct 2026. The 27-task project
plan points at Deep Bay; that conflict was resolved on 6 Oct 2026 in favour of
HSK/HT, which has the stronger public evidence base. Do not silently switch.

## Tech stack — as actually built

**Backend:** Python 3.13 · FastAPI · Pydantic v2 · numpy · Pillow · rasterio
**Frontend:** Vite · React 18 · TypeScript · CesiumJS 1.146 · Zustand · MUI

**Storage is JSON files, not PostGIS.** `STORE_MODE=file`. The documented target
is PostGIS + MinIO; the seam is `app/db.py` + `app/services/scene_service.py`
only, so that migration is a swap rather than a rewrite. **No SQLAlchemy, no
alembic, no migrations directory** — the spec references one, it does not exist,
and this file says so rather than faking it. Decision 1 in
`docs/ARCHITECTURE.md` has the full argument.

**No Cesium Ion token.** The Viewer sets `baseLayer: false` and supplies
OpenStreetMap explicitly, so the app runs with zero credentials. Leave
`VITE_CESIUM_ION_TOKEN` empty.

## Commands

```bash
# Backend. Run from backend/. There is no committed venv on this host -- it uses
# the system Python (3.14), and `pip install -r requirements.txt` is the setup.
cd backend
python scripts/preflight.py                  # environment check; exits 1 if not runnable
python scripts/fetch_landcover.py            # ESA WorldCover -> landclass_hsk.npy (needs rasterio)
python scripts/fetch_dem.py                  # Open-Meteo -> dem_hsk.npy
python scripts/tiles_to_rasters.py           # Open3Dhk tiles -> 5 m DTM + flow rasters
python scripts/build_buildings.py            # Open3Dhk + Overture -> buildings_hsk.json
python scripts/build_landclass.py            # fuse WorldCover + trees + real buildings
python scripts/seed_scene.py                 # prints the sceneId
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
python scripts/ai_plan.py "add two water reservoirs"   # NL -> draft plan (GenAI)
python -m uvicorn app.main:app --reload --port 8001   # :8001 -- vite.config.ts proxies here

# Every suite is a standalone script, not a pytest run. On Windows set PYTHONUTF8=1,
# or three suites die on a cp950 decode error before running a check. For the counts
# and what each one covers, read STATUS.md -- do not trust a number typed into a comment.
PYTHONUTF8=1 python -m pytest tests/          # or: for f in tests/test_*.py; do python "$f"; done

# Frontend
cd frontend && npm install && npm run dev    # :5173
npx tsc --noEmit                             # must be clean
npm run lint && npm test                     # eslint --max-warnings 0, vitest
# Browser suites: need BOTH servers up. They exit 2 if the servers are missing,
# so a missing server can never read as a pass. Setup once:
#   npm i --no-save playwright && npx playwright install chromium
node scripts/verify-3d.mjs && node scripts/verify-ux.mjs
```

`seed_interventions.py` and `make_citymodel.py` are gone: the derived-intervention
seed was authored against the retired 786 ha extent and was purged, and the
procedural massing generator was replaced by `build_buildings.py`.

Test counts move. `STATUS.md` carries the measured ones; if you add a check,
update that table in the same commit rather than editing the inline counts in
this file or `README.md`, which is how both went stale.

`cp backend/.env.example backend/.env` on first run. `STATIC_BASE_URL=/static` is
the one setting with no safe default.

## Architecture

### Principles

1. **Single data authority.** The frontend never touches the filesystem or a
   database. Everything goes through the API.
2. **GeoJSON pass-through.** EPSG:4326 everywhere on the wire -- geometry <->>
   GeoJSON <-> Cesium, no coordinate conversion at any layer.
3. **Degrees on the wire, metres in the maths.** Exactly one function converts:
   `build_grid()` in `app/services/geo_import.py`. Nothing else converts units.
4. **Scene as the entry anchor.** The viewer boots from
   `GET /api/scenes/{sceneId}` -> `center / bbox / terrainUrl / imageryUrl /
   defaultCamera`.

### Data flow

```
open data (fetch_*.py)  ->  backend/data/*.npy, *.png
  -> scene record (scene_hsk.json -> POST /api/scenes -> sceneId)
    -> frontend: GET /api/scenes/{sceneId}
      -> Cesium Viewer (terrain + imagery + overlays)
        -> GET /api/scenes/{sceneId}/plans
          -> GET /api/plans/{planId}/interventions -> GeoJSON FeatureCollection
        -> POST /api/scenes/{sceneId}/run -> baseline-vs-scenario stats + overlays
          -> POST /api/scenes/{sceneId}/chat/stream -> SSE: steps, deltas, card
```

The chat is a **leaf**: it consumes a run result the client already holds and
returns text. It holds no state and writes nothing.

### Layer responsibilities

| Layer | Path | Role |
|---|---|---|
| Routers | `app/routers/` | HTTP only. Parse, delegate, serialise. |
| Schemas | `app/schemas/` | The wire contract. `RowModel` translates storage <-> wire. |
| Services | `app/services/` | Business logic: cascade, bbox repair, URL resolution. |
| Agent | `app/agent/` | The chat. Explains a run result; **never** recomputes one. |
| Store | `app/db.py` | The **only** module that knows storage is files. |
| Config | `app/config.py` | Settings; paths anchored to `backend/`, never cwd. |
| Raster scripts | `scripts/` | Fetch + analysis. Attach results to a scene. |

Frontend: `src/view/` is **rendering only** -- it reads state and never fetches.
`src/components/` triggers state changes and holds no rendering logic. Data flows
API -> Zustand -> View.

## API contract

```
GET    /health
POST   /api/scenes
GET    /api/scenes/{sceneId}
PUT    /api/scenes/{sceneId}
DELETE /api/scenes/{sceneId}                # cascades to plans + interventions
GET    /api/scenes/{sceneId}/terrain        # + bbox, center, overlay URLs
GET    /api/scenes/{sceneId}/plans
PUT    /api/plans/{planId}
GET    /api/plans/{planId}/interventions    # GeoJSON FeatureCollection
```

There is **no** `POST /api/scenes/{sceneId}/plans` -- plans are created with
`PUT /api/plans/{planId}`. An earlier version of this file claimed otherwise.

Errors: 422 for validation, 404 with `{"detail": ...}`. **Both** a FastAPI
`detail` array and `{"error": {"code", "message"}}` are emitted, because the spec
mandates both. The frontend has one error branch.

## Storage shape

`scene -> plan -> intervention`, with a cascade delete chain.

`backend/data/{scenes,plans,interventions}/` is a **runtime store, not source.**
Only two files in it are committed — the seeded scene and the baseline plan — and
`.gitignore` allowlists exactly those.
Everything else is residue, from hand-poking the API. The suites that create
records now redirect `settings.*_dir` at a temp copy of `data/`
(`backend/tests/_tmpstore.py`, used by `isolate_store()`), so running them no
longer adds to it. **If you add a suite that creates records, call
`isolate_store()` first** — the residue is invisible until someone counts the
files, and it is not only clutter: a loose *plan* makes a loose intervention read
as live, which is how the ledger's "no shipped seed is over-large" check came to
fail for the store rather than for the code.

- **scene** -- geographic anchor. `center`, `bbox`, `terrain_url`,
  `imagery_url`, `default_camera`, `env_params`, plus `wet_grid_path` /
  `heatmap_path` for the §4.1 reserved overlay fields.
- **plan** -- `plan_type` in `{baseline, economic, environmental, comprehensive,
  custom}`, `source` in `{manual, ai}`. The last two plan types are legal but
  nothing creates them; noted in `models/plan.py`.
- **intervention** -- GeoJSON geometry, `params`, `style`, `status`.

Storage keeps the spec's `id` column; the API exposes `sceneId` / `planId` as an
**alias**, translated in `RowModel.from_row`. Do not rename the column.

## Raster data

Rasters are **generated artefacts, not source**. Reproduce with:

```bash
python scripts/tiles_to_rasters.py           # Open3Dhk tiles -> 5 m DTM + flow rasters
python scripts/build_buildings.py            # Open3Dhk + Overture -> buildings_hsk.json
python scripts/build_landclass.py            # WorldCover + trees + buildings -> landclass
python scripts/fetch_landcover.py            # the ESA base, needs rasterio
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
```

The DTM is the **real 5 m Open3Dhk/LandsD surface** (117 x 148, 3.4-17.3 m), not a
~90 m global model. It is the terrain-tile mesh surface, not a surveyed bare-earth
release. A run re-solves the water level on the conditioned surface but does not
re-condition it, so a DEM change needs `make_wetgrid.py` re-run.

No MinIO, no `gdal_translate`, no COG pipeline. Elevation reaches Cesium as a
**mapbox terrain-RGB** raster via `CustomHeightmapTerrainProvider`
(`src/view/demProvider.ts`). Overlays are `SingleTileImageryProvider` images over
the bbox rectangle.

Every raster writes a `.json` sidecar with its constants and assumptions, and
registers itself against the seeded scene.

## Conventions that will bite you

- **Run `scripts/preflight.py` before debugging anything.** Most failures here
  are environmental, not code: missing `numpy`/`rasterio`/`scipy` looks exactly
  like a broken script. On Windows, `PYTHONUTF8=1` too, or three suites die on a
  cp950 decode error.
- **Verify data sources against control points.** The AWS
  `elevation-tiles-prod` bucket returns HTTP 200 with non-terrarium pixels that
  decode Everest to ~1,000,000 m. `fetch_dem.py` asserts Everest and the open
  ocean before writing. Do not remove those assertions.
- **Measure a DEM's real resolution; do not infer it from the request.** The
  Open-Meteo API serves a ~90 m global model however finely you sample it.
  Asking for a 1 arc-second lattice over a 2.4 km extent returned a 78 x 110
  array containing only 28 x 37 distinct samples. Finer sampling duplicates,
  it does not resolve. `dem_hsk.json` records the measured posting.
- **Check for tile-boundary nodata.** The study area straddles 114 deg E, so
  WorldCover needs two tiles. Nodata silently filled with a default class
  fabricated 167 ha of "paved" ground.
- **`STATIC_BASE_URL` must match the `/static` mount**, and `/static` must be in
  the Vite dev proxy. Get it wrong and overlays fail silently.
- **A clean `tsc` does not mean the frontend works.** `tsconfig.json` declares a
  `@/*` alias that `vite.config.ts` must also register; TypeScript resolves it
  and Vite does not. Verify frontend changes in a real browser render.
- **A derived feature's geometry must match its stated size.** An early
  intervention seeder drew a polygon for a connected component's *centroid
  cell* while reporting the whole component's area, so a "155 ha" feature was
  0.003 ha and invisible. `test_constants.py` asserts the two agree.
- **The buildings are measured geometry, not generated massing.** The 396
  footprints come from Open3Dhk tiles fused with Overture (`buildings_hsk.json`,
  `buildings_meta.json`), and heights are the 90th percentile of tile surface
  minus tile terrain. Never describe it as surveyed storeys, and keep the
  provenance disclosure. No generated mesh is ever presented as what is there now.
- **The natural-language front door does not create geometry.** `place` is not in
  the parser, the tool schema or `_massing_feature`; a model that emits one is
  refused. `scenario.apply_massing` still *scores* a `place` op handed to it
  directly, but nothing reaches it from an instruction.
- **richdem/hydrotools will not install here** (richdem needs GDAL headers;
  hydrotools gates everything behind it). The algorithms live in
  `scripts/hydrology.py`. Do not try to add them as dependencies.
- **A raster PNG is a render, not data.** The wet grid's alpha saturates at
  0.30 m while the wet area averages ~1.5 m, so anything analysing depths must
  read `wetgrid_hsk.npy`, not `wetgrid_hsk.png`. Same for the DEM (`.npy`) and
  land cover (`.npy`).
- **The AI module never invents geometry.** The model chooses among candidate
  sites derived from the DEM/land cover/wet grid; coordinates are attached
  afterwards. If grounding is unavailable it refuses. Do not "improve" this by
  letting the model emit coordinates.
- **Ask for `provider=llm` without a key and it must 422**, not silently fall
  back. A demo that quietly stops being AI is worse than one that says so.
- **Derive constants, never hand-type them.** The bbox came from machine-retrieved
  OSM anchors recorded in `scene_hsk.json`, not from memory.
- **`ANTHROPIC_API_KEY` wins over `LLM_BASE_URL`.** `agent/llm.py` checks
  Anthropic first, so a leftover key there silently disables an OpenRouter /
  Ollama setup. Leave it empty unless Anthropic is what you want.
- **Free-tier models fail in three specific ways, all already handled.** They
  reject `response_format` (→ `LLM_JSON_MODE=off`), they reject `stream: true`
  *or* stream zero `content` deltas because every token went to the reasoning
  channel (→ one unstreamed retry), and they run out of `max_tokens` while
  thinking (→ `MAX_TOKENS=2000`; 900 was measured to truncate the card
  mid-object). If a card comes back as `source: "template"`, check step 2's
  result line before assuming the model is broken.
- **A card that parses to nothing is a 200, not an error.** `finish_reason:
  length` returns valid JSON with nothing in it. `_parse_card` returns `None`,
  which the UI reports as "the model did not return a card".
- **Guard every Cesium `Viewer` touch with `isLive()`.** StrictMode destroys the
  viewer in one effect's cleanup while sibling cleanups still hold a reference.

## Where things are written down

| Question | File |
|---|---|
| How do I run this? | [`README.md`](README.md) |
| What actually works, measured? | [`STATUS.md`](STATUS.md) |
| Why is it built this way? | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Where did the data come from? | [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) |
| Why does the physics look like this? | [`docs/research/`](docs/research/README.md) |
| What did we declare, and how does it pay? | [`docs/RUNG_AND_BUSINESS.md`](docs/RUNG_AND_BUSINESS.md) |
| What does the spec say? | `docs/技术文档.docx`, `docs/_技术文档_clean.txt` |

**One home per fact.** `STATUS.md` is the only live statement of what works.
`ARCHITECTURE.md` is the only home for design rationale. `CHANGELOG.md` is the
only history. Do not restate a fact in a second file — a claim repeated in two
places is a claim that will disagree in one of them, which is exactly how the
test counts, the stage table and the progress log all went stale before being
merged on 12 Oct 2026.

## Git

Feature branches: `feature/scene-management`, `feature/sandbox-3d-visualization`.
The REST contract is the integration boundary; merge the backend first.

This prototype is **not engineering-grade and must not overclaim**. Every
generated figure is a screening estimate. Keep the UI labels and the sidecar
metadata honest -- that is a requirement, not politeness.
