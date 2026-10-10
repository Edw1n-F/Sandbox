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

**Backend:** Python 3.13 · FastAPI · Pydantic v2 · numpy · Pillow · rasterio · pyproj
**Frontend:** Vite · React 18 · TypeScript · CesiumJS 1.146 · Zustand · MUI

**Storage is JSON files, not PostGIS.** `STORE_MODE=file`. The seam is
`app/db.py` + `app/services/scene_service.py` — the rest of the codebase never
touches storage directly, so the migration is a swap of those two files only.
**No SQLAlchemy, no alembic, no migrations directory** — the spec references
one; it does not exist, and it is flagged in `docs/OCT6_STATUS.md` rather than
faked.

**Target architecture (not yet implemented):** PostGIS (EPSG:4326 geometry
columns) replaces the JSON file store; MinIO replaces the local `/static` mount
for rasters and glTF assets. When that migration lands:
- `app/db.py` swaps from `read_doc/write_doc` to `psycopg2` + `GeoAlchemy2`.
- `app/services/scene_service.py` uses `ST_AsGeoJSON()` for geometry reads.
- `scripts/ingest_*.py` write SQL `INSERT` instead of JSON files.
- The `/static` FastAPI mount is replaced by pre-signed MinIO URLs.
- SQLAlchemy + Alembic + `asyncpg`/`psycopg2-binary` + `GeoAlchemy2` enter
  `requirements.txt`. A Docker Compose file with `postgis/postgis` and
  `minio/minio` is the local dev story.

**No Cesium Ion token.** The Viewer sets `baseLayer: false` and supplies
OpenStreetMap explicitly, so the app runs with zero credentials. Leave
`VITE_CESIUM_ION_TOKEN` empty.

## Commands

```bash
# Backend -- always activate the venv first; there are two Pythons on this host
cd backend && source .venv/bin/activate
python scripts/preflight.py                  # environment check; exits 1 if not runnable

# Primary data pipeline (6-NW-12D Lands Dept glTF -- run in order)
python scripts/ingest_terrain_gltf.py        # 6-NW-12D terrain -> dem_terrain_rgb.png
python scripts/ingest_buildings_gltf.py      # 6-NW-12D buildings -> citymodel_hsk.json
python scripts/ingest_layers_gltf.py         # 6-NW-12D waterbody + infrastructure -> GeoJSON
python scripts/seed_scene.py                 # prints the sceneId

# Analysis overlays (optional, on top of the above)
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py

python -m uvicorn app.main:app --reload      # :8000
python tests/test_contract.py                # 11/11
python tests/test_constants.py               # 16/16
python tests/test_ai_plan.py                # 10/10

# Superseded scripts (open-data fallbacks, do not run unless 6-NW-12D is unavailable)
# python scripts/fetch_landcover.py          # ESA WorldCover -> landclass_hsk.npy
# python scripts/fetch_dem.py               # Open-Meteo -> dem_hsk.npy
# python scripts/make_citymodel.py          # procedural massing from WorldCover
# python scripts/seed_interventions.py --reset

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

**Primary source: `backend/6-NW-12D/`** — HK Lands Department glTF assets,
EPSG:2326 (HK1980 Grid), converted to WGS84 (EPSG:4326) by `pyproj`.

| Asset class | 6-NW-12D subdir | Ingest script | Output |
|---|---|---|---|
| Terrain | `TERRAIN(TB)/` | `ingest_terrain_gltf.py` | `dem_terrain_rgb.png`, `dem_hsk.json` |
| Buildings | `BUILDING/` | `ingest_buildings_gltf.py` | `rasters/citymodel_hsk.json` |
| Water bodies | `WATERBODY/` | *(planned)* | — |
| Infrastructure | `INFRASTRUCTURE/` | *(planned)* | — |
| Vegetation | `VEGETATION(TB)/` | *(planned)* | — |

Each ingest script writes a `.json` sidecar with provenance and registers the
output against the seeded scene via `attach_overlay`.

**Open-data fallbacks (superseded):** `fetch_dem.py` (Open-Meteo ~90 m) and
`fetch_landcover.py` (ESA WorldCover) remain in `scripts/` but are not part of
the primary pipeline. Do not run them unless 6-NW-12D data is unavailable.

Elevation reaches Cesium as a **Mapbox terrain-RGB** raster via
`CustomHeightmapTerrainProvider` (`src/view/demProvider.ts`). Overlays are
`SingleTileImageryProvider` images over the bbox rectangle. Analysis overlays
(`wetgrid`, `heatmap`) still use `.npy` as the authoritative data; the `.png`
is a render only.

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
- **A derived feature's geometry must match its stated size.** The first
  intervention seeder drew a polygon for a connected component's *centroid
  cell* while reporting the whole component's area, so a "155 ha" feature was
  0.003 ha and invisible. `test_constants.py` now asserts the two agree.
- **The city model is real massing from Lands Dept, not procedural.** Footprints
  are AABB rectangles extracted from 6-NW-12D glTF (not surveyed polygons);
  heights are the glTF mesh Y-axis maximum. Never describe it as a surveyed
  building dataset, but do not call it "provisional" — the source is official.
  Keep `citymodel_meta.json`'s `note` field honest.
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
