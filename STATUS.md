# Status

The live state of the HSK / San Tin planning sandbox, **measured on 2026-10-13**.
Nothing here is a target. The architecture rationale is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Current state

### The user flow, today

1. **Open the app.** The Cesium viewer boots on the HSK / San Tin study area with
   terrain, OSM imagery and the city model already drawn — 2,639 *provisional*
   massing envelopes standing on the terrain, in an oblique default view. No
   Cesium Ion token required.
2. **Move around.** Left-drag pans, right-drag orbits, wheel/pinch zooms, arrow
   keys nudge, and three buttons reframe (3D / top-down / reset). Two view modes:
   **survey** (daylight, lit envelopes) and **tactical** (dark, emissive neon,
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
   geometry ops (`scale_all`, `set_site`, `rotate`, `move`, `place`, `reset`) apply
   scoped by a ring. Ops are idempotent — a second application cannot compound.

Empty states are named rather than blank: a scene with no terrain, or a plan with
no interventions, renders a `SceneNotice` instead of an empty surface.

### What works

**Scene and model**

- `GET /api/scenes/{id}` boots terrain + OSM imagery + overlays, token-free.
- 2,639 procedural massing envelopes, extruded and terrain-grounded, with a
  quantised height ramp in tactical view. **This is provisional massing, not
  surveyed buildings** — the `provisional` chip and the terrain-grounding
  disclosure stay.
- Per-run output paths (`data/rasters/run/<runId>/…`), so two concurrent planners
  cannot overwrite each other's overlay.

**The run**

- Live run loop as above, with a `stats.ratios` block published *by the run*
  (`unstoredOverRunoff` plus four siblings) so a model that quotes a derived
  percentage grounds it as a fact — the chat itself does no arithmetic.

**The three axes** (full findings, sources and citations in
[`docs/research/`](docs/research/README.md) ch01–ch03; ch05 is the roadmap this
implements)

- **Heat — shade from geometry, anchored at last.** Every footprint is swept along
  the sun vector (real shadow casting, not a land-cover lookup), so shade moves
  when the massing moves. A 16-bin, 300 m horizon search gives sky-view factor
  (0.07 s district-wide); `blended_heat_field` combines the land-class index, SVF
  and the cast shadow into one relative 0–1 raster at `GET /heat-field`. The field
  is a **relative index, never °C**; the HKO Lau Fau Shan July band on disk is what
  it is *read against*, verified by `fetch_heat_anchor.py --check`.
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

Counts were produced by running the suites on **2026-10-13**; all green. Lint and
typecheck are clean. The chat's model call is the one thing no suite exercises —
gap 9.

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
| frontend rendering (`verify-3d.mjs`) | 38/38 | `npm run verify:3d` — needs live servers |
| frontend robustness (`verify-ux.mjs`) | 20/20 | `npm run verify:ux` — needs live servers |

**498 backend assertions across fourteen suites, 56 frontend unit tests.** The two
`verify-*` suites are **not** part of `npm test`: they need the dev server on
`:5173` and the backend on `:8000`, and folding them in would let a missing server
read as a pass. Their counts date from 12 Oct; the rest were run on 13 Oct.

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

Sixteen open items, ordered by what hurts a user first. Each states the issue and
what closes it.

### 1. No validated parameter card, and user-written interventions never ask for missing assumptions

**The issue.** The brief puts a validated parameter object between "read scenario"
and "calculate", and nothing of it exists. The numbers are scattered across the
ledger and the stats dict: there is no single object carrying total site area,
pond/wetland retained or removed, bare ground, explicit storage, soil group, or a
source + date + confidence per assumption. The second half is what such an object
is *for*: `ai_plan` silently assumes where a user-written intervention replaced
land, what surface it is, how deep the storage goes and where it overflows. Today
the instruction applies directly — no review step, and none of the runs that follow
carry the "Preliminary, user-defined scenario" label the brief asks for.

**What to do.** Add `parameter_card.py` plus an endpoint, cached on (plan ids +
intervention hash) so a rainfall-only change reuses the land-cover half. Have
`ai_plan` return `questions[]` (location, footprint, land replaced, surface type,
usable depth, overflow, vegetation), render a confirm/edit card, and label the run
that follows. One entry because the card **is** the container the answers belong in.

### 2. The project catalog is a placeholder

**The issue.** Four projects, two phases each, and each phase is an English
sentence piped through `ai_plan` — so placement is re-derived on every run and
non-deterministic, with no fixed footprint, land-cover conversion or stated
assumptions. A demo needs reproducible runs, and this makes every run different.

**What to do.** Add `backend/data/projects.json`: fixed footprint rings, the
land-cover conversion each implies, the assumed environmental changes, and a
source / date / confidence per field. (`ProjectCatalog.tsx` already flags this in
the panel rather than hiding it.)

### 3. The base model is procedural, not real — so it is also direction-blind and cannot be dated

**The issue.** All 2,639 envelopes are the same 30 m × 30 m axis-aligned square
tiled on a 45 m pitch (`BLOCK_CELLS = 6`, `GAP_CELLS = 3`); only height varies, and
`height_source` is `ASSUMED_DISTRIBUTION_NOT_SURVEYED`. Two consequences are one
fix. **Orientation:** `cell_fad` is 180°-periodic, so the NE and SW cost surfaces
are bit-identical and their corridor paths are the same cells — there is no
orientation to recover, only to introduce, and real footprints supply it for free.
**Age:** a real build-year/phase join (old CSDI `BDBIAR` records) is blocked for the
same reason — joining age records to *invented* blocks is a spatial coincidence,
but against real footprints it becomes a genuine overlap test. Frontal area density
additionally *cannot* be made direction-sensitive for any orientation; that is
exact geometry, not a gap to close later.

**The replacement is measured, not hypothetical.** Overture Maps' `buildings` theme
is reachable from this host — OSM is not, see the `make_citymodel.py` docstring —
and holds **5,743 footprints** inside the study-area bbox. Measured on release
`2026-09-23.1`: median footprint **69 m²** (mean 184, max 19,378) against a uniform
900 m² today; **20.6 % are not simple quads** (6.1 % six-vertex, 5.2 % seven-to-nine,
8.1 % ten or more, max 245 vertices); and only **57.7 % sit within 5° of
axis-aligned**, so ~42 % carry a genuine orientation. Honest limit: heights are
*not* in the data — 18 of 5,743 carry `height` and 1,092 carry `num_floors` — so
height stays modelled, but conditioned on real area and storeys.

**What to do.** The work has two halves: the data, then the geometry built on it.

- **The data half:** ingest the real footprints (Overture, and/or Open3Dhk's
  LandsD 3D Digital Map that Chenxu is already cleaning), reproject to lon/lat, and
  publish them as a **GeoJSON contract** with a storeys-conditioned height. Also
  the real terrain (gap 13) ships on the same contract.
- **The geometry half:** replace the procedural massing generator so it consumes
  real footprints and extrudes actual building shapes — outline, orientation, a
  height conditioned on storeys — instead of tiling squares.

**The trap, which is why the contract is not optional.** `make_heatmap.py:82` reads
`citymodel_hsk.json` *directly*. A photoreal mesh with no matching lon/lat footprint
GeoJSON leaves every heat, shade, SVF and wind number computed from the old fake
blocks. The contract must carry footprints as lon/lat GeoJSON, **not only a mesh**.
Not yet ingested.

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

### 6. The chat cannot change the model

**The issue.** `app/agent/chat_service.py` answers but exposes no tools — "no
skills, no tools the model can call" is deliberate. NL → geometry *does* exist
(`parse_massing_intent` → `massingOps` → `applyMassingOps`) but is reachable only
via `POST /scenes/{id}/ai-plan`, so the chat panel and the geometry engine are
unconnected.

**What to do.** Bridge them. **One op vocabulary serves all four cases** — existing
buildings, project toggles, AI suggestions and generated glTF — so this is one
path, not four, and the op shape already exists. That is what makes it the cheapest
large win on this list.

### 7. No water-pour animation

**The issue.** The wet grid is a static PNG swap.

**What to do.** Precompute ~6 frames at increasing volume and step them —
terrain-based accumulation, clearly labelled as not a timing model.

### 8. `HEAT_INDEX` is hand-authored, and the heat anchor is ~20 km away

**The issue.** The index weights (`paved 1.00, bare 0.85, green 0.40, wetland 0.20,
water 0.05`, plus floodable 0.40 and tank 1.00) have no published provenance **and
they sit in the retention path**, so they touch drainage numbers too. It is the
weakest number in the project and says so (`heatIndexSource: "Hand-authored
screening index; awaiting verification"`). The anchor is an *air*-temperature
climatology from HKO Lau Fau Shan (July 2015–2024, `[26.4, 32.7] °C`), not skin
temperature and not a site measurement.

**What to do.** Find a published provenance for the weights, or declare them as an
authored screening assumption more loudly. Replace the anchor with a real Landsat
8/9 LST scene over the site.

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

### 11. Wind is built but not surfaced, and its geometry has two encodings

**The issue.** `ventilation.py` (frontal area density + least-cost corridors)
computes and tests, but nothing surfaces it in the UI or the run; the brief marks
this optional — wire it in as a **third overlay**, kept out of the flood/heat
numbers. Meanwhile the same geometry is written down twice: `projected_width_m` and
`frontal_area_density` exist in both `ventilation.py` (dict-based) and
`design_metrics.py` (typed `Footprint`), and `ch05` says the typed one is canonical.

**What to do.** Surface wind as a third overlay endpoint and layer. Have
`ventilation.py` **delegate** to `design_metrics.py`'s typed `Footprint` rather than
grow a third encoding. Each corridor already carries `entryEdge` and
`firstObstructionMeanM`, so the two wind readings stay distinguishable even though
the underlying paths still cannot separate NE from SW.

### 12. The outlet water-level condition is unresolved

**The issue.** `sea_level_scenario` models retention *submergence* rather than
outfall capacity, which is where sea level would actually bite at this site; it
needs pipe and outfall data the project does not have. Related: two drainage
readings coexist by design — the routed fill-and-spill cascade and the level-pool
solve — shown side by side with a `reconciliation` block, and the headline
`bufferDeficitM3` stays on the level-pool path and deliberately did not move.

**What to do.** Declare it rather than invent the network. The routed cascade now
exists and could host the outfall question; the headline stays where it is.

### 13. The terrain is ~90 m SRTM-derived, and the docs are stale about the fix

**The issue.** `ch05 §5.4` calls the 5 m DTM "request-gated", but it is now freely
available — and Open3Dhk's set includes a TIN terrain model. Also: a run re-solves
the water level on the conditioned surface but does **not** re-condition it, so a
DEM change needs `make_wetgrid.py` re-run (`make_wetgrid.py` writes the
Priority-Flood conditioning and the D8 receiver once, ~9 s, offline).

**What to do.** Ingest the real 5 m DTM / TIN terrain and document the re-condition
step. Gates every drainage number.

### 14. Storage is JSON files, not PostGIS

**The issue.** The documented target store is not started; the seam is `app/db.py`
and `scene_service.py` only.

**What to do.** Implement the PostGIS store behind the existing seam. File mode
already satisfies the same API contract, so this is a swap, not a rewrite.

### 15. Coverage stops short of two view modules, and two known limits stand

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

### 16. The temperature slider is a display preset, not physics

**The issue.** Above 35 °C the run applies `make_heatmap`'s documented heatwave
stretch — a render transform on the same field; the land-class heat index does not
know about air temperature, and `test_run` asserts it is bit-identical across
presets. A planner who drags temperature and sees no rainfall-side change is looking
at a model that is being honest, not at a broken slider — but the brief says
temperature "simulates heatwaves", so it is a scope risk if read literally.

**What to do.** A scope call, not a build: keep it as a clearly-labelled preset
(recommended — a real energy-balance term is out of scope and should not be
implied), or fund the physics it would take to make temperature move the numbers.

---

## Who does what

Three lanes. Each gap below is self-contained, so any lane can start without
waiting on another.

| Owner | Gaps | The lane |
|---|---|---|
| **Lia** | 1, 4, 5, 6, 7, 11, 15 | The interface and the interaction |
| **Chenxu** | 3, 8, 13 | The real 3D and geospatial data |
| **Edwin** | 2, 9, 10, 12, 14, 16 | Model internals, storage and configuration |

### Lia — the interface and the interaction

- **1** — the parameter card and the assumption questions.
- **4** — contour the intervention's true cell mask and give each kind a real
  massing profile, so a reservoir is a basin and not a box.
- **5** — generated suggestion cards and Pin/Unpin.
- **6** — the chat → ops bridge.
- **7** — the water-pour animation.
- **11** — surface wind as a third overlay and collapse the two geometry encodings.
- **15** — the two uncovered view modules, the credit-link accessible name, and the
  documented pan limit.

### Chenxu — the real 3D and geospatial data

- **3** — ingest and clean the real footprints (Overture and/or Open3Dhk's LandsD 3D
  Digital Map), reproject to lon/lat, publish the **GeoJSON contract** with a
  storeys-conditioned height, and replace the procedural massing generator so it
  extrudes actual building shapes from those footprints instead of tiling squares.
- **8** — the real Landsat LST scene over the site, which is the heat anchor, plus a
  provenance pass on `HEAT_INDEX`.
- **13** — the real 5 m DTM / TIN terrain, on the same contract as gap 3.

### Edwin — model internals, storage and configuration

- **2** — `projects.json`: fixed footprints and stated assumptions, so runs are
  reproducible.
- **9** — the provider-client test.
- **10** — the paid model id.
- **12** — the outlet water-level condition.
- **14** — the PostGIS store behind the existing seam.
- **16** — the temperature-slider scope call.

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
  energy balance, no air temperature, no humidity. Heat is not a temperature.
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
- **The heat field is a relative index, not a temperature.** The anchor band on disk
  (HKO Lau Fau Shan, July 2015–2024, `[26.4, 32.7] °C`) is what the 0–1 scale is
  *read against*; the field itself is still not °C and never becomes °C. Do not
  convert it, do not call the band a site measurement (the station is ~20 km away),
  and do not conflate `HKO_air` with `LST_skin`.
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
