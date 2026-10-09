# Status

What actually works, measured rather than asserted. Counts were produced by running the
suites on 2026-10-09; nothing here is a target.

**Tests: 258 passing** — backend 167, frontend 91. Lint and typecheck are clean.

| Suite | Count | Command |
|---|---|---|
| backend `test_ai_plan` | 39/39 | `python tests/test_ai_plan.py` |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_ledger` | 24/24 | `python tests/test_ledger.py` |
| backend `test_constants` | 18/18 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_contract` | 11/11 | `python tests/test_contract.py` |
| frontend unit (`vitest`) | 37/37 | `npm test` |
| frontend rendering (`verify-3d.mjs`) | 38/38 | `npm run verify:3d` |
| frontend robustness (`verify-ux.mjs`) | 16/16 | `npm run verify:ux` |

The two frontend `verify-*` suites are **not** part of `npm test`: they need the dev
server on `:5173` and the backend on `:8000`, and folding them in would let a missing
server read as a pass.

## Working

- **Scene chain** — `GET /api/scenes/{id}` boots the viewer with terrain, OSM imagery
  and overlays. No Cesium Ion token.
- **3D city model** — 2,639 procedural massing envelopes, extruded, standing on the
  terrain, with a quantised height ramp in the tactical view. Openable in 3D from an
  oblique default, and orbitable.
- **Massing ops** — `scale_all`, `set_site`, `rotate`, `move`, `place`, `reset`, scoped
  by a ring. Idempotent: a second application cannot compound.
- **Two view modes** — survey (daylight, lit envelopes) and tactical (dark, emissive
  neon, height ramp, 200 m reference grid, bloom). Same data; the tactical legend says
  so.
- **Navigation** — left-drag pan, right-drag orbit, wheel/pinch zoom, arrow keys,
  on-screen orbit/zoom, and three framings (3D / top-down / reset).
- **Scenario controls** — rainfall and temperature sliders write to the live scene
  (ADR-024: local only, not persisted).
- **GenAI plans** — NL to a draft FeatureCollection, with a badge that distinguishes
  "LLM ready", "no LLM key" and "LLM status unknown".

## Known gaps

Ordered by what would hurt a user first.

1. **Failure modes are thinly covered.** A 500 from any endpoint, a slow network, a
   scene with no terrain, and a plan with no interventions all go untested. The
   unreachable-backend path *is* covered now.
2. **No per-building development phase.** The 2,639 envelopes carry no build year, age
   or phase — `height_class` is a *height* class from a random draw and must not be
   relabelled as one. The government source exists and was verified: CSDI **"Building
   information and age records"** (layer `BDBIAR`), whose phase signal is
   `NSEARCH3_E`/`NSEARCH3_C` = **Occupation Permit Date**. Not ingested, because joining
   it to procedurally generated blocks is a spatial coincidence rather than an ID match,
   and presenting that as authority would overclaim. See `CHANGELOG.md`.
3. **Two-finger scroll does nothing.** It cannot pan reliably in Cesium 3D — a
   cursor-anchored ground pan was implemented and measured at 39 m for a 2 km request —
   and it was disabled rather than left zooming. Left-drag pans. Zoom is on pinch,
   Cmd/Ctrl+scroll, the buttons and the keyboard.
4. **Unit coverage is 37 tests** over the DEM sampler, the camera geometry, the
   plan-scoping geometry (`pointInRing`, `rotateRing`, `translateRing`) and the store
   reducers. The API client and the overlay layers are untested.
5. **Cesium's credit logo link has no accessible name.** Third-party attribution markup
   (`.cesium-credit-wrapper`); excluded from the accessibility check rather than stripped,
   because removing it would break Cesium's attribution terms.
6. **Storage is JSON files, not PostGIS**, and the documented target is not started. The
   seam is `app/db.py` and `app/services/scene_service.py` only.

## Honesty constraints that must survive future work

- The city model is **provisional massing, not surveyed buildings.** Keep the
  `provisional` chip and the terrain-grounding disclosure.
- Rasters are **generated artefacts**. Depths come from `wetgrid_hsk.npy`, not the PNG.
- The frontend has **no unit test for the renderer's own correctness** beyond the two
  browser suites, and those need a running server. A green `npm test` does not mean the
  map renders.
