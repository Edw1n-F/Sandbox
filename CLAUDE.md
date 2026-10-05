# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SDG hackathon project: a 3D geospatial visualization sandbox for scene/plan/intervention management. The current phase focuses on the chain: **geographic data → PostgreSQL/PostGIS → scene management API → CesiumJS 3D frontend**. Physics engine, data analysis, AI modules are out of scope for now.

## Tech Stack

**Backend:** Python FastAPI + SQLAlchemy + PostgreSQL/PostGIS + MinIO  
**Frontend:** Vite + React + TypeScript + CesiumJS + Zustand + MUI + Tailwind CSS

## Development Commands

### Backend
```bash
cd backend
pip install -r requirements.txt
alembic upgrade head          # run migrations
uvicorn app.main:app --reload # dev server (default: localhost:8000)
```

### Frontend
```bash
cd frontend
npm install
npm run dev    # Vite dev server
npm run build
npm run lint
```

## Architecture

### Core Design Principles

1. **Single data authority**: Frontend never connects directly to the database. All reads/writes go through the FastAPI scene management module.
2. **GeoJSON pass-through**: PostGIS `geometry` column ↔ GeoJSON ↔ Cesium `GeoJsonDataSource` — no coordinate conversion at any layer.
3. **Unified coordinate system**: EPSG:4326 everywhere — database, API, and Cesium rendering.
4. **Scene as entry anchor**: All frontend rendering begins by fetching `GET /api/scenes/{sceneId}` to get `center / bbox / terrainUrl / imageryUrl / defaultCamera`.

### Data Flow

```
GeoJSON/DEM input
  → geo_import.py (validate + route)
    → vector: PostGIS (scene.bbox, intervention.geom)
    → raster: MinIO (COG), URL registered in scene.terrain_url / imagery_url
  → scene record (sceneId issued)
    → frontend: GET /api/scenes/{sceneId}
      → Cesium Viewer init (terrain + imagery)
        → GET /api/plans/{planId}/interventions (GeoJSON FeatureCollection)
          → Cesium GeoJsonDataSource render
```

### Backend Layer Responsibilities

| Layer | Path | Role |
|---|---|---|
| Routers | `app/routers/` | HTTP endpoints, request validation |
| Services | `app/services/` | Business logic, data validation |
| Models | `app/models/` | SQLAlchemy ORM (PostGIS geometry columns) |
| Schemas | `app/schemas/` | Pydantic request/response models |
| Geo import | `app/services/geo_import.py` | GeoJSON/raster ingestion, EPSG:4326 normalization |

### Frontend Layer Responsibilities

| Layer | Path | Role |
|---|---|---|
| State | `src/state/` | Zustand stores (sceneStore, planStore) — source of truth |
| API | `src/api/` | REST calls to backend (axios/fetch wrapper in `client.ts`) |
| View | `src/view/` | CesiumJS rendering only — reads from state, never fetches directly |
| Components | `src/components/` | MUI UI layer — triggers state changes, no rendering logic |

The rendering layer (`src/view/`) must not fetch data itself. Data flows: API → State (Zustand) → View (Cesium).

## Database Schema

Three core tables with `ON DELETE CASCADE` chain: `scene → plan → intervention`.

- **scene**: geographic anchor. Key fields: `center GEOMETRY(Point,4326)`, `bbox GEOMETRY(Polygon,4326)`, `terrain_url TEXT`, `imagery_url TEXT`, `default_camera JSONB`, `env_params JSONB`.
- **plan**: `plan_type` ∈ `{baseline, economic, environmental, comprehensive, custom}`, `source` ∈ `{manual, ai}`.
- **intervention**: `geom GEOMETRY` (SRID 4326), `params JSONB`, `style JSONB` (color/opacity/extrusionHeight), `status` ∈ `{draft, validated}`.

All geometry columns have GIST indexes. Use `ST_Transform` to normalize to EPSG:4326 on import.

## API Contract (key endpoints)

```
POST   /api/scenes                          # create scene
GET    /api/scenes/{sceneId}                # 3D sandbox init entry point
PUT    /api/scenes/{sceneId}
DELETE /api/scenes/{sceneId}                # cascades to plans + interventions
GET    /api/scenes/{sceneId}/plans
POST   /api/scenes/{sceneId}/plans
PUT    /api/plans/{planId}
GET    /api/plans/{planId}/interventions    # returns GeoJSON FeatureCollection
GET    /api/scenes/{sceneId}/terrain        # terrainUrl + imageryUrl + bbox
```

Error responses: 422 for validation errors, 404 with `{"detail": "..."}`, all errors use `{"error": {"code": "...", "message": "..."}}`.

## Raster Data (MinIO)

- DEM and imagery stored as Cloud-Optimized GeoTIFF (COG) in MinIO.
- Convert: `gdal_translate -of COG input.tif output_cog.tif`
- Upload: `mc cp output_cog.tif myminio/sandbox/terrain/`
- URLs registered in `scene.terrain_url` / `scene.imagery_url`; frontend treats them as opaque provider URLs.

During development before real geodata is available, use a placeholder GeoJSON boundary + online Cesium imagery to close the end-to-end loop.

## Git Branch Convention

- `feature/scene-management` — backend CRUD + geo import
- `feature/sandbox-3d-visualization` — frontend Cesium + rendering
- Merge `feature/scene-management` first; use its REST API contract as the integration boundary.
