# Changelog

Every non-trivial change gets an entry. Newest first.

## 2026-10-09 — heat SVF + anchoring, wind corridors, drainage routing, compare()

Stages 3–6 of `Hack4SDG_Heat_Wind_Drainage_Research.md` §5.2. **393 backend tests**
(was 271). Every new output carries a machine-readable `status` and a human
`warning`; nothing here is a temperature, a velocity ratio, or a flood prediction.

### Added

- **`compare()` — an ordering, but only under stated weights.**
  `POST /api/scenes/{id}/scenario/compare`. With no `weights` it *refuses*
  (`refused: true`, `ranked: null`, reason given) rather than falling back to a
  default weight set, because a default would quietly become the project's opinion
  about which axis matters — the judgement TC 1/06 says cannot be settled. With
  weights it returns `ranked` rows that each still carry the three raw axes and
  their per-axis ranks, plus a full `byAxis` ordering. The weighted total is named
  `weightedFavourability`, never `score`, and `combinedScore` stays `null`.
  Weights need not sum to 1; both the supplied and the normalised vectors are
  echoed. Negative, unknown-axis and all-zero weights raise.
- **Sky-view factor by horizon search** — `sky_view_factor_raster` /
  `sky_view_factor`, 16 azimuth bins over a 300 m radius, fully vectorised.
  Acceptance: a thin lone 50 m tower in a flat field reads open-field mean
  **0.9913** with `minSvf` 0.0 at the tower; a uniform block over the whole grid
  reads **0.0**. 0.07 s over the district. A 4×4-cell block reads 0.957 and is
  deliberately *not* the lone-tower fixture — it is a large obstruction, not a
  thin tower.
- **A blended heat field** — land-class index, SVF and cast shadow combined into
  one relative 0–1 raster (`blended_heat_field`), served by
  `GET /api/scenes/{id}/heat-field` and rendered by `make_heatmap.py`. It is a
  raster, deliberately **not** a fourth axis: adding one would have broken the
  three-axis "never a combined score" guard, and renaming `shade` would have
  broken existing tests.
- **An anchoring mechanism with nothing yet to anchor to.**
  `load_heat_calibration()` reads `data/reference/heat_anchor.json` when it
  exists. It does not exist, so `anchorSource: "none"`, `anchorRangeC: null`, and
  `HEAT_INDEX` stays labelled hand-authored. The schema names the physical
  quantity (`LST_skin` vs `HKO_air`) so the two cannot be conflated later.
- **Least-cost-path ventilation corridors** — `least_cost_corridors` via
  `scipy.sparse.csgraph.dijkstra`, plus `fad_ratio()` for baseline-vs-scenario
  comparison per direction. A ratio is a comparison, which the AVA framework
  authorises; it is not a velocity ratio.
- **A per-cell frontal-area-density field** — `frontal_area_raster` / `cell_fad`,
  spreading each footprint's oriented frontal area over the cells it covers and
  dividing by the cell's own area, so the area-weighted mean of the per-cell field
  equals the site-level FAD. That identity is the check that catches a wrong
  denominator.
- **D8 fill-and-spill routing** — `label_depressions`, `fill_and_spill`, `hand` in
  `scripts/hydrology.py`, on the Priority-Flood surface the project already
  computed but never routed. Served by `GET /api/scenes/{id}/drainage/routed` and
  `routed_ledger()`.
- **A mass-balance assertion that is enforced, not reported.** `mass_balance()`
  raises `AssertionError` when `runoff in ≠ stored + discharged`. A second
  identity, `generation == local_retention + runoff_in`, is asserted separately so
  an ADR-015 double count cannot hide inside a balanced cascade.
- **Four new raster artefacts**: `flow_receiver_hsk.npy` (the D8 routing graph),
  `flow_raw_elev_hsk.npy`, `flow_filled_hsk.npy` and `routed_depth_hsk.npy`.
- **HAND** (height above nearest drainage), computed from flow accumulation and
  deliberately *not* from the water/wetland class — a cross-check derived from
  land cover would cross-check nothing.

### Fixed

- **`apply_massing`'s `move` op raised `NameError` on every call.** The
  metres-per-degree-longitude factor was hoisted out of the comprehension and
  referenced an undefined `f_lat`. It cannot be hoisted at all: the degree scale
  depends on each footprint's own latitude. Pre-existing, and live — `move` is one
  of the ops the frontend applies.
- **`scale_all`, `set_site` and `move` silently reset `bearing_deg` to 0**,
  discarding the orientation a preceding `rotate` had set, because each rebuilt
  `Footprint(...)` without carrying it through. A `[rotate, scale_all]` scheme was
  scored as if unrotated — and `frontal_area_raster`, `cell_fad` and
  `frontal_area_density` all read exactly that field.
- **`GET /design-metrics` accepted `?dayOfYear=` and silently ignored it**
  (FastAPI binds the snake_case name), and neither it nor the new `/heat-field`
  range-checked the day — so `?day_of_year=999` returned a plausible sun position
  for a date that does not exist. Both routes now accept both spellings and
  require 1–366. Fixed in both, not one: a fix that leaves the same hole next door
  is how the hole survived.
- **`Grids.elevation_5m` was not what its docstring said.** It was documented as
  the Priority-Flood conditioned surface, but `load_grids()` rebuilt a fresh
  `Overlay` from the raw DEM, so the script's in-place `ov.elevation = filled`
  never persisted. Only **94.8%** of D8 receivers point downhill on the raw
  surface; routing on it loses water to already-processed cells and the
  mass-balance assertion fires — which is exactly how it was found.
  `flow_filled_hsk.npy` now stores the surface the receiver was derived from,
  which also makes that long-standing docstring true.
- **`scipy` was not installable as declared.** The 1.15.3 wheel's `scipy.sparse`
  is unimportable on this host (`_propack` dyld error), and `requirements.txt`
  claimed 1.18.1 was installed when nothing was. Pinned to `>=1.11,<1.14`
  (1.13.1 verified). No fallback Dijkstra was written: two shortest-path
  implementations is the drift this project forbids.
- **The depression mask is only correct in float64.** In float32 the rounding
  (~1e-5 m) turns `filled > raw + 1e-9` into 1,748 phantom depressions instead of
  37, inflating the capacity that is meant to count genuinely raised cells only.

### Changed

- `Overlay` gained `cell_of`, mirroring `Grids.cell_of`, so `make_heatmap.py`
  could drop its `_OverlayGrid` shim — the last duplicated cell-centre map.
  `Overlay.cell_area_m2` is now a `float` subclass that is also callable, bridging
  the two existing spellings without touching five files. A documented wart: it
  changes no number.
- `Grids` gained `receiver` and `raw_elevation_5m`, both appended last with `None`
  defaults, so no existing construction site changed. `routed_ledger()` returns
  `ROUTING_UNAVAILABLE` with a reason rather than raising when the artefacts are
  absent.
- `storage_ledger` was refactored onto a shared `_runoff_and_unstored()` helper.
  Its numbers are unchanged and pinned by `test_ledger`.

### Measured

- Mass balance on the real grid at 450 mm: runoff in **3,170,180.3 m³** (equal to
  the published `unstoredM3` — conserved, not re-derived), stored 372,621.8,
  discharged 2,797,558.5, residual **+2.9e-08 m³ (9.3e-15 relative)**.
- 37 depressions over 32,500 cells. HAND: 4,607 drainage cells, mean 6.12 m,
  agreement with the routed depth **0.881** — reported as a stated relation, never
  merged into the routed depth.
- `wetgrid_hsk.npy` and `flow_accum_hsk.npy` are **byte-unchanged**
  (md5 `d7cc052a45af4a3f6aac59773568b2ee`). The cascade is additive; the level-pool
  solve and the buffer deficit did not move.
- `evaluate()` 0.10 s, `compare()` <0.001 s, `sky_view_factor` 0.07 s,
  `least_cost_corridors` 0.70 s for eight directions.

### Known gaps

- **The corridors cannot distinguish a north-easterly from a south-westerly.**
  `cell_fad` is 180°-periodic, so the NE and SW cost surfaces are bit-identical
  and their corridors are equally optimal paths over the same cells. The symmetry
  is broken only by `directional_exposure`, which reads the *arrangement*.
  Documented in the corridor limitation, not hidden.
- **The heat field is not anchored to anything.** No Landsat LST scene and no HKO
  normal is on disk, so the 0–1 scale has no observational reference. The
  mechanism is built; the data is not.
- **`HEAT_INDEX` is still hand-authored** and still the weakest number in the
  project. It sits in the retention path, so it touches drainage numbers too.
- **`place` and `reset` remain unscoreable** by the scenario evaluator, as before.

## 2026-10-09 — 3D sandbox, tactical view mode, camera navigation

### Added
- **3D framing that is actually 3D.** `camera.flyTo({ destination: rectangle })` frames a
  `Rectangle` from directly overhead — a rectangle carries no orientation, so the fit is
  always nadir, and 2,639 massing envelopes on 139 m of relief rendered as grey squares.
  `src/view/cameraRig.ts` frames the bbox as a bounding sphere at pitch −33° instead, and
  slides the camera sideways so the study area sits in the middle of the *visible* canvas
  rather than behind the 340 px panel.
- **Tactical view mode.** A custom Cesium material (`src/view/techMaterial.ts`) draws the
  envelopes as dark volumes traced in neon with roof panel joints and a quantised height
  ramp, over a dimmed basemap, a 200 m reference grid and a tuned bloom pass. Inspired by
  [OSIRIS](https://github.com/simplifaisoul/osiris). Re-renders the *same* provisional
  data — the `provisional` chip and the caveat stay in both modes.
- **Rotation and camera controls.** Right-drag orbits, middle-drag and Ctrl+drag tilt,
  arrow keys orbit and tilt, and there are on-screen `↺ ↻ ▲ ▼ + −` buttons. Zoom is more
  sensitive than Cesium's stock rate (`zoomFactor` 12; buttons and keys move ~a third of
  the distance per press).
- **Verification harnesses.** `scripts/verify-3d.mjs` (38 checks) drives a real headless
  browser and asserts on console output, camera state *and* rendered pixels;
  `scripts/verify-ux.mjs` (15 checks) covers first paint, a missing scene, a small screen,
  keyboard access and reduced motion.
- **Unit tests.** `src/view/geometry.test.ts` (14 tests) over the DEM sampler and the
  camera geometry, on vitest 2.1.9 (pinned: vitest 5 needs Vite ≥6, this project is on 5).
- `eslint.config.mjs` — see Fixed.

### Fixed
- **Every building now stands on the terrain.** Envelope bases were all zero, so uphill
  blocks were buried inside hills. Two causes: the city model and the DEM image load in
  parallel and the model usually wins, and `demProvider` indexed a hard-coded 256 px grid
  against a raster that is really **28 × 37** — so every sample read past the end of the
  array and reported "no terrain" for the whole district *without throwing*.
- **`npm run lint` could not run at all.** ESLint 9 was installed with ESLint 8 flags and no
  `eslint.config.(js|mjs|cjs)`, so the command failed immediately. A gate that cannot run
  reads as a passing check.
- **Basemap stayed blurry after a zoom change.** Under `requestRenderMode` nothing paints
  unless a frame is requested, and imagery arriving from the network does not request one.
  Now requested on `tileLoadProgressEvent` once the queue drains.
- **Mode switches and building selection rebuilt every primitive.** The material is now
  assigned once and only its uniforms are written, so switching modes is two float writes
  per building.
- **Envelopes rendered see-through.** `Material.fromType` defaults a custom fabric to
  translucent, and translucent primitives do not write depth — so 2,639 envelopes could not
  occlude one another and the street map read through the district.
- **Arrow keys stopped working after any panel click**, because the guard skipped anything
  inside the drawer to protect the sliders. Narrowed to what actually consumes arrows.

### Changed
- The two-finger scroll gesture is **disabled** rather than zoom-only, at the user's
  request. A trackpad swipe and a mouse wheel are the same DOM event, so this necessarily
  disables wheel zoom for a mouse too; the page no longer scrolls behind the canvas, and
  zoom remains available by pinch, ⌘/Ctrl+scroll, the `+`/`−` buttons and the `+`/`−` keys.
- Panel copy now describes the gestures that are actually bound. The previous hint said
  "right-drag to orbit and tilt", which was false — right-drag zoomed.

### Fixed (audit follow-up)
- **The panel covered 100% of a phone viewport** — a 340 px drawer on a 390 px screen, with
  no map visible and no way back. The panel now collapses below 768 px and has a floating
  toggle; the camera framing inset follows it, so the study area is not centred under a
  panel that is no longer there.
- **The GenAI badge said "no LLM key" when `/health` was unreachable** — stating something
  false rather than admitting it knew nothing. Now tri-state: "LLM ready", "no LLM key",
  or "LLM status unknown" with an explanation that plans will still be generated by the
  deterministic parser.
- **The plan combo box had no accessible name.** `aria-label` now on the `Select` itself;
  `slotProps.input`/`inputProps` target MUI's *hidden* native input, so labelling those
  looks like a fix and changes nothing a user can reach.

### Fixed (failure-path audit)
- **A malformed API payload rendered a blank white page.** Measured: a 200 whose body was
  not a FeatureCollection left `#root` empty — no message, no controls — because
  `ImpactPanel` read `.length` of undefined inside a render and React unmounted the
  tree. An unreadable scene record did the same. Both are now rejected at the store
  boundary, and an `AppErrorBoundary` turns any remaining render throw into a sentence
  rather than a white page.
- **A network failure reported two different codes** depending on which branch produced
  it: `NETWORK` for a non-axios value, `HTTP_0` (a nonsense status) for an axios error
  with no response. One condition, one code now.
- **A thrown non-`Error` value lost its content**, becoming "Unknown error" — a shrug
  where `String(error)` had the diagnosis.

### Known gaps
- Failure paths now covered (20/20 in `verify-ux.mjs`): unreachable backend, 404, 5xx,
  malformed payload, slow network, phone viewport, keyboard, accessible names, reduced
  motion. Still uncovered: a scene with no terrain, and a plan with no interventions.
- **Cesium's own credit logo link has no accessible name.** Third-party attribution markup
  (`.cesium-credit-wrapper`) — excluded from the accessibility check rather than stripped,
  because removing it would break Cesium's attribution terms.
- **Still no `STATUS.md` test-count badge or `docs/FILE_STRUCTURE.md` dependency map**, both
  of which `CLAUDE.md` expects. The file map in particular is now out of date: `cameraRig.ts`,
  `techMaterial.ts` and `viewModes.ts` are new modules.
- Unit coverage is now 46 tests: the DEM sampler, the camera geometry, the plan-scoping
  geometry (`pointInRing`, `ringCentroid`, `rotateRing`, `translateRing` — exported for
  this purpose), the store reducers including the ADR-024 in-place-write trap, and the
  API error normaliser. The overlay layers and the online 3D assets loader are untested.
- Added `docs/FILE_STRUCTURE.md` (the dependency map this file was previously missing)
  and `STATUS.md` (measured test counts, what works, what does not).
