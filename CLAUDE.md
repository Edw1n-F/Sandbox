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

Study area: **Hung Shui Kiu / Ha Tsuen (HSK/HT)**, ~786 ha. The 27-task project
plan points at Deep Bay; that conflict was resolved on 6 Oct 2026 in favour of
HSK/HT, which has the stronger public evidence base. Do not silently switch.

## Tech stack — as actually built

**Backend:** Python 3.13 · FastAPI · Pydantic v2 · numpy · Pillow · rasterio
**Frontend:** Vite · React 18 · TypeScript · CesiumJS 1.146 · Zustand · MUI

**Storage is JSON files, not PostGIS.** `STORE_MODE=file`. The documented target
is PostGIS + MinIO; the seam is `app/db.py` + `app/services/scene_service.py`
only, so that migration is a swap rather than a rewrite. **No SQLAlchemy, no
alembic, no migrations directory** — the spec references one, it does not exist,
and it is flagged in `docs/OCT6_STATUS.md` rather than faked.

**No Cesium Ion token.** The Viewer sets `baseLayer: false` and supplies
OpenStreetMap explicitly, so the app runs with zero credentials. Leave
`VITE_CESIUM_ION_TOKEN` empty.

## Commands

```bash
# Backend -- always activate the venv first; there are two Pythons on this host
cd backend && source .venv/bin/activate
python scripts/preflight.py                  # environment check; exits 1 if not runnable
python scripts/fetch_landcover.py            # ESA WorldCover -> landclass_hsk.npy
python scripts/fetch_dem.py                 # Open-Meteo -> dem_hsk.npy
python scripts/seed_scene.py                 # prints the sceneId
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
python -m uvicorn app.main:app --reload      # :8000
python tests/test_contract.py                # 11/11
python tests/test_constants.py               #  9/9

# Frontend
cd frontend && npm install && npm run dev    # :5173
npx tsc --noEmit                             # must be clean
```

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
```

### Layer responsibilities

| Layer | Path | Role |
|---|---|---|
| Routers | `app/routers/` | HTTP only. Parse, delegate, serialise. |
| Schemas | `app/schemas/` | The wire contract. `RowModel` translates storage <-> wire. |
| Services | `app/services/` | Business logic: cascade, bbox repair, URL resolution. |
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
python scripts/fetch_landcover.py            # ESA WorldCover 10 m, CC BY 4.0
python scripts/fetch_dem.py                 # Open-Meteo, ~90 m
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
```

No MinIO, no `gdal_translate`, no COG pipeline. Elevation reaches Cesium as a
**mapbox terrain-RGB** raster via `CustomHeightmapTerrainProvider`
(`src/view/demProvider.ts`). Overlays are `SingleTileImageryProvider` images over
the bbox rectangle.

Every raster writes a `.json` sidecar with its constants and assumptions, and
registers itself against the seeded scene.

## Conventions that will bite you

- **Run `scripts/preflight.py` before debugging anything.** Most failures here
  are environmental, not code: a stale 3.10 `.venv` without numpy looks exactly
  like a broken script.
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
- **Derive constants, never hand-type them.** The bbox came from machine-retrieved
  OSM anchors recorded in `scene_hsk.json`, not from memory.
- **Guard every Cesium `Viewer` touch with `isLive()`.** StrictMode destroys the
  viewer in one effect's cleanup while sibling cleanups still hold a reference.

## Where things are written down

| Question | File |
|---|---|
| How do I run this? | [`README.md`](README.md) |
| Why is it built this way? | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Where did the data come from? | [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) |
| What actually works? | [`docs/OCT6_STATUS.md`](docs/OCT6_STATUS.md) |
| What does the spec say? | `docs/技术文档.docx`, `docs/_技术文档_clean.txt` |

## Git

Feature branches: `feature/scene-management`, `feature/sandbox-3d-visualization`.
The REST contract is the integration boundary; merge the backend first.

This prototype is **not engineering-grade and must not overclaim**. Every
generated figure is a screening estimate. Keep the UI labels and the sidecar
metadata honest -- that is a requirement, not politeness.
