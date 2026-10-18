# HACK4SDG - Sandbox
A Hack4SDG prototype for exploring planning scenarios in Hung Shui Kiu / Ha Tsuen (HSK/HT). It renders terrain, buildings and environmental overlays (heat, flood/wet grid, ventilation corridors) in a 3D Cesium viewer. You can place interventions, re-run the analysis and ask questions about a run in a grounded chat panel.

## Features

- **3D sandbox:** terrain and buildings in Cesium, in textured 3D or 2.5D massing, with buttons for tilt, orbit, zoom, top-down and north-up.
- **Overlays:** flood (wet grid) and heat layers you can switch on and off.
- **Scenario inputs:** rainfall intensity, duration and surface temperature sliders feed each run.
- **Project catalog:** planned projects with real footprints. Selecting several scores their combined effect. The backend refuses a run that reworks more than 40% of the study area.
- **Interventions:** describe one in the chat (for example, a park or a retention pond). It shows up as a draft, and you confirm it before it is placed. A details card asks for each value the run uses, and you either enter your own or let the AI estimate it. Estimated values are labelled as such.
- **Grounded chat:** ask about a run and get a structured answer card covering what the run shows, what to do, confidence and caveats. Every number in it is checked against the run's outputs.
- **Stats and parameter card:** a live summary after each run, plus a reference table of the run's assumptions, including what the model doesn't know.
- **LLM settings:** set the provider, base URL and model in the app. The API key is never shown back.

## User flow

1. Open the app. The HSK/HT scene loads and the camera frames the study area.
2. Turn on the Flood or Heat overlay to see the baseline.
3. Pick projects from the catalog, add interventions through the chat, or both.
4. Set the sliders and press **Run**.
5. Read the stats panel, then ask the chat why the numbers changed or what to try next.
6. Adjust or delete interventions in the Interventions panel and run again to compare.

## Where GenAI is used

The language model explains results and picks from options the analysis has already worked out. It never produces the numbers itself.

- **Chat answers** (`backend/app/agent/chat_service.py`): turns a question about a run into a structured answer card. Every number in the card is checked against the run's outputs. If a number doesn't match, the card goes back to the model once with the problem named. Anything still unmatched is flagged in the card.
- **Run explanation** (`backend/app/services/run_service.py`): after each run, the model writes a short card on what the run changed.
- **Interventions from plain language** (`backend/app/services/ai_plan.py`): the model turns an instruction like "add a retention pond in the north" into a choice from a fixed list of candidate sites drawn from the terrain, land cover and flood data. It can't invent coordinates. Building-height and density requests go to a separate massing tool. Anything the AI proposes starts as a draft, and nothing is calculated on it until the user confirms it.
- **Estimated values**: on the details card, the user can let the AI estimate a value instead of entering one. Estimated values are labelled differently from values the user entered.

Telling a question apart from an intervention request is done by fixed rules, not the model. The app supports Anthropic or any OpenAI-compatible endpoint (`backend/app/agent/llm.py`) and forces structured output. If no model is set up, it falls back to template cards, labelled as templates, and a rule-based parser.

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
