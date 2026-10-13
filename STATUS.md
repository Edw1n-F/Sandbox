# Status

The live state of the HSK / San Tin planning sandbox, **measured on 2026-10-13**.
Nothing here is a target. The architecture rationale is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Current state

### The user flow, today

1. **Open the app.** The Cesium viewer boots on the HSK / San Tin study area with
   terrain, OSM imagery and the real building model already drawn — **396 real
   building footprints** extracted from Open3Dhk tiles fused with Overture, and
   the textured 3D Tiles meshes standing on the terrain, in an oblique default
   view. No Cesium Ion token required.
2. **Move around.** Left-drag pans, right-drag orbits, wheel/pinch zooms, arrow
   keys nudge, and three buttons reframe (3D / top-down / reset). Two view modes:
   **survey** (daylight, lit meshes) and **tactical** (dark, emissive neon,
   height ramp, 200 m grid, bloom) — same data, and the tactical legend says so.
3. **Pick projects.** The catalog is multi-select: tick several phases and the run
   merges them, so *cumulative* effects are one run rather than one project at a
   time.
4. **Set the scenario.** Rainfall and temperature sliders write to the live scene
   (ADR-024: local only, never persisted).
5. **Run simulation.** `POST /api/scenes/{id}/run` applies the selected plans'
   interventions to the land-class grid, re-runs the project's own SCS-CN
   generation/retention arithmetic at the chosen rainfall, re-solves the level-pool
   fill, re-renders **both** overlays, and returns the baseline-vs-scenario ledger,
   a plain-language explanation, and any sanity warnings. 0.7 s warm; the terrain
   conditioning and the sky-view/shade rasters are cached, since neither moves with
   a slider. A run at 450 mm reproduces the committed overlay exactly
   (3,170,180.3 m³, 208.05 ha — pinned by `test_run`).
6. **Read the result.** The stats panel shows seven rows, the ledger shows the
   signed delta, and `sanityChecks` warns (never raises) when storage exceeds
   runoff, wet area exceeds the site, impervious share passes 100 %, cumulative
   coverage passes a cap, or CN leaves 30–100.
7. **Ask the chat.** The run's own numbers are handed to the model as a FACTS
   block; the answer returns as a card (title / sentence / cause / todo / stats /
   confidence / caveats / followups) rendered field by field, with every number
   checked against the run by code before it is shown. Three steps stream as they
   happen. With no key configured the same card is assembled by code and labelled
   `source: "template"`.
8. **Generate a plan (optional).** A sentence becomes a draft FeatureCollection via
   `ai_plan`, badged "LLM ready" / "no LLM key" / "LLM status unknown", and the NL
   geometry ops (`scale_all`, `set_site`, `rotate`, `move`, `resize`, `reset`)
   apply scoped by a ring. This path is driven from the plan panel, not
   from the chat. Ops are idempotent — a second application cannot compound.

Empty states are named rather than blank: a scene with no terrain, or a plan with
no interventions, renders a `SceneNotice` instead of an empty surface.

### What works

**Scene and model**

- `GET /api/scenes/{id}` boots terrain + OSM imagery + overlays, token-free.
- **396 real building footprints** (`buildings_hsk.json`, `buildings_meta.json`),
  extruded and terrain-grounded from Open3Dhk tiles fused with Overture, plus the
  textured 3D Tiles meshes (`buildings3DUrl`). **This is surveyed geometry, not
  procedural massing** — the old 2,639-square `citymodel_hsk.json` placeholder is
  deleted, and the `buildingsMeta.status` says
  `DERIVED_FROM_OPEN3DHK_AND_OVERTURE`.
- Per-run output paths (`data/rasters/run/<runId>/…`), so two concurrent planners
  cannot overwrite each other's overlay.

**The run**

- Live run loop as above, with a `stats.ratios` block published *by the run*
  (`unstoredOverRunoff` plus four siblings) so a model that quotes a derived
  percentage grounds it as a fact — the chat itself does no arithmetic.

**The three axes** (full findings, sources and citations in
[`docs/research/`](docs/research/README.md) ch01–ch03; ch05 is the roadmap this
implements)

- **Heat — a °C surface-temperature field, anchored to observed LST.** Every
  footprint is swept along the sun vector (real shadow casting), a 16-bin, 300 m
  horizon search gives sky-view factor, and `cell_fad` gives wind shelter. These
  geometry terms express each cell's *deviation from the site mean* around an
  **observed Landsat LST surface-temperature scene** (the 2022-09-03 capture,
  resampled 30 m → 5 m), producing a real °C field (`temperature_field.py`) shown
  as an anomaly map — blue cooler / neutral average / red hotter — so a
  well-designed site reads near-neutral and only genuine hot spots go red. The
  temperature slider sets the scenario mean °C; air temperature is an *estimate*
  (surface − 12 °C), never a measurement.
- **Wind — one geometry, read per direction.** `cell_fad` spreads each footprint's
  oriented frontal area over its cells, and the area-weighted mean of that field
  equals the site-level FAD — the identity that catches a wrong denominator.
  Least-cost corridors on a `1 + cellFAD` surface via Dijkstra; `fad_ratio()`
  compares two schemes' geometry. A ratio is a **comparison, not a velocity
  ratio**.
- **Drainage — routed, with the arithmetic enforced.** `evaluate(scenario)` returns
  **three separate deltas** with their own units plus a trade-off list, never one
  score; a routed D8 fill-and-spill cascade whose `mass_balance()` **raises** on a
  leak; an hourly storm simulation carrying the per-cell retention state forward,
  with `rain_in == stored_gain + drained + standing` enforced; and a
  `reconciliation` block showing the routed cascade and the level-pool headline
  side by side under the same rainfall.
- **Comparison under stated weights.** `compare()` **refuses** without weights,
  because a default would quietly become the project's opinion. With weights, every
  ranked row keeps the three raw axes beside the ordering; the total is named
  `weightedFavourability`, never `score`, and `combinedScore` stays `null`.
- **Design metrics** — height dispersion, site coverage, ground-level void
  fraction, shade fraction, each tied to a published rule quoted in the output;
  `massing_fit.py` + `GET /buildings/{id}/fit` exposes the objective to the LLM as
  an enum while the ledger does the arithmetic.

---

## Checks and disclaimers

### Test suites

Counts measured by running every suite on **2026-10-13 (evening)**. Lint and
typecheck are clean. **Six of the sixteen backend suites are not green**: three
carry stale pins, three stop before finishing. Every one of the six is pinned to
state that the real-data switches replaced — the procedural massing envelope, the
~90 m synthetic DEM, and the 0–1 heat index — and none of them touches the run
loop, the contract, the temperature field or the overlay masking, which are green.

| Suite | Result | Command |
|---|---|---|
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_hourly_storm` | 27/27 | `python tests/test_hourly_storm.py` |
| backend `test_run` | 30/30 | `python tests/test_run.py` — the live run loop |
| backend `test_ai_plan` | 41/41 | `python tests/test_ai_plan.py` |
| backend `test_contract` | 23/23 | `python tests/test_contract.py` |
| backend `test_constants` | 18/18 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_temperature_field` | 13/13 | `python tests/test_temperature_field.py` |
| backend `test_agent` | 10/10 | `python tests/test_agent.py` — grounding, no key |
| backend `test_overlay_masking` | 3/3 | `python tests/test_overlay_masking.py` |
| backend `test_design_metrics` | **86/91** | 5 pins stale: 395/396 envelopes convert, 3 shade values, `green 0.40` |
| backend `test_ledger` | **39/42** | 3 pins stale: 2 artefact md5s, the sea-level ground range |
| backend `test_hydrology` | **28/32** | 4 pins stale: depression count and cells, HAND cells, headline |
| backend `test_ventilation` | **20/45, then `TypeError`** | `test_ventilation.py:124` divides a list |
| backend `test_building_fit` | **4 checks, then `IndexError`** | reads `features[900]`; the model holds 396 |
| backend `test_scenario` | **does not run** | imports `placement_footprints`, absent from `ai_plan` |
| frontend unit (`vitest`) | 36/36 | `npm test` |
| frontend rendering (`verify-3d.mjs`) | 38/38 | `npm run verify:3d` — needs live servers |
| frontend robustness (`verify-ux.mjs`) | 20/20 | `npm run verify:ux` — needs live servers |

**385 backend assertions ran: 373 green, 12 red, and 131 never ran at all.**
Report the three crashing suites as crashing rather than as failures — a suite
that dies on line 124 hides the 25 checks behind it, and a pinned count of 900
buildings was never a statement about the world, only about the placeholder that
used to be there.

The two `verify-*` suites are **not** part of `npm test`: they need the dev server
on `:5173` and the backend on `:8001`, and folding them in would let a missing
server read as a pass. Their counts date from 12 Oct; the rest were run on 13 Oct.

The chat's model call is the one thing no suite exercises — gap 9.

`scipy` is required by `test_ventilation` and `test_contract`. Two timing
assertions use host-measured ratios rather than absolute wall-clock bounds, so they
hold under parallel load.

### Deliberately absent, and why

Each of these is a design position, not an unfinished feature.

- **No combined score.** The three axes are incommensurable — a fraction of ground,
  a dimensionless blockage, cubic metres — and the ventilation axis has no
  threshold to normalise against. A single number would settle the
  shade-versus-blockage trade-off silently. `combinedScore` is `null` with the
  reason in the response.
- **No verdict.** TC 1/06 §6 states the framework "does not provide an absolute
  benchmark standard", and the AVA policy adds that whether the better option meets
  a standard "cannot be known". The tool compares and never grades.
- **No comfort index.** No UTCI, PET or SET — those need air-temperature and
  humidity series the project does not have.
- **No CFD.** Frontal area density and first-hit height are geometry, and say so.
- **Out of scope, and the docs already say why:** typhoon surge with jacking, a
  trained perception encoder, the generative emulator, kappa validation against
  2017 flood records, and InVEST (SCS-CN is implemented directly from USDA TR-55
  Table 2-2, and that deviation is declared).

---

## Gaps

Fifteen items, ordered by what hurts a user first. Each states the issue and
what closes it. Four (8, 11, 12, 15) are now marked *resolved* and kept in place
so the numbering stays stable.

### 1. No validated parameter card, and nothing estimates a user intervention's own numbers

**The issue.** The brief puts a validated parameter object between "read scenario"
and "calculate", and nothing of it exists. The numbers are scattered across the
ledger and the stats dict: there is no single object carrying total site area,
pond/wetland retained or removed, bare ground, explicit storage, soil group, or a
source + date + confidence per assumption.

The second half is what such an object is *for*. A user-written intervention is a
hypothesis, and its numbers are not written down anywhere to be read — a basin's
usable depth, the permeability of proposed paving, the area actually reworked — so
something has to **estimate** them. Nothing does. `ai_plan` emits `params` for the
operations (`multiplier`, `heightM`, `degrees`, `dx_m`/`dy_m`, `targetLon`/
`targetLat`, `widthScale`/`depthScale`, `count`) and never a storage depth, a
permeability or an area, so every intervention falls back to the fixed value in
`INTERVENTION_EFFECTS`. The sink is wired and empty: `apply_interventions` reads
`params.storageDepthM`, validates it against 0.05–10 m, and emits the per-cell
storage override the run scores, while `run_service` renders the wet grid and the
heat map from that same grid. So an estimated number moves the stats and both
overlays at once. Today the instruction applies with none of it, no review step,
and none of the runs that follow carry the "Preliminary, user-defined scenario"
label the brief asks for.

**What to do.** Add `parameter_card.py` plus an endpoint, cached on (plan ids +
intervention hash) so a rainfall-only change reuses the land-cover half. The card
is the container, and it holds two kinds of field: the **plan-level** figures (site
area, soil group, pond and wetland retained or removed, bare ground, explicit
storage) and the **per-intervention estimates** (location, footprint, land
replaced, surface type, usable depth, overflow, vegetation), each carrying a
source, a date and a confidence.

Have `ai_plan` produce those estimates as the intervention's `params`, and return
`questions[]` for the ones it cannot ground, so the card confirms and edits rather
than asking from nothing. **The model supplies an assumption, never a result** —
it states a depth, and the code multiplies it by the area it computed and keeps
the arithmetic, which is the same boundary Decision 14 draws. Then label the run
that follows.

### 2. The project catalog is a placeholder

**The issue.** Four projects, two phases each, and each phase is an English
sentence piped through `ai_plan` — so placement is re-derived on every run and
non-deterministic, with no fixed footprint, land-cover conversion or stated
assumptions. A demo needs reproducible runs, and this makes every run different.

**What to do.** Add `backend/data/projects.json`: fixed footprint rings, the
land-cover conversion each implies, the assumed environmental changes, and a
source / date / confidence per field. (`ProjectCatalog.tsx` already flags this in
the panel rather than hiding it.)

### 3. The base model is real footprints, not procedural blocks — done

**The issue, resolved.** The 2,639 square placeholder envelopes are deleted. In
their place, **396 real building footprints** (`buildings_hsk.json`) are extracted
from Open3Dhk tiles fused with Overture, with heights from the 90th percentile of
tile surface minus tile terrain, and the textured 3D Tiles meshes render at
`buildings3DUrl`. `buildings_meta.json` carries the provenance
(`DERIVED_FROM_OPEN3DHK_AND_OVERTURE`, footprint source, height rule, generated
date). The heat/shade/SVF/wind analyses all read `buildings_hsk.json`, so every
number is computed from measured geometry, not invented blocks.

**Remaining (stage 2/3, not blocking stage 1).** Height is still a model (p90
surface-minus-terrain), not a surveyed storey count — an honest limit, stated in
`buildings_meta.heightSource`. A build-year/phase join (old CSDI `BDBIAR`) is now
*possible* against real footprints but not yet done.

### 4. An intervention is an axis-aligned slab, not a shaped object

**The issue.** Two placeholders compound. **The footprint:** `extent_ring` and
`_fraction_ring` both emit five-point axis-aligned rings, and `_clip_to_scope`
states the assumption in its own docstring — so a reservoir is a bounding box
extruded 14 m, not a basin. **The massing:** `STYLE_FOR_KIND` gives each kind a
single `extrusionHeight` (reservoir 14 m, green space 6 m), so every proposal of a
kind is the same slab — no rim, no depth profile, no concept object, where the
brief asks for a "visual overlay or concept object" per suggestion.

**What to do.** `_components` already computes the true cell mask; **contour it** so
an intervention takes the shape of the ground it sits on. Then give each kind a
massing profile — rim, depth profile, concept object — rather than one flat
extrusion.

### 5. AI suggestions are not generated, and Pin/Unpin does not exist

**The issue.** The suggestion chips are hardcoded strings (`ChatPanel.tsx:57`) and
a grep for `pin` returns zero hits anywhere.

**What to do.** Add `suggest_interventions(run_result, grounding)` over the existing
`reservoir_sites` / `green_sites` / `paving_sites` pools, returning 2–3 draft cards
(name, type, footprint, why, required area, storage or cooling mechanism, evidence
status). Add `pinned: bool` to `InterventionRow`, and drop unpinned + untoggled
features on the next run.

### 6. No one owns the natural-language → sandbox loop

**The issue.** The loop the brief asks for — a planner types *"add this"*, *"turn
this"*, *"remove this"* and the sandbox reflects it — has no owner end to end, and
two of the three verbs have nothing behind them.

`app/agent/chat_service.py` answers but exposes no tools ("no skills, no tools the
model can call" is deliberate), and `POST /chat` / `POST /chat/stream` return a
card and nothing else. NL → geometry *does* exist (`parse_massing_intent` →
`massingOps` → `applyMassingOps`) but is reachable only via `POST
/scenes/{id}/ai-plan`, so the chat panel and the geometry engine are unconnected.

Then the vocabulary itself. It is seven ops — `scale_all`, `set_site`, `rotate`,
`move`, `resize`, `place`, `reset` — and **not one of them removes anything**, so
*"remove this"* cannot be expressed at all. And `place` adds a procedural massing
envelope: the flat extrusion of gap 4, not a shaped object, so *"add this"*
produces a slab where the brief asks for a concept object. The only glTF in the
repo is the three hardcoded Khronos samples in `view/onlineProps.ts` — a loader,
not a generator, and not connected to any instruction.

**What to do.** Close the loop as one path. Expose the existing ops as
chat-callable, so the op shape the panel's buttons emit is the same shape the model
emits. Add the missing **`remove`** op — until it exists the vocabulary cannot say
*"take this away"*, and the brief asks for removal as plainly as for addition. Make
`place` build an object rather than extrude a slab (the per-kind profile is gap 4's
half; the point here is that an addition is *shaped*).

**The seam, so nothing waits.** An op already carries a `params` dict and
`apply_interventions` already consumes it: a storage depth becomes the per-cell
storage override, and an empty one falls back to the intervention kind's fixed
value. So this path transports the numbers gap 1 estimates without depending on
gap 1 — the loop applies whatever it is handed, and an op carrying no estimates
behaves exactly as it does today. An addition that is generated rather than drawn
lands labelled `draft_pending_human_review`, like every AI suggestion, so a
generated object is honest as a proposal and never as existing state.

**One op vocabulary serves all four cases** — existing buildings, project toggles,
AI suggestions and generated objects — so this is one path, not four, and the op
shape already exists. The manipulation ops are **target-agnostic**: they act on the
massing, not on a pillar, so the loop is buildable and testable against today's
model and does not wait on real footprints. That is what makes it the cheapest
large win on this list.

### 7. No water-pour animation

**The issue.** The wet grid is a static PNG swap.

**What to do.** Precompute ~6 frames at increasing volume and step them —
terrain-based accumulation, clearly labelled as not a timing model.

### 8. The heat field is now anchored to observed LST; the index weights are the residual

**The issue, mostly resolved.** The heat overlay is no longer a hand-authored
0–1 relative index. `temperature_field.py` anchors it to a real **Landsat 8/9
LST surface-temperature scene** over the site (the 2022-09-03 scene, the only
100 %-finite, ~1 %-cloud capture), resampled 30 m → 5 m, and expresses each
cell's *deviation from the site mean* through the geometry terms — land class,
sky-view, shade, and **wind shelter** (`cell_fad`, the frontal-area density for
the prevailing NE wind). The result is a **°C surface-temperature field**, shown
as an anomaly map (blue cooler / neutral average / red hotter), so a
well-designed site reads near-neutral and only genuine hot spots go red.

**What remains, stated honestly.** The `HEAT_INDEX` land-class *weights* are
still hand-authored (only `green 0.64` is measured from the LST calibration) —
but they now express the *relative* geometry delta, not the absolute field, so
the unproven part is a small perturbation on an observed base. Air temperature
is an *estimate* (surface minus a documented 12 °C offset), never a measurement,
and there is still no wind-speed or surface-energy-balance term — wind shelter
is geometry (FAD), not flow.

### 9. The chat's model call is covered only by hand, and threads are not persisted

**The issue.** `tests/test_agent.py` covers the pure half of `app/agent/` — number
grounding and fact assembly — with no key and no network, but the provider client
has no automated test: the LLM call itself is verified by hand against a live key.
Threads are not stored either — run state is sent by the client per request
(ADR-024), so a reload loses the thread.

**What to do.** Add an automated test for the provider client (the real coverage
hole). Thread persistence is a deliberate design choice rather than an oversight —
either keep declaring it, or store the thread.

### 10. The free tier is slow and throttled, and the fallback is slower

**The issue.** Measured 40–55 s per answer. Google's pool returned `429 …
limit_source=upstream_provider_shared_pool` for `gemma-4-31b-it:free`, so every
live call fell through to `nemotron-3-ultra-550b-a55b:free`. Three mitigations are
in place — `MAX_TOKENS=2000` (900 starved reasoning models: 382 thinking tokens,
then `finish_reason=length` and a truncated card), one unstreamed retry when a
stream yields nothing, and `LLM_FALLBACK_MODELS` — but a throttled primary still
costs a wasted round trip before the rescue runs.

**What to do.** A paid model id — a one-line `.env` change — removes all three
problems.

### 11. The outlet water-level condition is now a screening factor, not a hidden limit

**The issue, mostly resolved.** `fill_and_spill` now takes a `tailwater_m`
boundary condition: outlet cells (`receiver == -1`) pond up to the tailwater
surface before spilling off-grid, so the cascade can answer "what if this rain
hits at high tide". A Low/Mean/High tide toggle (HKO Quarry Bay, 0 / 1.4 /
2.2 mCD) threads through the run, the stats, the sidecar and the frontend. The
mechanism is verified: on a synthetic low-lying site, a 1.5 m tailwater holds
back the full runoff volume that would otherwise discharge.

**The honest finding.** The HSK / San Tin site is **landward** — the grid minimum
is 3.41 m, above the 2.2 m high-tide tailwater — so tidal backwater has ~zero
effect here (flood area is unchanged across low/mean/high). The feature is wired
and tested but effectively inert in this demo, and that is stated, not hidden.

**What remains.** True *outfall capacity* under storm surge needs pipe and
outfall data the project does not have; that stays declared out of scope. The
headline `bufferDeficitM3` stays on the level-pool path and deliberately did not
move.

### 12. The terrain is the real 5 m DTM; only the re-condition note remains

**The issue, resolved.** The terrain is **not** ~90 m SRTM. `dem_hsk.npy` /
`elevation_5m` are the real 5 m Open3Dhk / LandsD DTM (`terrain_source =
open3dhk-tiles`, 117×148, 3.4–17.3 m), built by `scripts/tiles_to_rasters.py`
from the `model/terrain(TB)/*.b3dm` tiles on disk. The earlier "~90 m SRTM"
wording here was stale.

**What remains, stated honestly.** It is the *surface* of the LandsD terrain-tile
mesh, not a surveyed bare-earth DTM release. And a run re-solves the water level
on the conditioned surface but does **not** re-condition it, so a DEM change
needs `make_wetgrid.py` re-run (`make_wetgrid.py` writes the Priority-Flood
conditioning and the D8 receiver once, ~9 s, offline).

### 13. Storage is JSON files, not PostGIS

**The issue.** The documented target store is not started; the seam is `app/db.py`
and `scene_service.py` only.

**What to do.** Implement the PostGIS store behind the existing seam. File mode
already satisfies the same API contract, so this is a swap, not a rewrite.

### 14. Coverage stops short of two view modules, and two known limits stand

**The issue.** Unit coverage does not reach `view/onlineProps.ts` (the online 3D
assets loader) or `view/interventionLayer.ts`; the 56 unit tests cover the DEM
sampler, camera geometry, plan-scoping geometry, store reducers and the API error
normaliser instead. Separately, and honestly so: Cesium's credit logo link has no
accessible name (third-party markup `.cesium-credit-wrapper`, excluded rather than
stripped, because removing it would break Cesium's attribution terms); and
two-finger scroll does not pan in Cesium 3D (a cursor-anchored ground pan measured
39 m for a 2 km request) and is disabled — left-drag pans instead.

**What to do.** Add unit tests for the two view modules. Give the credit link an
accessible name if it can be done without breaking the attribution terms. Keep the
pan workaround documented.

### 15. The temperature slider now sets the scenario mean, and says so

**The issue, resolved.** The slider is no longer a silent display-stretch. It now
sets the **scenario mean surface temperature (°C)** — the temperature analogue of
the rainfall slider's intensity — and re-anchors the whole heatmap so its mean
matches, with presets naming the **HKO heat warnings** (*Hot night* ≥28 °C air,
*Very Hot Weather Warning* ≥33 °C, *Extreme heat* ≥35 °C) the way the rainfall
buttons name the rainstorm signals. The spatial pattern (shade, green, wind
shelter) still decides which cells are hotter or cooler than average, so a
well-designed site reads near-neutral rather than "all red". The slider is
labelled: it is surface (skin) temperature anchored to observed Landsat LST, air
is estimated as ~12 °C lower, and it changes no rainfall or runoff figure —
temperature still cannot drive SCS-CN without an evapotranspiration term the
project does not have.

**What remains.** Nothing blocking. The one honest caveat is already stated beside
the slider: this is surface temperature, not air temperature, and the anomaly
display is what makes it a *comparison* tool rather than a wall of red.

---

## Who does what

Three lanes. Each gap below is self-contained, so any lane can start without
waiting on another.

| Owner | Gaps | The lane |
|---|---|---|
| **Lia** | 4, 5, 6, 7, 14, 15 | The interface and the interaction |
| **Chenxu** | 3, 8, 12 | The real 3D and geospatial data |
| **Edwin** | 1, 2, 9, 10, 11, 13 | Model internals, storage and configuration |

### Lia — the interface and the interaction

- **4** — contour the intervention's true cell mask and give each kind a real
  massing profile, so a reservoir is a basin and not a box.
- **5** — generated suggestion cards and Pin/Unpin.
- **6** — the natural-language → sandbox loop: chat-callable ops, the missing
  `remove` op, and shaped additions.
- **7** — the water-pour animation.
- **14** — the two uncovered view modules, the credit-link accessible name, and the
  documented pan limit.
- **15** — the temperature slider: the constrained range, the 35 °C threshold marker,
  and the label saying what the slider does and does not change.

### Chenxu — the real 3D and geospatial data

- **3** — ingest and clean the real footprints (Overture and/or Open3Dhk's LandsD 3D
  Digital Map), reproject to lon/lat, publish the **GeoJSON contract** with a
  storeys-conditioned height, and replace the procedural massing generator so it
  extrudes actual building shapes from those footprints instead of tiling squares.
- **8** — the real Landsat LST scene over the site, which is the heat anchor, plus a
  provenance pass on `HEAT_INDEX`.
- **12** — the terrain is in place (real 5 m Open3Dhk / LandsD DTM); only the
  residual caveat — it is the tile-mesh *surface*, not a surveyed bare-earth DTM —
  needs a line in the pitch wording.

### Edwin — model internals, storage and configuration

- **1** — the parameter card, and the estimate of a user intervention's own
  numbers: the assumptions the run is scored on, with their provenance.
- **2** — `projects.json`: fixed footprints and stated assumptions, so runs are
  reproducible.
- **9** — the provider-client test.
- **10** — the paid model id.
- **11** — the outlet water-level condition.
- **13** — the PostGIS store behind the existing seam.

---

## Constraints that must survive future work

These are the rules the project is built on. Every one of them was a decision, and
each was made because the honest alternative was to quietly publish something
false.

- **Generative 3D belongs on proposals, not on the existing district.** An
  AI-generated mesh is fiction, so it must not be presented as the current state of
  HSK — the existing buildings have to come from measured data (gap 3).
  Interventions are already labelled `draft_pending_human_review`, so a generated
  glTF is honest there and dishonest as "what is there now".
- **The city model is provisional massing, not surveyed buildings.** Keep the
  `provisional` chip and the terrain-grounding disclosure.
- **Rasters are generated artefacts.** Depths come from `wetgrid_hsk.npy`, not the
  PNG.
- **The frontend has no unit test for the renderer's own correctness** beyond the
  two browser suites, and those need a running server. A green `npm test` does not
  mean the map renders.
- **Never publish a combined score across heat, wind and drainage.** The units are
  incommensurable and the axes genuinely conflict. If an ordering is ever shown, its
  weights must be stated *and* the three axes must stay separately visible.
- **Never print a pass/fail verdict on a scheme.** TC 1/06 §6 provides no absolute
  benchmark, and the AVA policy states that whether the better design option meets a
  standard "cannot be known". Comparing schemes is authorised; grading them is not.
- **Frontal area density is not a velocity ratio**, and the two are never
  interchanged. They differ by orders of magnitude and share no calibration.
- **Shade is two solar instants, not a daily mean radiant temperature.** No surface
  energy balance, no air temperature, no humidity. Heat is a *surface* temperature
  field, not an air-temperature or comfort model.
- **Height does not change runoff.** A footprint is already impervious whatever its
  height, so a height-only scheme returns a drainage delta of exactly zero. That is
  arithmetic, not a bug, and the response says so in the axis limitation.
- **`compare()`'s `weightedFavourability` is the caller's opinion, not a score.** It
  exists only because the caller stated weights, it is echoed back beside the three
  raw axes, and it is never named `score`. With no weights the call refuses.
  Removing the refusal, or hiding the axes behind the ordering, would re-create the
  combined score by another name.
- **Every new output carries a machine-readable `status` and a human `warning`**,
  from the closed set in
  [`docs/research/ch05_way_forward.md`](docs/research/ch05_way_forward.md) §5.2.
  `status` is never a verdict.
- **The heat field is a surface temperature anchored to observed LST — and it is
  the one quantity here that is in °C.** The base is the Landsat 8/9 LST scene of
  2022-09-03 resampled 30 m → 5 m, so the absolute level is measured, not modelled;
  the geometry terms (land class, sky view, shade, wind shelter) only move a cell
  away from the site mean. Two conflations must be refused. **`LST_skin` is not
  `HKO_air`**: air is an estimate at surface − 12 °C, and the band on disk (HKO Lau
  Fau Shan, July 2015–2024, `[26.4, 32.7] °C`) is a sanity anchor from a station
  ~20 km away, never a site measurement. And the **anomaly** map is a deviation
  from the site mean, never an absolute temperature. The `HEAT_INDEX` land-class
  weights stay hand-authored except `green 0.64` (measured from the LST
  calibration): they perturb an observed base, they do not constitute it.
- **A FAD *ratio* is a comparison, not a velocity ratio.** `fad_ratio` compares two
  schemes' geometry; it does not become a VR by being divided.
- **The corridors are geometry, not flow, and they cannot separate NE from SW.**
  `cell_fad` is 180°-periodic, so the two cost surfaces are identical. Say so; do
  not present a corridor as a wind path with a direction.
- **The routed depth is not the headline deficit.** `routed_depth_hsk.npy` and
  `bufferDeficitM3` come from two different methods. Never present the routed depth
  as a correction to the published deficit, or vice versa.
- **`mass_balance` raises rather than reporting.** If it is ever changed to return a
  flag, a leak becomes a field someone can ignore.
- **A test must never assert that bad data exists.** `test_ledger` once scanned the
  intervention store for badly-sited polygons and required the set to be non-empty —
  so it passed *because* stale test residue was lying around, and cleaning the store
  up broke a real check. Every guard must build its own adversarial fixture and
  assert the guard fires on it. If deleting a file can turn a test red, the test was
  measuring the wrong thing.
- **The chat explains a run; it never computes one.** `build_facts` reads the run
  result the planner is looking at and hands it over verbatim. Recomputing a
  simulation inside a Q&A would turn a fast reply into a 0.7 s run *and* let the
  answer drift from the numbers on screen. No new arithmetic in `app/agent/`.
- **Grounding is code, never the model.** A card's numbers are checked against the
  facts by `grounding.check_card`, and a card that fails is either revised once or
  shown with the problems named. The model is never asked whether its own numbers
  are right.
- **`source: "template"` is not a degraded mode to hide.** When no model is
  configured the card is assembled by code from the run's own figures and the UI
  says so. Every number in it is one the run actually produced, which is more than
  a hallucinated model answer can claim.
