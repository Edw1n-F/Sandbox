# Status

What actually works, measured rather than asserted. Counts were produced by running the
suites on **2026-10-13**; nothing here is a target.

**Tests: 498 backend + 56 frontend unit**, all green. Lint and typecheck are clean.
The chat's model call is the one thing no suite exercises — gap 9.

> **Division of labour.** The frontend is being built by another process in parallel;
> this file is the single shared status page, so each party updates its own section and
> leaves the other's alone. Backend counts were re-measured at **498** on 2026-10-13
> across all fourteen suites (`test_run`, 30/30; `test_agent`, 10/10 and new). The
> previously documented **488** was thirteen suites, before `test_agent` existed and
> before `test_run` and `test_ventilation` each gained a check. The old note that
> `test_ventilation`'s 45th check "fails on this host only" is gone: it was an
> absolute one-second timing bound, and it is now a ratio measured in-process, so it
> is **45/45** under parallel load. `scipy` is required by `test_ventilation` and
> `test_contract`; both were run with it present.

| Suite | Count | Command |
|---|---|---|
| backend `test_design_metrics` | 90/90 | `python tests/test_design_metrics.py` |
| backend `test_scenario` | 66/66 | `python tests/test_scenario.py` |
| backend `test_ventilation` | 45/45 | `python tests/test_ventilation.py` |
| backend `test_ledger` | 42/42 | `python tests/test_ledger.py` |
| backend `test_ai_plan` | 41/41 | `python tests/test_ai_plan.py` |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_hydrology` | 32/32 | `python tests/test_hydrology.py` |
| backend `test_run` | 30/30 | `python tests/test_run.py` — the live run loop |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_hourly_storm` | 27/27 | `python tests/test_hourly_storm.py` |
| backend `test_contract` | 23/23 | `python tests/test_contract.py` |
| backend `test_constants` | 18/18 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_agent` | 10/10 | `python tests/test_agent.py` — grounding + fact assembly, no key |
| frontend unit (`vitest`) | 56/56 | `npm test` |
| frontend rendering (`verify-3d.mjs`) | 38/38 ⚠ | `npm run verify:3d` — not re-run 13 Oct; needs live servers |
| frontend robustness (`verify-ux.mjs`) | 20/20 ⚠ | `npm run verify:ux` — not re-run 13 Oct; needs live servers |

The two frontend `verify-*` suites are **not** part of `npm test`: they need the dev
server on `:5173` and the backend on `:8000`, and folding them in would let a missing
server read as a pass. Their figures are carried from 12 Oct and marked, not re-measured.

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
  (ADR-024: local only, not persisted) and are read by **Run simulation**, which
  sends them as per-run inputs.
- **Live run loop** — `POST /api/scenes/{id}/run` applies the active plan's
  interventions to the land-class grid, re-runs the project's own SCS-CN
  generation/retention arithmetic at the requested rainfall, re-solves the
  level-pool fill and re-renders **both** overlays, then returns the
  baseline-vs-scenario ledger and a plain-language explanation. A run at 450 mm
  reproduces the committed overlay exactly (3,170,180.3 m³, 208.05 ha — pinned by
  `test_run`). 0.7 s warm; the terrain conditioning and the sky-view/shade
  rasters are cached, since neither changes with a slider.
- **GenAI plans** — NL to a draft FeatureCollection, with a badge that distinguishes
  "LLM ready", "no LLM key" and "LLM status unknown".
- **Chat: grounded answer cards** — `POST /api/scenes/{id}/chat` and
  `/chat/stream` (SSE). The run's own numbers are handed to the model as a FACTS
  block; the answer comes back as a card (title / sentence / cause / todo / stats
  / confidence / caveats / followups) that the UI renders field by field. Three
  steps are streamed as they happen, and every number in the card is checked
  against the run by code before it is shown. **Live against a real OpenRouter
  key**: two consecutive calls returned `source: model` with real titles and
  stats; one was fully grounded, the other had its derived "95 %" flagged as a
  number the run did not produce. 40–55 s per answer on the free tier (gap 10).
  With no model configured the same card is assembled by code and labelled
  `source: "template"` — a printout, not an interpretation, and it says so.

### The three simulations

Stages 3–6 of [`docs/research/ch05_way_forward.md`](docs/research/ch05_way_forward.md) §5.2, built 9 Oct 2026. Each
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
- **Hourly storm simulation — the time axis.** `hourly_storm_simulation` +
  `GET /api/scenes/{id}/drainage/hourly`: rain level and drainage capability,
  hour by hour, with the per-cell retention state carried forward (a pool filled
  at hour 2 has less room at hour 5). Reference storms are cited (the 2023-09-07
  record storm, the DSD 50-year design block, the Black Rain threshold); a
  custom hyetograph is accepted. The whole-simulation identity
  `rain_in == stored_gain + drained + standing` is **enforced** (raises on a
  leak). The drainage capability is the DSD published 134 mm/h rural design
  standard, stated as a PROXY in every response — no pipe data exists for this
  site and none is invented. The no-drainage counterfactual
  (`drained_m3_per_hour=0`) is the capability read: on the 2023-09-07 storm it
  is the difference between 0 and **1,515,884 m³** of standing water.
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
citations are in the research chapters under
[`docs/research/`](docs/research/README.md) — chapters 1 to 3 for these three
axes, chapter 5 for the roadmap this implements.

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

Numbering follows [`docs/research/ch05_way_forward.md`](docs/research/ch05_way_forward.md) §5.2. Dependency
ordered: a stage may only begin when the raster and the interface it consumes
already exist and are tested.

| Stage | What | State |
|---|---|---|
| 0 | Research, cited and recorded | **done** |
| 1 | `design_metrics.py` — geometry metrics each tied to a published rule | **done** |
| 2 | One `evaluate(scenario)` returning three separate deltas, never one score | **done** |
| 3 | Scenario-vs-baseline comparison with **stated** weights | **done** — `compare()` refuses to rank without them |
| 4 | Heat: sky-view factor, blended and anchored to a published LST/HKO normal | **done** — SVF, the blend, and the anchor: the HKO Lau Fau Shan July band is on disk, verified by `fetch_heat_anchor.py --check`; `anchorSource: "hko_station"`. `HEAT_INDEX` itself remains hand-authored (see gap 8) |
| 5 | Wind: break the NE≡SW symmetry; least-cost-path corridors | **done** — symmetry broken twice: `directional_exposure` reads the arrangement, and each corridor now carries `entryEdge` + `firstObstructionMeanM` (the canonical march attached per direction), so a NE reading and a SW reading are no longer interchangeable. The path cells remain identical for opposite winds — exact geometry, stated in the limitation |
| 6 | Drainage: D8 fill-and-spill cascade, mass-balance assertion, HAND cross-check | **done** — and the routed response now carries a `reconciliation` block putting the level-pool headline and the routed cascade side by side under the same rainfall, with their shared identity (`runoffInM3 == unstoredM3`) stated and checked |
| 7 | Expose the three axes to the LLM as an objective, never as a number | **done** — `massing_fit.py` + `GET /buildings/{id}/fit` + the `assess_building_fit` tool: the model states an objective enum and constraints, the ledger does the arithmetic. 32 tests |

### Next, in order

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

Ordered by what would hurt a user first, numbered **1–16** in this revision. The
list held 29 entries before; eleven of those are closed and have been **removed**
rather than struck through, because their rationale now lives in
[`CHANGELOG.md`](CHANGELOG.md) and that is where history belongs. Four pairs that
were really one job are merged, so 18 open entries became 16.

**A number from the 29-entry revision does not mean the same thing here.** The
table below is the translation, and it is the only place the old numbering is
still authoritative.

| Old | New | What happened |
|---|---|---|
| 1 | — | Closed 13 Oct — cumulative project selection (`planIds[]`) |
| 2 + 11 | 1 | Merged — the parameter card is the container the missing-assumption questions need |
| 3 | 2 | Renumbered |
| 4 + 7 | 3 | Merged — real footprints remove the direction-blindness for free |
| 5, 6 | — | Closed 13 Oct — land classes, per-intervention storage depth |
| 8 + 9 | 4 | Merged — both are "an intervention is a primitive, not a shape" |
| 10 | 5 | Renumbered |
| 12 | 6 | Renumbered |
| 13, 14, 16 | — | Closed 13 Oct — stats rows, sanity checks, per-run output dir |
| 15 | 7 | Renumbered |
| 17 | 8 | Renumbered |
| 18, 19, 22 | — | Closed 13 Oct — suite store isolation, error states, grounding ratios |
| 20 | 9 | Closed except its residual, carried below as entry 9 |
| 21 | 10 | Renumbered |
| 23 + 26 | 11 | Merged — both are the wind geometry's unfinished business |
| 24 | 12 | Renumbered |
| 25 | 13 | Renumbered |
| 27 | 14 | Renumbered |
| 28 | 15 | Closed except its residual, carried below as entry 15 |
| 29 | 16 | Renumbered |

### Gaps against the brief

1. **No validated parameter card, and user-written interventions never ask for
   missing assumptions.** Two halves of the same hole, which is why they are one
   entry. **The card** is the biggest structural gap: the brief puts it between
   "read scenario" and "calculate", and nothing of it exists. The numbers are
   scattered across the ledger and the stats dict; there is no single object
   carrying total site area, pond/wetland retained or removed, bare ground,
   explicit storage, soil group, or source + date + confidence per assumption. Add
   `parameter_card.py` plus an endpoint, cached on (plan ids + intervention hash)
   so a rainfall-only change reuses the land-cover half. **The questions** are what
   the card is for: `ai_plan` silently assumes where a user-written intervention
   replaced land, what surface it is, how deep the storage goes and where it
   overflows. The brief wants `questions[]` (location, footprint, land replaced,
   surface type, usable depth, overflow, vegetation), a confirm/edit card, and the
   label "Preliminary, user-defined scenario" on the run that follows. Today the
   instruction applies directly — no review step, no label — and the parameter card
   is the object those answers belong in.
2. **The project catalog is a placeholder.** Four projects, two phases each, and
   each phase is an English sentence piped through `ai_plan` — so placement is
   re-derived and non-deterministic, with no predefined footprint, land-cover
   conversion or stated assumptions. Add `backend/data/projects.json` with fixed
   footprint rings, land-cover conversion, assumed environmental changes and
   source/date/confidence per field. This also makes runs reproducible, which a
   demo needs. (`ProjectCatalog.tsx` flags this in the panel rather than hiding it.)

### The model, its geometry, and the AI's reach

3. **The base model is procedural, not real — so it is also direction-blind.**
   All 2,639 envelopes are the same 30 m × 30 m axis-aligned square tiled on a 45 m
   pitch (`BLOCK_CELLS = 6`, `GAP_CELLS = 3`); only height varies, and
   `height_source` is `ASSUMED_DISTRIBUTION_NOT_SURVEYED`. Two consequences are one
   fix. **Orientation:** `cell_fad` is 180°-periodic, so the NE and SW cost surfaces
   are bit-identical and their corridor paths are the same cells — there is no
   orientation to recover, only to introduce, and real footprints supply it for
   free. **Age:** a real build-year/phase join (old CSDI `BDBIAR` records) is blocked
   for the same reason — joining age records to *invented* blocks is a spatial
   coincidence, but against real footprints it becomes a genuine overlap test.
   Frontal area density additionally *cannot* be made direction-sensitive for any
   orientation; that is exact geometry, not a gap to close later.
   **The replacement is measured, not hypothetical.** Overture Maps' `buildings`
   theme is reachable from this host — OSM is not, see the `make_citymodel.py`
   docstring — and holds **5,743 footprints** inside the study-area bbox. Measured
   on release `2026-09-23.1`: median footprint **69 m²** (mean 184, max 19,378)
   against a uniform 900 m² today; **20.6 % are not simple quads** (6.1 %
   six-vertex, 5.2 % seven-to-nine, 8.1 % ten or more, max 245 vertices); and only
   **57.7 % sit within 5° of axis-aligned**, so ~42 % carry a genuine orientation.
   Honest limit: heights are *not* in the data — 18 of 5,743 carry `height` and
   1,092 carry `num_floors` — so height stays modelled, but conditioned on real
   area and storeys. **The trap:** `make_heatmap.py:82` reads `citymodel_hsk.json`
   directly, so a photoreal mesh with no matching lon/lat footprint GeoJSON leaves
   every heat, shade, SVF and wind number computed from the old fake blocks; the
   contract must include footprints as lon/lat GeoJSON, not only a mesh. Not yet
   ingested.
4. **An intervention is an axis-aligned slab, not a shaped object.** Two
   placeholders compound. **The footprint:** `extent_ring` and `_fraction_ring`
   both emit five-point axis-aligned rings, and `_clip_to_scope` states the
   assumption in its own docstring — so a reservoir is a bounding box extruded
   14 m, not a basin. `_components` already computes the true cell mask;
   contouring it would give an intervention the shape of the ground it sits on.
   **The massing:** `STYLE_FOR_KIND` gives each kind a single `extrusionHeight`
   (reservoir 14 m, green space 6 m), so every proposal of a kind is the same
   slab — no rim, no depth profile, no concept object, where the brief asks for a
   "visual overlay or concept object" per suggestion.
5. **AI suggestions are not generated, and Pin/Unpin does not exist.** The chips
   are hardcoded strings (`ChatPanel.tsx:57`) and a grep for `pin` returns zero
   hits anywhere. Add `suggest_interventions(run_result, grounding)` over the
   existing `reservoir_sites` / `green_sites` / `paving_sites` pools, returning
   2–3 draft cards (name, type, footprint, why, required area, storage or cooling
   mechanism, evidence status); add `pinned: bool` to `InterventionRow`; drop
   unpinned + untoggled features on the next run.
6. **The chat cannot change the model.** `app/agent/chat_service.py` answers but
   exposes no tools — "no skills, no tools the model can call" is deliberate.
   NL → geometry *does* exist (`parse_massing_intent` → `massingOps` →
   `applyMassingOps`) but is reachable only via `POST /scenes/{id}/ai-plan`, so
   the chat panel and the geometry engine are unconnected. **One op vocabulary
   serves all four cases** — existing buildings, project toggles, AI suggestions
   and generated glTF — so this is one path, not four, and the op shape already
   exists, which is what makes it the cheapest large win.

### The run, its outputs, and the data behind them

7. **No water-pour animation.** The wet grid is a static PNG swap. The cheapest
   honest version precomputes ~6 frames at increasing volume and steps them —
   terrain-based accumulation, clearly not a timing model.
8. **`HEAT_INDEX` is hand-authored, and the heat anchor is ~20 km away.** The
   index weights (`paved 1.00, bare 0.85, green 0.40, wetland 0.20, water 0.05`,
   plus floodable 0.40 and tank 1.00) have no published provenance and they sit in
   the retention path, so they touch drainage numbers too. It is the weakest number
   in the project and says so (`heatIndexSource: "Hand-authored screening index;
   awaiting verification"`). The anchor is an *air*-temperature climatology from
   HKO Lau Fau Shan (July 2015–2024, `[26.4, 32.7] °C`), not skin temperature and
   not a site measurement. A real Landsat 8/9 LST scene over the site is the
   documented fix.

### The chat agent

9. **The chat's model call is covered only by hand, and threads are not
   persisted.** `tests/test_agent.py` covers the pure half of `app/agent/` —
   number grounding and fact assembly — with no key and no network, but the
   provider client has no automated test: the LLM call itself is verified by hand
   against a live key. Threads are not stored either — run state is sent by the
   client per request (ADR-024), so a reload loses the thread. The second half is
   a design choice rather than an oversight, and the UI says so; the first is a
   real coverage hole.
10. **The free tier is slow and throttled, and the fallback is slower.** Measured
    40–55 s per answer. Google's pool returned `429 …
    limit_source=upstream_provider_shared_pool` for `gemma-4-31b-it:free`, so every
    live call fell through to `nemotron-3-ultra-550b-a55b:free`. Three mitigations
    are in place — `MAX_TOKENS=2000` (900 starved reasoning models: 382 thinking
    tokens, then `finish_reason=length` and a truncated card), one unstreamed retry
    when a stream yields nothing, and `LLM_FALLBACK_MODELS` — but a throttled
    primary still costs a wasted round trip before the rescue runs. A paid model id
    removes all three.

### Wind, terrain, storage, and other tracked limitations

11. **Wind is built but not surfaced, and its geometry has two encodings.**
    `ventilation.py` (frontal area density + least-cost corridors) computes and
    tests, but nothing surfaces it in the UI or the run; the brief marks this
    optional — wire it in as a third overlay, kept out of the flood/heat numbers.
    The same geometry is meanwhile written down twice: `projected_width_m` and
    `frontal_area_density` exist in both `ventilation.py` (dict-based) and
    `design_metrics.py` (typed `Footprint`), and `ch05` says the typed one is
    canonical, so `ventilation.py` should delegate rather than grow a third
    encoding. Already closed at the *response* level (Stage 5c): each corridor
    carries `entryEdge` and `firstObstructionMeanM`, so the two wind readings are
    no longer interchangeable even though the underlying paths still cannot
    separate NE from SW.
12. **The outlet water-level condition is unresolved.** `sea_level_scenario`
    models retention *submergence* rather than outfall capacity, which is where sea
    level would actually bite at this site; it needs pipe and outfall data the
    project does not have, so the honest option is to declare it rather than invent
    the network. Related: two drainage readings coexist by design — the routed
    fill-and-spill cascade and the level-pool solve — and are shown side by side
    with a `reconciliation` block; the headline `bufferDeficitM3` stays on the
    level-pool path and deliberately did not move.
13. **The terrain is ~90 m SRTM-derived, and the docs are stale about the fix.**
    `ch05 §5.4` calls the 5 m DTM "request-gated", but it is now freely available
    (and Open3Dhk's set includes a TIN terrain model). Also: a run re-solves the
    water level on the conditioned surface but does not re-condition it, so a DEM
    change needs `make_wetgrid.py` re-run (`make_wetgrid.py` writes the
    Priority-Flood conditioning and the D8 receiver once, ~9 s, offline).
14. **Storage is JSON files, not PostGIS.** The documented target is not started;
    the seam is `app/db.py` and `scene_service.py` only.
15. **Coverage stops short of two view modules, and two known limits stand.**
    Unit coverage does not reach `view/onlineProps.ts` (the online 3D assets
    loader) or `view/interventionLayer.ts`; the 56 unit tests cover the DEM
    sampler, camera geometry, plan-scoping geometry, store reducers and the API
    error normaliser instead. Separately and honestly so: Cesium's credit logo
    link has no accessible name (third-party markup `.cesium-credit-wrapper`,
    excluded rather than stripped, because removing it would break Cesium's
    attribution terms); and two-finger scroll does not pan in Cesium 3D (a
    cursor-anchored ground pan measured 39 m for a 2 km request) and is disabled —
    left-drag pans instead.
16. **The temperature slider is a display preset, not physics.** Above 35 °C the
    run applies `make_heatmap`'s documented heatwave stretch — a render transform
    on the same field; the land-class heat index does not know about air
    temperature, and `test_run` asserts it is bit-identical across presets. A
    planner who drags temperature and sees no rainfall-side change is looking at a
    model that is being honest, not at a broken slider — but the brief says
    temperature "simulates heatwaves", so it is a scope risk if read literally.
    Making temperature move the physics needs a real energy-balance term this
    project does not have and should not imply.

**Deliberately out of scope — the docs already say why, so don't build them:**
typhoon surge with jacking, a trained perception encoder, the generative
emulator, kappa validation against 2017 flood records, and InVEST (SCS-CN is
implemented directly from USDA TR-55 Table 2-2, and that deviation is declared).

## Honesty constraints that must survive future work

- **Generative 3D belongs on proposals, not on the existing district.** An
  AI-generated mesh is fiction, so it must not be presented as the current state
  of HSK — the existing buildings have to come from measured data (gap 3).
  Interventions are already labelled `draft_pending_human_review`, so a generated
  glTF is honest there and dishonest as "what is there now".

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
  the closed set in [`docs/research/ch05_way_forward.md`](docs/research/ch05_way_forward.md) §5.2. `status` is never a verdict.
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
- **A test must never assert that bad data exists.** `test_ledger` once scanned
  the intervention store for badly-sited polygons and required the set to be
  non-empty — so it passed *because* stale test residue was lying around, and
  cleaning the store up broke a real check. Every guard must now build its own
  adversarial fixture and assert the guard fires on it. If deleting a file can
  turn a test red, the test was measuring the wrong thing.
- **The chat explains a run; it never computes one.** `build_facts` reads the
  run result the planner is looking at and hands it over verbatim. Recomputing a
  simulation inside a Q&A would turn a fast reply into a 0.7 s run *and* let the
  answer drift from the numbers on screen. No new arithmetic in `app/agent/`.
- **Grounding is code, never the model.** A card's numbers are checked against
  the facts by `grounding.check_card`, and a card that fails is either revised
  once or shown with the problems named. The model is never asked whether its
  own numbers are right.
- **`source: "template"` is not a degraded mode to hide.** When no model is
  configured the card is assembled by code from the run's own figures and the UI
  says so. Every number in it is one the run actually produced, which is more
  than a hallucinated model answer can claim.
