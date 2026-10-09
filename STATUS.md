# Status

What actually works, measured rather than asserted. Counts were produced by running the
suites on 2026-10-09; nothing here is a target.

**Tests: 271 backend + 104 frontend.** Lint and typecheck are clean.

> **Division of labour.** The frontend is being built by another process in parallel;
> this file is the single shared status page, so each party updates its own section and
> leaves the other's alone. Backend counts below were re-verified at **271** on
> 2026-10-09 18:0x. The frontend figure of 104 is the frontend owner's, carried forward
> unchanged — the two numbers happen to coincide at 271 and must not be read as one.

| Suite | Count | Command |
|---|---|---|
| backend `test_design_metrics` | 54/54 | `python tests/test_design_metrics.py` |
| backend `test_scenario` | 27/27 | `python tests/test_scenario.py` |
| backend `test_ai_plan` | 39/39 | `python tests/test_ai_plan.py` |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_ledger` | 24/24 | `python tests/test_ledger.py` |
| backend `test_ventilation` | 23/23 | `python tests/test_ventilation.py` |
| backend `test_constants` | 18/18 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_contract` | 11/11 | `python tests/test_contract.py` |
| frontend unit (`vitest`) | 46/46 | `npm test` |
| frontend rendering (`verify-3d.mjs`) | 38/38 | `npm run verify:3d` |
| frontend robustness (`verify-ux.mjs`) | 20/20 | `npm run verify:ux` |

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

## Design choices → heat, wind and drainage

The goal is to make a design choice *legible*: this change, in this place, moves
shade, ventilation and drainage by these amounts. Full findings, sources and
citations are in `docs/HEAT_AND_WIND_PROGRESS.md`; the design it implements is
`Hack4SDG_Heat_Wind_Drainage_Research.md` (workspace root, Chapters 1–5).

The organising fact is that these are **not three features but one geometry read
three ways**. Hong Kong's own air-ventilation guidance states that a designer
controls exactly two comfort variables — *"maximize wind speed and minimize solar
radiation"* (Ng & Cheng, *Building and Environment* 44 (2009) 1478–1488) — and
both are functions of the same height, spacing, orientation, podium coverage and
ground-level permeability.

### Working

- **Shade from geometry** — NOAA solar position; every footprint swept along the
  sun vector. Real shadow casting, not a land-cover lookup, so it moves when the
  massing moves. December 09:00 16.2%, 14:00 22.1%; June mean 11.5%.
- **Per-axis scenario evaluation** — `POST /api/scenes/{id}/scenario/evaluate`
  returns **three separate deltas** with their own units, plus a plain-language
  trade-off list. Raising the western blocks 40% taller gives **more shade
  (+3.0 pp), more wind blockage (FAD +0.084) and no change to drainage** — the
  conflict is the finding.
- **Directional exposure** — mean first-hit height over a massing height raster,
  per direction. NE reads 30.2 m against SW 44.9 m, so the screen separates the
  published prevailing axis (NE/ENE/E, 40.8% of the year, AVRG73) from its
  opposite.
- **Design metrics, each tied to a published rule** —
  `GET /api/scenes/{id}/design-metrics`: height dispersion, site coverage,
  ground-level void fraction, shade fraction, with the rule quoted in the output.

### Deliberately absent, and why

- **No combined score.** The three axes are incommensurable — a fraction of
  ground, a dimensionless blockage, cubic metres — the ventilation axis has no
  threshold to normalise against, and a single number would settle the
  shade-versus-blockage trade-off silently. `combinedScore` is `null` with the
  reason in the response.
- **No verdict.** TC 1/06 §6 states the framework "does not provide an absolute
  benchmark standard"; the AVA policy adds that whether the better option meets a
  standard "cannot be known". So the tool compares and never grades.
- **No comfort index.** No UTCI, PET or SET — those need air temperature and
  humidity series we do not have.
- **No CFD.** Frontal area density and first-hit height are geometry, and say so.

## Stages

Numbering follows `Hack4SDG_Heat_Wind_Drainage_Research.md` §5.2. Dependency
ordered: a stage may only begin when the raster and the interface it consumes
already exist and are tested.

| Stage | What | State |
|---|---|---|
| 0 | Research, cited and recorded | **done** |
| 1 | `design_metrics.py` — geometry metrics each tied to a published rule | **done** |
| 2 | One `evaluate(scenario)` returning three separate deltas, never one score | **done** |
| 3 | Scenario-vs-baseline comparison with **stated** weights | **next** |
| 4 | Heat: sky-view factor, blended and anchored to a published LST/HKO normal | **partial** — shadows done; SVF and anchoring outstanding |
| 5 | Wind: break the NE≡SW symmetry; least-cost-path corridors | **partial** — symmetry broken; corridors blocked on scipy, now installed |
| 6 | Drainage: D8 fill-and-spill cascade, mass-balance assertion, HAND cross-check | **not started** |
| 7 | Expose the three axes to the LLM as an objective, never as a number | **not started** |

### Next, in order

- **Stage 3, comparison.** `compare()` must rank with *stated* weights and refuse
  to rank without them. A default weight set would quietly become the project's
  opinion about which axis matters, which is the judgement TC 1/06 says cannot be
  settled. Cheap; unblocks the demo.
- **Stage 4, sky-view factor.** The horizon search over the height raster is the
  missing half of the heat axis, and `rasterise_height()` now provides its input.
- **Stage 4b, anchoring.** `HEAT_INDEX` is still hand-authored with no published
  provenance and is the weakest number in the project. It needs a real Landsat
  8/9 LST scene or the HKO 1991–2020 normals to become defensible. Until then it
  stays labelled as hand-authored.
- **Stage 5b, corridors.** Least-cost-path ventilation corridors on an inverse-FAD
  cost surface, via `scipy.sparse.csgraph.dijkstra`. scipy 1.18.1 is installed for
  this one purpose and is recorded in `requirements.txt` with that scope stated.
- **Stage 6, drainage.** Mostly **wiring**: `hydrology.py` already computes
  Priority-Flood, D8 receivers and flow accumulation, and `flow_accum_hsk.npy` is
  saved and then never used. Turn it into a fill-and-spill cascade guarded by a
  mass-balance assertion — runoff in = stored + discharged — as a runtime check
  and a unit test.
- **Stage 7, the LLM as policy.** With three real axes the planner can state an
  objective — "cut the deficit here without losing shade" — and the evaluator
  reports what that costs on each axis. Neither half can silently corrupt the
  other, which is the point.

Scoring-side items (rung declaration, business model) are tracked in
`docs/RUNG_AND_BUSINESS.md`.

## Known gaps

Ordered by what would hurt a user first.

1. **A malformed payload used to render a blank white page, and the class is only
   partly closed.** Two separate causes found on 2026-10-09 — an unreadable scene
   record and a FeatureCollection with no `features`, both throwing inside a render and
   unmounting the tree. Both are now fixed at the store boundary and an error boundary
   catches the rest, and 5xx / malformed-payload / slow-network are covered. What is
   *not* covered: a scene with no terrain, and a plan with no interventions.
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
4. **Unit coverage is 46 tests** over the DEM sampler, the camera geometry, the
   plan-scoping geometry (`pointInRing`, `rotateRing`, `translateRing`), the store
   reducers and the API error normaliser. The overlay layers and the online 3D assets
   loader are untested.
5. **Cesium's credit logo link has no accessible name.** Third-party attribution markup
   (`.cesium-credit-wrapper`); excluded from the accessibility check rather than stripped,
   because removing it would break Cesium's attribution terms.
6. **Storage is JSON files, not PostGIS**, and the documented target is not started. The
   seam is `app/db.py` and `app/services/scene_service.py` only.

### Gaps in the heat / wind / drainage work

7. **The scenario evaluator cannot score `place` or `reset`.** Both need footprints
   generated on unbuilt paved land, or the seeded heights restored, and both belong to
   the city-model generator. Reimplementing either in the evaluator would drift from it,
   so the response lists them under `unsupportedOps` and says why rather than
   approximating. Real gap before the demo: the AI's most-used operations are
   placeable but not scoreable.
8. **Drainage is a level-pool solve, not a routed cascade.** Unstored volume is placed
   by solving for one water-surface elevation, which is a horizontal plane over the
   basin and is **not routed downhill cell by cell**. `hydrology.py` already computes
   Priority-Flood, D8 receivers and flow accumulation and `flow_accum_hsk.npy` is
   written and never read. Stage 6.
9. **`HEAT_INDEX` is hand-authored** — `paved 1.00, bare 0.85, green 0.40,
   wetland 0.20, water 0.05` — with no published provenance, and it sits in the
   retention path, so it touches drainage numbers too. It is the weakest number in the
   project and is honest about itself (`heatIndexSource: "Hand-authored screening
   index; awaiting verification"`). Stage 4b.
10. **The massing model carries no orientation, so the *baseline* is still
    direction-blind.** `directional_exposure` breaks the NE≡SW symmetry on the real
    layout, and footprints can now be rotated, but every generated envelope is
    axis-aligned — so there is no orientation to recover for the baseline, only to
    introduce by design. Frontal area density additionally *cannot* be made
    direction-sensitive for any orientation; that is exact geometry, not a gap to be
    closed later.

## Honesty constraints that must survive future work

- The city model is **provisional massing, not surveyed buildings.** Keep the
  `provisional` chip and the terrain-grounding disclosure.
- Rasters are **generated artefacts**. Depths come from `wetgrid_hsk.npy`, not the PNG.
- The frontend has **no unit test for the renderer's own correctness** beyond the two
  browser suites, and those need a running server. A green `npm test` does not mean the
  map renders.
- **Never publish a combined score across heat, wind and drainage.** The units are
  incommensurable and the axes genuinely conflict. If an ordering is ever shown, its
  weights must be stated *and* the three axes must stay separately visible.
- **Never print a pass/fail verdict on a scheme.** TC 1/06 §6 provides no absolute
  benchmark, and the AVA policy states that whether the better design option meets a
  standard "cannot be known". Comparing schemes is authorised; grading them is not.
- **Frontal area density is not a velocity ratio**, and the two are never interchanged.
  They differ by orders of magnitude and share no calibration.
- **Shade is two solar instants, not a daily mean radiant temperature.** No surface
  energy balance, no air temperature, no humidity. Heat is not a temperature.
- **Height does not change runoff.** A footprint is already impervious whatever its
  height, so a height-only scheme returns a drainage delta of exactly zero. That is
  arithmetic, not a bug, and the response says so in the axis limitation.
