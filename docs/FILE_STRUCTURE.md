# Frontend file map

Where each module lives, what it owns, and what it is allowed to depend on. Updated
2026-10-09 — `cameraRig.ts`, `techMaterial.ts` and `viewModes.ts` were added since the
last version of this file, which is why it did not exist.

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
| `SandboxViewer.tsx` | the Cesium `Viewer` and every wiring effect; publishes `cameraControl`, `cityModelStats`, the overlay/prop status hooks | almost everything below |
| `cameraRig.ts` | bbox framing, orbit, zoom, gesture bindings | cesium |
| `cityModel.ts` | 2,639 massing envelopes as entities; height/rotation ops; click-to-select | `demProvider`, `techMaterial` |
| `techMaterial.ts` | the emissive building shader and the `MaterialProperty` protocol | cesium |
| `viewModes.ts` | survey/tactical scene dressing; restores everything it changes | cesium |
| `demProvider.ts` | terrain-RGB decode, **the ground sampler**, terrain provider | canvas |
| `terrainLoader.ts` | imagery providers, bbox -> `Rectangle` | cesium |
| `interventionLayer.ts` | intervention GeoJSON styling | cesium |
| `onlineProps.ts` | the online 3D assets layer | cesium |

`geometry.test.ts` and `cityModel.test.ts` sit alongside the modules they cover.

### Why `sampleAt`, `pointInRing` and friends are exported

`demProvider.sampleAt`, `client.toApiError`, `cityModel.pointInRing`, `ringCentroid`,
`rotateRing` and `translateRing` are exported **for tests**. They are pure, and they are exactly where
silent damage happens: `sampleAt` once indexed a 256 px grid against a raster that is
really 28 x 37 and reported "no terrain" for the whole district without throwing, and
`pointInRing` decides which buildings a scoped plan touches. Neither was reachable from
a test while module-private.

## `src/components/` — UI only

| File | Owns |
|---|---|
| `ScenePanel.tsx` | scene identity, plan selector, GenAI badge, city-model card |
| `LayerToggle.tsx` | the "what am I looking at" switches |
| `ViewModeSwitch.tsx` | survey/tactical switch **and the tactical legend** |
| `CameraController.tsx` | framing buttons, orbit/zoom buttons, gesture captions |
| `SliderField.tsx` | one labelled slider; shared so the a11y wiring exists once |
| `AppErrorBoundary.tsx` | last-resort boundary: a render throw becomes a sentence, not a white page |
| `ImpactPanel.tsx` | per-feature impact readout |

Two components reach into `view/` on purpose, both for **actions**, never for state:
`CameraController` and `ViewModeSwitch`/`LayerToggle` consume the registries
(`cameraControl`, `cityModelStats`) that `SandboxViewer` publishes. This is the seam
that lets the Cesium instance stay owned by the viewer instead of being drilled into
the tree.

## Scripts

| File | Purpose |
|---|---|
| `verify-3d.mjs` | 38 checks: rendering, camera, materials. Asserts on pixels, not flags |
| `verify-ux.mjs` | 20 checks: first paint, failure paths, phone, keyboard, a11y, reduced motion |
| `dump-shader.mjs` | prints the generated GLSL for the building material |

Both `verify-*` need the dev server on `:5173` **and** the backend on `:8000`. They are
not part of `npm test`, which is unit-only, so a missing server cannot silently turn
them green.

## Adding a module

1. Put it in the layer matching the table above and respect the dependency direction.
2. If it is pure and decides geometry, export it and write tests for it.
3. Add it to this table.
4. Add a `CHANGELOG.md` entry — every non-trivial change gets one.
