# Frontend file map

Where each module lives, what it owns, and what it is allowed to depend on. Updated
2026-10-12 — the sandbox reorganisation replaced most of the panel set, and the chat
added `api/chatApi.ts`.

> **Two components tables below.** The first is what `App.tsx` actually renders.
> The second lists modules that are still on disk but no longer imported by
> anything — they are tsc-clean leftovers from the pre-sandbox layout, kept
> rather than deleted mid-reorg. Do not add to them.

## The one rule

```
api/  ->  state/  ->  view/  ->  components/
```

Data flows one way. `api/` never imports from `state/` or `view/`; `state/` never
imports from `view/` or `components/`; `components/` never import from `view/`
*except* for the two action registries noted below.

`src/view/` is rendering only: it reads state and never fetches. `src/components/`
triggers state changes and holds no rendering logic.

## `src/api/` — the wire contract

| File | Owns |
|---|---|
| `client.ts` | axios instance, the single error normaliser (`ApiError`) |
| `types.ts` | every wire type, mirrored from the Pydantic schemas |
| `sceneApi.ts` `planApi.ts` `impactApi.ts` | one module per endpoint group |
| `chatApi.ts` | `postChat` and `streamChat`. **Not axios**: SSE needs a readable stream and a POST body, so `EventSource` cannot be used. Abortable via `AbortSignal` |

`ViewMode` lives here rather than in `view/viewModes.ts` because the **store** owns it
and `src/view/` must not be imported from state.

## `src/state/` — the source of truth

| File | Owns |
|---|---|
| `sceneStore.ts` | scene, terrain, layer visibility, view mode, loading/error |
| `planStore.ts` | plans, active plan, intervention features |
| `sceneStore.test.ts` | reducer regression tests (ADR-024 in-place-write trap) |

## `src/view/` — rendering

| File | Owns | Depends on |
|---|---|---|
| `SandboxViewer.tsx` | the Cesium `Viewer` and every wiring effect; publishes `cameraControl`, the overlay/prop status hooks | almost everything below |
| `cameraRig.ts` | bbox framing, orbit, zoom, and the gesture bindings (left-drag moves/spins the slab, right-drag and middle-drag tilt, pinch and ctrl+wheel zoom) | cesium |
| `viewModes.ts` | survey/tactical scene dressing; restores everything it changes | cesium |
| `demProvider.ts` | terrain-RGB decode, **the ground sampler**, terrain provider | canvas |
| `terrainLoader.ts` | imagery providers, bbox -> `Rectangle` | cesium |
| `interventionLayer.ts` | intervention GeoJSON styling | cesium |
| `onlineProps.ts` | the online 3D assets layer | cesium |
| `tilesetLayer.ts` | the real Open3Dhk 3D Tiles layer (buildings + terrain) | cesium |

`geometry.test.ts` sits alongside the module it covers. The procedural
`cityModel.ts` / `techMaterial.ts` (2,639 massing envelopes) were removed in stage 1 —
buildings are now the real Open3Dhk tiles via `buildings3DUrl`.

### Why `sampleAt`, `pointInRing` and friends are exported

`demProvider.sampleAt`, `client.toApiError`, `ringCentroid`,
`rotateRing` and `translateRing` are exported **for tests**. They are pure, and they are exactly where
silent damage happens: `sampleAt` once indexed a 256 px grid against a raster that is
really 28 x 37 and reported "no terrain" for the whole district without throwing.

## `src/components/` — UI only

| File | Owns |
|---|---|
| `ChatPanel.tsx` | the thread: answer cards, the three-step stepper, the stop button. Reads partial JSON mid-stream so the headline appears while the model writes. Routes interventions to the AI planner and questions to the chat |
| `HudPanel.tsx` | the hairline box + corner brackets every panel sits in; replaces `Paper variant="outlined"` |
| `SlidersPanel.tsx` | rainfall and temperature, with the live mono readout |
| `StatsPanel.tsx` | unstored runoff, wet area, heat index, green · paved, each with its signed delta |
| `ProjectCatalog.tsx` | the project/phase pick-list (placeholder data — see `STATUS.md`) |
| `ViewButtons.tsx` | bottom-centre pill: rotate / tilt / zoom / top-down / reset |
| `SliderField.tsx` | one labelled slider; shared so the a11y wiring exists once |
| `AppErrorBoundary.tsx` | last-resort boundary: a render throw becomes a sentence, not a white page |

`theme.ts` (at `src/`, not in `components/`) is the single MUI dark theme. Its
signal colours drive the 2D chrome. It is imported by
`main.tsx` and by no component.

`index.css` holds the `hud-label` / `hud-mono` / `hud-edge-text` / `hud-dot`
utilities, the thin scrollbars and the pulse keyframes.

`SandboxViewer` publishes the `cameraControl` action registry
so a component can trigger a viewer action without the Cesium
instance being drilled into the tree. `ViewButtons` is the current consumer.

### Removed 13 Oct 2026 — orphaned, imported by nothing

`ScenePanel.tsx` (which itself imported `ImpactPanel` and `RunSummary`),
`ImpactPanel.tsx`, `RunSummary.tsx`, `CameraController.tsx`, `LayerToggle.tsx`,
`ViewModeSwitch.tsx` and `PromptBar.tsx` were all tsc-clean but reached from
nothing — `App.tsx` had moved on. They were deleted, along with the doc
references that named them, so a feature added to one cannot be work nobody
sees. (Closed 13 Oct 2026; the reasoning is in `CHANGELOG.md`.)

## Scripts

| File | Purpose |
|---|---|
| `verify-3d.mjs` | 38 checks: rendering, camera, materials. Asserts on pixels, not flags |
| `verify-ux.mjs` | 20 checks: first paint, failure paths, phone, keyboard, a11y, reduced motion |
| `dump-shader.mjs` | prints the generated GLSL for the building material |

Both `verify-*` need the dev server on `:5173` **and** the backend on `:8000`. They are
not part of `npm test`, which is unit-only, so a missing server cannot silently turn
them green.

## What is *not* source

Three directories under `backend/data/` are a **runtime store**, not part of the
build. Exactly three files in them are committed — the seeded scene, the baseline
plan and `seed_interventions.py`'s output — and `.gitignore` allowlists precisely
those by id.

Everything else there is residue. The suites that create records now redirect
`settings.*_dir` at a temp copy (`backend/tests/_tmpstore.py`), so running them no
longer adds to it — poking the API by hand still does. A loose file in
`interventions/` that no plan references is unreachable (34 had accumulated before
the 12 Oct 2026 cleanup), and a loose *plan* makes a loose intervention read as
live, which is why the ledger's "no shipped seed is over-large" check asks git what
is actually in the repo. Deleting residue is safe; deleting a fixture is not.

Rasters (`backend/data/rasters/`) are the opposite case: generated artefacts that
*are* committed because the tests pin their md5s and the API serves them. Depths
come from the `.npy`, never the `.png`.

## Adding a module

1. Put it in the layer matching the table above and respect the dependency direction.
2. If it is pure and decides geometry, export it and write tests for it.
3. Add it to this table.
4. Add a `CHANGELOG.md` entry — every non-trivial change gets one.
5. If it creates records in the store, call `tests/_tmpstore.isolate_store()`
   first. The residue is invisible until someone counts the files.
