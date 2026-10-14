# Frontend file map

Where each module lives, what it owns, and what it is allowed to depend on. Updated
2026-10-14, after the real-data swap (the procedural city model and its shader were
removed) and the browser-suite rewrite.

## The one rule

```
api/  ->  state/  ->  view/  ->  components/
```

Data flows one way. `api/` never imports from `state/` or `view/`; `state/` never
imports from `view/` or `components/`; `components/` never import from `view`
*except* for the action registries noted below.

`src/view/` is rendering only: it reads state and never fetches. `src/components/`
triggers state changes and holds no rendering logic.

## `src/api/` — the wire contract

| File | Owns |
|---|---|
| `client.ts` | axios instance, the single error normaliser (`toApiError`) |
| `types.ts` | every wire type, mirrored from the Pydantic schemas |
| `sceneApi.ts` `planApi.ts` `impactApi.ts` | one module per endpoint group |
| `chatApi.ts` | `postChat` and `streamChat`. **Not axios**: SSE needs a readable stream and a POST body, so `EventSource` cannot be used. Abortable via `AbortSignal` |

`ViewMode` lives here rather than in `view/viewModes.ts` because the **store** owns it
and `src/view/` must not be imported from state.

## `src/state/` — the source of truth

| File | Owns |
|---|---|
| `sceneStore.ts` | scene, terrain, layer visibility, view mode, the rainfall/temperature sliders, `run()` and its result, loading/error |
| `planStore.ts` | plans, `activePlanId` (what the 3D view shows), `selectedPlanIds` (what a run scores — deliberately a separate field) |
| `tileLoadStore.ts` | 3D Tiles load progress, which drives the loading screen |
| `sceneStore.test.ts` | reducer regression tests (ADR-024 in-place-write trap) |

## `src/view/` — rendering

| File | Owns |
|---|---|
| `SandboxViewer.tsx` | the Cesium `Viewer` and every wiring effect; publishes `cameraControl` and the overlay status hooks; draws the flood/heat overlays as entities named *Flood extent* / *Heat map overlay* |
| `cameraRig.ts` | bbox framing, orbit, zoom, the gesture bindings, and `wheelToOrbit` (the pure maths that turns a wheel event into a camera move) |
| `cameraHeading.ts` | the camera heading as a tiny external store, read by the compass. Not Zustand: it changes every frame of a drag and only the compass reads it |
| `viewModes.ts` | survey/tactical scene dressing; restores everything it changes. Reached from the sandbox chrome, not a labelled button |
| `demProvider.ts` | terrain-RGB decode, **the ground sampler**, terrain provider |
| `terrainLoader.ts` | imagery providers, bbox -> `Rectangle` |
| `tilesetLayer.ts` | the real Open3Dhk 3D Tiles layers (buildings + terrain) |
| `interventionLayer.ts` | intervention GeoJSON styling |
| `onlineProps.ts` | the online glTF assets layer (Khronos samples) |
| `hk1980Transform.ts` | HK1980 grid <-> lon/lat, for reading the Open3Dhk tiles |

`geometry.test.ts` sits alongside the module it covers. The procedural
`cityModel.ts` / `techMaterial.ts` were removed in stage 1 — buildings are now the
real Open3Dhk tiles, and there is no generated GLSL.

### Why `sampleAt`, `pointInRing` and friends are exported

`demProvider.sampleAt`, `client.toApiError`, `ringCentroid`, `rotateRing` and
`translateRing` are exported **for tests**. They are pure, and they are exactly where
silent damage happens: `sampleAt` once indexed a 256 px grid against a raster that is
really 28 x 37 and reported "no terrain" for the whole district without throwing.

## `src/components/` — UI only

| File | Owns |
|---|---|
| `ChatPanel.tsx` | the thread: answer cards, the three-step stepper, the stop button. Reads partial JSON mid-stream so the headline appears while the model writes. Routes intervention instructions to the AI planner and questions to the chat |
| `SlidersPanel.tsx` | rainfall intensity + duration, tide state, surface temperature, the Run button, and the run error alert |
| `ProjectCatalog.tsx` | the project/phase pick-list (placeholder data — see `STATUS.md`) |
| `StatsPanel.tsx` | flood extent, ponding area, surface/air temperature, green · paved, storage capacity, impervious share, green + water, each with its signed delta |
| `ViewButtons.tsx` | bottom-centre pill: compass, orbit, tilt, zoom, top-down, reset, and the `?` help |
| `navHelp.ts` | the gesture cheat-sheet as data, plus the first-run flag. Data so a test can hold it to the keys and gestures the app actually binds |
| `SliderField.tsx` | one labelled slider; the label element carries the id `aria-labelledby` points at, which is what gives the slider an accessible name |
| `HudPanel.tsx` | the hairline box + corner brackets every panel sits in; replaces `Paper variant="outlined"` |
| `SceneNotice.tsx` | the named empty/error states (no terrain, no interventions) instead of a blank surface |
| `LoadingScreen.tsx` | shown while the tilesets initialise |
| `AppErrorBoundary.tsx` | last-resort boundary: a render throw becomes a sentence, not a white page |

`theme.ts` (at `src/`, not in `components/`) is the single MUI dark theme. Its signal
colours drive the 2D chrome. It is imported by `main.tsx` and by no component.

`index.css` holds the `hud-label` / `hud-mono` / `hud-edge-text` / `hud-dot`
utilities, the thin scrollbars and the pulse keyframes.

`SandboxViewer` publishes the `cameraControl` action registry so a component can
trigger a viewer action without the Cesium instance being drilled into the tree.
`ViewButtons` is the current consumer.

## Scripts

| File | Purpose |
|---|---|
| `_verify-common.mjs` | shared plumbing: Playwright resolution, server preflight, the checker, error capture, the tileset-aware `settleViewer` |
| `verify-3d.mjs` | 17 checks: tileset load, camera framing, pan, the run reaching `POST /run`, both overlays drawn from the run's own id, canvas change, the flood/heat toggles |
| `verify-ux.mjs` | 21 checks: first paint, loading affordance, failure paths (404 / unreachable / 5xx / malformed payload), phone layout, keyboard reach, slider names, reduced motion, slow network |
| `verify-nav.mjs` | 25 checks on navigation: the help opens once and stays dismissed, the tilt buttons and the T / Home keys move the real camera, the compass turns with the view and faces north, and typing in the chat does not move it |
| `verify-chat.mjs` | 17 checks on the chat panel with the stream mocked (so no model is needed): the provenance receipt, the live region, the in-progress shimmer, a general answer carrying no receipt, and a stream cut before `done` |

Both need the dev server on `:5173` **and** the backend on **`:8001`** (that is what
`vite.config.ts` proxies `/api`, `/static` and `/health` to). They are not part of
`npm test`, which is unit-only, so a missing server cannot silently turn them green.
They exit `2` when the servers are absent or Playwright cannot be found, `1` when a
check fails, `0` only when every check passed.

Playwright is resolved from `$PLAYWRIGHT_PATH`, else from the repo. It is not a
dependency, so `npm i --no-save playwright && npx playwright install chromium` is the
one-off setup.

## What is *not* source

Two directories under `backend/data/` are a **runtime store**, not part of the build.
Exactly two files in them are committed — the seeded scene and the baseline plan —
and `.gitignore` allowlists precisely those by id.

Everything else there is residue. The suites that create records redirect
`settings.*_dir` at a temp copy (`backend/tests/_tmpstore.py`), so running them no
longer adds to it — poking the API by hand still does. A loose file in
`interventions/` that no plan references is unreachable, and a loose *plan* makes a
loose intervention read as live, which is why the ledger's "no shipped seed is
over-large" check asks git what is actually in the repo. Deleting residue is safe;
deleting a fixture is not.

Rasters (`backend/data/rasters/`) are the opposite case: generated artefacts that
*are* committed because the tests pin their md5s and the API serves them. Depths come
from the `.npy`, never the `.png`. A live run writes to `rasters/run/`, which is
gitignored and pruned to the newest runs.

## Adding a module

1. Put it in the layer matching the table above and respect the dependency direction.
2. If it is pure and decides geometry, export it and write tests for it.
3. Add it to this table.
4. Add a `CHANGELOG.md` entry — every non-trivial change gets one.
5. If it creates records in the store, call `tests/_tmpstore.isolate_store()` first.
   The residue is invisible until someone counts the files.
