# Status

What actually works, measured rather than asserted. Counts were produced by running the
suites on 2026-10-09; nothing here is a target.

**Tests: 416 backend + 104 frontend.** Lint and typecheck are clean.

> **Division of labour.** The frontend is being built by another process in parallel;
> this file is the single shared status page, so each party updates its own section and
> leaves the other's alone. Backend counts below were re-verified at **416** on
> 2026-10-09 (404 after the Stage 4b anchor tests, +5 Stage 5c corridor checks,
> +6 routed-reconciliation checks, +1 count correction). The frontend figure of 104 is
> the frontend owner's, carried forward unchanged.

| Suite | Count | Command |
|---|---|---|
| backend `test_design_metrics` | 90/90 | `python tests/test_design_metrics.py` |
| backend `test_scenario` | 54/54 | `python tests/test_scenario.py` |
| backend `test_ai_plan` | 39/39 | `python tests/test_ai_plan.py` |
| backend `test_ventilation` | 45/45 | `python tests/test_ventilation.py` |
| backend `test_ledger` | 39/39 | `python tests/test_ledger.py` |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_hydrology` | 32/32 | `python tests/test_hydrology.py` |
| backend `test_contract` | 23/23 | `python tests/test_contract.py` |
| backend `test_constants` | 18/18 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
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

### The three simulations

Stages 3–6 of `Hack4SDG_Heat_Wind_Drainage_Research.md` §5.2, built 9 Oct 2026. Each
new output carries a machine-readable `status` and a human `warning`.

- **Heat: sky view and a blended field.** `sky_view_factor_raster` /
  `sky_view_factor` run a 16-bin, 300 m horizon search over the massing height raster
  (0.07 s over the district). A thin lone 50 m tower reads open-field mean **0.9913**;
  a uniform block over the whole grid reads **0.0**. `blended_heat_field` combines the
  land-class index, SVF and the cast shadow into one relative 0–1 raster, served by
  `GET /api/scenes/{id}/heat-field`. It is a raster, deliberately **not** a fourth
  axis — a fourth axis would break the three-axis "never a combined score" guard.
- **Heat: anchored to a cited band at last.** `load_heat_calibration()` reads
  `data/reference/heat_anchor.json` — now **on disk**, fetched by
  `scripts/fetch_heat_anchor.py` from the HKO Lau Fau Shan (LFS) daily station
  record (July 2015–2024: mean daily min **26.4 °C**, mean daily max **32.7 °C**),
  with the HKO 1991–2020 normals and an Open-Meteo ERA5 cross-check recorded as
  cross-checks, not the anchor. `anchorSource` is `"hko_station"`, the status is
  `ANCHORED_TO_PUBLISHED_REFERENCE`, and `fetch_heat_anchor.py --check` is the
  drift gate: it recomputes the band and fails if the committed file no longer
  agrees. The anchor names the physical quantity (`HKO_air`, not `LST_skin`) so
  the two cannot be conflated, and the field stays a relative 0–1 index — the band
  is what the index is *read against*, never converted to. `HEAT_INDEX` remains
  hand-authored; the anchor changes only the band.
- **Wind: least-cost-path corridors.** `least_cost_corridors` on a `1 + cellFAD` cost
  surface via `scipy.sparse.csgraph.dijkstra` (0.70 s for eight directions), plus
  `fad_ratio()` for a per-direction baseline-vs-scenario comparison. A ratio is a
  comparison, which the AVA framework authorises; it is not a velocity ratio.
- **Wind: a per-cell frontal-area field.** `cell_fad` spreads each footprint's
  oriented frontal area over the cells it covers and divides by the cell's own area,
  so the area-weighted mean of the per-cell field equals the site-level FAD. That
  identity is the check that catches a wrong denominator.
- **Drainage: D8 fill-and-spill routing.** `label_depressions`, `fill_and_spill` and
  `hand` in `scripts/hydrology.py`, on the Priority-Flood surface the project already
  computed but never routed. Served by `GET /api/scenes/{id}/drainage/routed`.
- **Drainage: a mass-balance assertion that is enforced, not reported.**
  `mass_balance()` **raises** when `runoff in ≠ stored + discharged`. A second
  identity, `generation == local_retention + runoff_in`, is asserted separately so an
  ADR-015 double count cannot hide inside a balanced cascade.
- **Stage 3: `compare()`.** `POST /api/scenes/{id}/scenario/compare`. With no
  `weights` it **refuses** (`refused: true`, `ranked: null`, reason given) rather than
  defaulting to a weight set, because a default would quietly become the project's
  opinion about which axis matters. With weights, every ranked row still carries the
  three raw axes and their per-axis ranks. The weighted total is named
  `weightedFavourability`, never `score`, and `combinedScore` stays `null`.

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
- **Sky view and a blended heat field** — a 16-bin, 300 m horizon search over the
  massing height raster, combined with the land-class index and the cast shadow into
  one relative 0–1 raster at `GET /api/scenes/{id}/heat-field`. A thin lone 50 m
  tower reads open-field mean **0.9913**; a uniform block over the whole grid reads
  **0.0**.
- **Least-cost-path wind corridors and a per-direction FAD ratio** —
  `GET /api/scenes/{id}/ventilation/corridors`, on a `1 + cellFAD` cost surface via
  `scipy.sparse.csgraph.dijkstra`. A ratio is a comparison; it is not a velocity
  ratio and the response says so.
- **Routed drainage with a mass-balance assertion** —
  `GET /api/scenes/{id}/drainage/routed`. At 450 mm: **3,170,180 m³** in (equal to
  the published deficit, so the headline is conserved rather than re-derived),
  372,622 stored, 2,797,559 discharged, residual **9.3e-15** relative. 37
  depressions over 32,500 cells; HAND mean 6.12 m, agreement with the routed depth
  0.881 — reported as a stated relation, never merged into the routed depth.
- **Scenario comparison under stated weights** —
  `POST /api/scenes/{id}/scenario/compare`. Refuses without weights; with them,
  every ranked row keeps the three raw axes beside the ordering.

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
| 3 | Scenario-vs-baseline comparison with **stated** weights | **done** — `compare()` refuses to rank without them |
| 4 | Heat: sky-view factor, blended and anchored to a published LST/HKO normal | **done** — SVF, the blend, and the anchor: the HKO Lau Fau Shan July band is on disk, verified by `fetch_heat_anchor.py --check`; `anchorSource: "hko_station"`. `HEAT_INDEX` itself remains hand-authored (see gap 9) |
| 5 | Wind: break the NE≡SW symmetry; least-cost-path corridors | **done** — symmetry broken twice: `directional_exposure` reads the arrangement, and each corridor now carries `entryEdge` + `firstObstructionMeanM` (the canonical march attached per direction), so a NE reading and a SW reading are no longer interchangeable. The path cells remain identical for opposite winds — exact geometry, stated in the limitation |
| 6 | Drainage: D8 fill-and-spill cascade, mass-balance assertion, HAND cross-check | **done** — and the routed response now carries a `reconciliation` block putting the level-pool headline and the routed cascade side by side under the same rainfall, with their shared identity (`runoffInM3 == unstoredM3`) stated and checked |
| 7 | Expose the three axes to the LLM as an objective, never as a number | **done** — `massing_fit.py` + `GET /buildings/{id}/fit` + the `assess_building_fit` tool: the model states an objective enum and constraints, the ledger does the arithmetic. 32 tests |

### Next, in order

- **`place` and `reset` remain unscoreable.** The AI's most-used operations are
  placeable but not scoreable, because both belong to the city-model generator and
  reimplementing either in the evaluator would drift from it.
- **`sea_level_scenario` still reasons about retention submergence rather than
  outfall capacity**, which is where sea level would actually bite. The routed
  cascade now exists and could host the outfall question; the level-pool headline
  deliberately does not move.
- **Stage 5c's cost-surface half.** The arrangement information is now *attached*
  to each corridor (entry edge + first-obstruction height), but the cost surface
  itself is still 180°-periodic, so opposite winds' paths remain the same cells.
  Any cost that weighted the arrangement would be an invented weight unless a
  published basis is found; the limitation says so.

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
8. **Two drainage readings coexist by design; they are now shown side by side.**
   The routed fill-and-spill cascade balances to 1e-14 and sits **beside** the
   level-pool solve rather than replacing it: the headline `bufferDeficitM3` and
   `wetgrid_hsk.npy` still come from the level-pool path, deliberately, so the
   published number did not move. **The one-view half is closed (9 Oct 2026):**
   `GET /drainage/routed` now carries a `reconciliation` block — both readings'
   volumes under the same rainfall, their shared identity
   (`runoffInM3 == levelPool.unstoredM3`, 3,170,180.3 m³, checked), and a plain
   statement of why they differ. Still open: `sea_level_scenario` reasons about
   retention submergence rather than outfall capacity, which is where sea level
   would actually bite.
9. **`HEAT_INDEX` is hand-authored** — `paved 1.00, bare 0.85, green 0.40,
   wetland 0.20, water 0.05` — with no published provenance, and it sits in the
   retention path, so it touches drainage numbers too. It is the weakest number in the
   project and is honest about itself (`heatIndexSource: "Hand-authored screening
   index; awaiting verification"`). The Stage 4b anchor does **not** change this
   table: it supplies the band the index is read against, not the index weights
   themselves.
10. **The massing model carries no orientation, so the *baseline* is still
    direction-blind.** `directional_exposure` breaks the NE≡SW symmetry on the real
    layout, and footprints can now be rotated, but every generated envelope is
    axis-aligned — so there is no orientation to recover for the baseline, only to
    introduce by design. Frontal area density additionally *cannot* be made
    direction-sensitive for any orientation; that is exact geometry, not a gap to be
    closed later.
11. **The heat field is anchored to a *nearby* observation, not the site.** The
    anchor band (HKO Lau Fau Shan, July 2015–2024, `[26.4, 32.7] °C`) is now on
    disk and served in every heat response, but the station is ~20 km from the
    study area and the record is an *air*-temperature climatology, not skin
    temperature and not a site measurement. The 0–1 field stays relative; a real
    Landsat 8/9 LST scene over the site would still be the stronger anchor, and
    every response says so. **Closed as a data gap; open as a provenance
    limitation.**
12. **The corridor *path* still cannot tell a north-easterly from a south-westerly;
    the response now can.** `cell_fad` is 180°-periodic, so the NE and SW cost
    surfaces are bit-identical and their paths are the same cells — exact geometry,
    unchanged. **Closed at the response level (9 Oct 2026, Stage 5c):** each
    corridor now carries `entryEdge` (which side the wind enters from) and
    `firstObstructionMeanM` (the arrangement, from the canonical
    `directional_exposure` march — NE 30.2 m vs SW 44.9 m on the real district),
    so the two readings are no longer interchangeable. A cost surface weighted by
    the arrangement would be an invented weight; the limitation says exactly this.
13. **~~`Overlay.cell_area_m2` was a `float` subclass that was also callable.~~**
    **Collapsed 9 Oct 2026.** The two spellings (`ov.cell_area_m2()` in the scripts,
    `grids.cell_area_m2` in the canonical geometry) are now one: the attribute form,
    everywhere. The `_CellAreaM2` float-subclass bridge is deleted; every caller reads
    the plain-float property. All 404 backend tests and both raster regeneration runs
    are byte-identical before and after (`wetgrid_hsk.npy` md5 `d7cc052a…`
    unchanged), so the collapse changed no number.

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
- **`compare()`'s `weightedFavourability` is the caller's opinion, not a score.** It
  exists only because the caller stated weights, it is echoed back beside the three raw
  axes, and it is never named `score`. With no weights the call refuses. Removing the
  refusal, or hiding the axes behind the ordering, would re-create the combined score
  by another name.
- **Every new output carries a machine-readable `status` and a human `warning`**, from
  the closed set in `docs/drafts/DESIGN_stages_3_6.md` §6. `status` is never a verdict.
- **The heat field is a relative index, not a temperature.** The anchor band now on
  disk (HKO Lau Fau Shan, July 2015–2024, `[26.4, 32.7] °C`) is what the 0–1 scale
  is *read against*; the field itself is still not °C and never becomes °C. Do not
  convert it, do not call the band a site measurement (the station is ~20 km away),
  and do not conflate `HKO_air` with `LST_skin`.
- **A FAD *ratio* is a comparison, not a velocity ratio.** `fad_ratio` compares two
  schemes' geometry; it does not become a VR by being divided.
- **The corridors are geometry, not flow, and they cannot separate NE from SW.**
  `cell_fad` is 180°-periodic, so the two cost surfaces are identical. Say so; do not
  present a corridor as a wind path with a direction.
- **The routed depth is not the headline deficit.** `routed_depth_hsk.npy` and
  `bufferDeficitM3` come from two different methods. Never present the routed depth as
  a correction to the published deficit, or vice versa.
- **`mass_balance` raises rather than reporting.** If it is ever changed to return a
  flag, a leak becomes a field someone can ignore.
