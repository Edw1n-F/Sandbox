# SDG T1: 3D Planning Sandbox

A Hack4SDG prototype for exploring planning scenarios in Hung Shui Kiu / Ha Tsuen (HSK/HT). It renders terrain, buildings and environmental overlays (heat, flood/wet grid, ventilation corridors) in a 3D Cesium viewer. You can place interventions, re-run the analysis and ask questions about a run in a grounded chat panel.

This prototype is no longer being developed.

## Stack

- **Backend:** FastAPI (Python 3.14), with numpy, scipy, rasterio and trimesh for the analysis. Data is stored as files in `backend/data` (`STORE_MODE=file`).
- **Frontend:** React 18, TypeScript, Vite, Cesium, MUI and Zustand.
- **Contracts:** `contracts/openapi.json` and `contracts/intent_vocabulary.json`.

## Running locally

Backend (port 8000):

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
python scripts/preflight.py   # checks the environment
uvicorn app.main:app --reload
```

Frontend (port 5173, sends `/api` and `/static` requests to the backend):

```bash
cd frontend
npm ci
cp .env.local.example .env.local
npm run dev
```

## Configuration

- `backend/.env`: the chat model is optional. Set `ANTHROPIC_API_KEY`, or point `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` at an OpenAI-compatible endpoint. Without a model, chat falls back to template answers that are labelled as such.
- `frontend/.env.local`: `VITE_CESIUM_ION_TOKEN` is optional. `VITE_API_BASE_URL` and `VITE_SCENE_ID` override the defaults.

## Layout

```
backend/app/       API: routers, services, schemas, chat agent
backend/scripts/   data preparation (DEM, land cover, heat, wet grid, buildings, seeding)
backend/data/      scenes, plans, rasters and other generated data, served at /static
backend/model/     3D models (city tiles, terrain, station, park)
backend/tests/     pytest suite
frontend/src/      viewer, panels and state
frontend/scripts/  end-to-end verification scripts (npm run verify:*)
```

## Checks

```bash
cd backend && pytest
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
```

CI runs the same checks (`.github/workflows/ci.yml`).
