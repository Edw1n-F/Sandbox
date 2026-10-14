# Status

The live state of the HSK / HT planning sandbox, **measured on 2026-10-14**.
Nothing here is a target. The architecture rationale is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

> **Read the stage table below before reading the flow.** "Works" here means
> *verified by running it on 14 Oct* — not *designed*. Anything authored against
> the retired 786 ha extent was purged on 14 Oct and is not described here.

---

## Where the project is

| Stage | Content | State |
|---|---|---|
| 0 | Scaffold and the first feature buildout | Discarded — built on a placeholder world |
| 1 | Real spatial foundation: terrain, land cover, buildings | **Done** |
| 2 | Deterministic run loop on today's state: sliders → runoff/storage → flood + heat overlays → stats → LLM panel | **Current, complete** |
| 3 | Scenario composition: fixed project footprints and toggles | **Not started** |
| 4 | Intervention authoring: AI suggestions, NL → card, parameter card, pin/unpin | **Not started** |
| 5 | Demo narrative, validation, pitch | Not started |

Stage 2 is closed. Stage 3 is the next build, and it is additive: the run loop
already consumes interventions correctly, so Stage 3 supplies footprints rather
than machinery.

### What the two unbuilt stages mean for a demo

The catalog in the UI (`ProjectCatalog.tsx`) is a **placeholder**: four hardcoded
projects whose phases are English sentences piped through `ai_plan` on every
click. It is not project data, placements are re-derived per click and therefore
non-deterministic, and a rate-limited provider makes the click a no-op. Do not
demo it as a working feature, and do not read it as evidence that Stage 3 exists.

The natural-language path is **massing-only**. Measured:

| Instruction | Parsed |
|---|---|
| "make the western blocks 40% taller" | `scale_all(1.4, west)` |
| "add a retention basin north of canal" | *nothing* |
| "remove the pond in the south" | *nothing* |
| "add permeable paving around the school" | *nothing* |

The blue-green intervention vocabulary the brief is actually about is not in the
parser, and the `remove` op does not exist at all. Intervention *application*
works — an in-bounds polygon moves the run (−302.1 m³ unstored, −0.03 ha wet) —
but nothing in the product generates one yet.

---

## Current state

### The user flow, today

1. **Open the app.** The Cesium viewer boots on the HSK / HT study area with
   terrain and the real building model already drawn — **396 real building
   footprints** extracted from Open3Dhk tiles fused with Overture, and the
   textured 3D Tiles meshes standing on the terrain, in an oblique default view.
   No Cesium Ion token required.
2. **Move around.** Left-drag pans, right-drag orbits, the wheel and pinch zoom,
   arrow keys orbit, and the buttons reframe (orbit / zoom / top-down / reset).
   The scene is dressed in **tactical** mode by default (dark, height ramp); survey
   mode exists in `viewModes.ts` but is no longer reachable from a labelled button.
3. **Set the scenario.** Rainfall intensity + duration, tide state (Low / Mean /
   High) and surface temperature write to the live scene (ADR-024: local only,
   never persisted), with HKO-named presets beside each.
4. **Run simulation.** `POST /api/scenes/{id}/run` applies the selected plans'
   interventions to the land-class grid, re-runs the project's own SCS-CN
   generation/retention arithmetic at the chosen rainfall, re-solves the level-pool
   fill, re-renders **both** overlays, and returns the baseline-vs-scenario ledger,
   a plain-language explanation, and any sanity warnings. The terrain
   conditioning and the sky-view/shade rasters are cached, since neither moves with
   a slider. A run at 450 mm reproduces the committed overlay exactly
   (180,566.3 m³ unstored, 16.89 ha wet, 6,725 wet cells — pinned by `test_run`).
   Measured end to end in a browser: **~11 s**, cold and warm alike.
5. **Read the result.** The stats panel shows its rows, the ledger shows the
   signed delta, and `sanityChecks` warns (never raises) when storage exceeds
   runoff, wet area exceeds the site, impervious share passes 100 %, cumulative
   coverage passes a cap, or CN leaves 30–100.
6. **Ask the chat.** The run's own numbers are handed to the model as a FACTS
   block; the answer returns as a card (title / sentence / cause / todo / stats /
   confidence / caveats / followups) rendered field by field, with every number
   checked against the run by code before it is shown. Three steps stream as they
   happen. With no key configured the same card is assembled by code and labelled
   `source: "template"`.
7. **Generate a plan (optional).** A sentence becomes a draft FeatureCollection via
   `ai_plan`, badged "LLM ready" / "no LLM key" / "LLM status unknown", and the
   massing ops (`scale_all`, `set_site`, `rotate`, `move`, `resize`, `reset`)
   apply scoped by a ring. This path is driven from the plan panel, not from the
   chat. Ops are idempotent — a second application cannot compound. **`place` is
   not among them**: it was removed from the parser, the tool schema and
   `_massing_feature` on 14 Oct, so no instruction can add a building.

Empty states are named rather than blank: a scene with no terrain, or a plan with
no interventions, renders a `SceneNotice` instead of an empty surface.

### What works

**Scene and model**

- `GET /api/scenes/{id}` boots terrain + explicit OpenStreetMap imagery + overlays,
  token-free (`baseLayer: false`, so no Cesium Ion token is ever required).
- **396 real building footprints** (`buildings_hsk.json`, `buildings_meta.json`),
  extruded and terrain-grounded from Open3Dhk tiles fused with Overture, plus the
  textured 3D Tiles meshes (`buildings3DUrl`). **This is measured geometry, not
  procedural massing** — the old 2,639-square `citymodel_hsk.json` placeholder and
  its generator are deleted, and `buildingsMeta.status` says
  `DERIVED_FROM_OPEN3DHK_AND_OVERTURE`. Heights are a 90th-percentile
  surface-minus-terrain, **not surveyed storeys**.
- Both overlays drape onto the tiles with the building footprints masked out of the
  *render* only, so water is not painted onto roofs. `wetgrid_hsk.npy` keeps every
  solved cell and the ledger still counts them; 3,142 of 6,725 wet cells were being
  drawn on roofs before the mask. A display transform must not delete a number.
- Per-run output paths (`data/rasters/run/<runId>/…`), so two concurrent planners
  cannot overwrite each other's overlay.

**The run**

- Live run loop as above, with a `stats.ratios` block published *by the run*
  (`unstoredOverRunoff` plus four siblings) so a model that quotes a derived
  percentage grounds it as a fact — the chat itself does no arithmetic.

**What works beyond the brief.** The brief asks for two outputs — a wet grid and a
heatmap. Three evaluation axes exist and are tested, of which only drainage and
heat are in the product surface:

- **Heat — a °C surface-temperature field, anchored to observed LST.** Every
  footprint is swept along the sun vector (real shadow casting), a 16-bin, 300 m
  horizon search gives sky-view factor, and `cell_fad` gives wind shelter. These
  geometry terms express each cell's *deviation from the site mean* around an
  **observed Landsat LST surface-temperature scene** (the 2022-09-03 capture, site
  mean 41.26 °C, resampled 30 m → 5 m), producing a real °C field
  (`temperature_field.py`) shown as an anomaly map — blue cooler / neutral average
  / red hotter — so a well-designed site reads near-neutral and only genuine hot
  spots go red. The temperature slider sets the scenario mean °C; air temperature
  is an *estimate* (surface − 12 °C), never a measurement.
- **Wind — out of scope as a deliverable, kept as a heat term.** `cell_fad` spreads
  each footprint's oriented frontal area over its cells, and the area-weighted mean
  of that field equals the site-level FAD — the identity that catches a wrong
  denominator. Least-cost corridors on a `1 + cellFAD` surface via Dijkstra, and
  `fad_ratio()` compares two schemes' geometry. A ratio is a **comparison, not a
  velocity ratio**; the corridors are geometry, and `cell_fad` is 180°-periodic so
  they cannot separate NE from SW. **Wind is not presented as a product axis** — it
  feeds the heat field's shelter term and that is all.
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
  `massing_fit.py` + `GET /buildings/{id}/fit` ranks candidate changes by an
  objective enum while the ledger does the arithmetic. **The endpoint works and is
  tested; the LLM tool is not wired to it.** `FIT_TOOL`
  (`assess_building_fit`) is *offered* to the model, but the agent's result parser
  handles only `CHOOSE_TOOL` and `MASSING_TOOL`, so a fit call is never read back.
  `score_candidates` has exactly one caller — the router. Nothing in the frontend
  calls the endpoint either. See open item 12.

---

## Checks and disclaimers

### Test suites

Counts measured by running every suite on **2026-10-14**. Lint and typecheck are
clean. **Every backend suite is green.** `.github/workflows/ci.yml` runs every
suite below except the three browser suites, plus the contract drift check and
`npm run build`, on every PR and every push to `main`. It has not yet run on
GitHub; every command in it was run locally, including from a fresh clone. The last three red suites were
pinned to state the real-data switches replaced, and were closed on 14 Oct: the
`place` checks were rewritten to pin that the planner no longer offers it, and the
hydrology and ledger pins were recounted against the 43.5 ha window rather than
copied from the code's output.

| Suite | Result | Command |
|---|---|---|
| backend `test_design_metrics` | 91/91 | `python tests/test_design_metrics.py` |
| backend `test_scenario` | 65/65 | `python tests/test_scenario.py` |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_ventilation` | 45/45 | `python tests/test_ventilation.py` |
| backend `test_ledger` | 42/42 | `python tests/test_ledger.py` |
| backend `test_ai_plan` | 40/40 | `python tests/test_ai_plan.py` — includes the 429 handling |
| backend `test_hydrology` | 32/32 | `python tests/test_hydrology.py` |
| backend `test_run` | 34/34 | `python tests/test_run.py` — the live run loop |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_hourly_storm` | 27/27 | `python tests/test_hourly_storm.py` |
| backend `test_contract` | 23/23 | `python tests/test_contract.py` |
| backend `test_constants` | 17/17 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_temperature_field` | 13/13 | `python tests/test_temperature_field.py` |
| backend `test_agent` | 28/28 | `python tests/test_agent.py` — grounding and the confidence ladder, no key |
| backend `test_llm_provider` | 30/30 | `python tests/test_llm_provider.py` — the chat and the run card through a scripted `FakeProvider`, no key |
| backend `test_openapi_contract` | 5/5 | `python tests/test_openapi_contract.py` — fails if `contracts/openapi.json` is stale |
| backend `test_overlay_masking` | 3/3 | `python -m pytest tests/test_overlay_masking.py` |
| frontend unit (`vitest`) | 71/71 | `npm test` — includes `streamChat` against real streams, the provenance receipt, the trackpad wheel maths and the navigation cheat-sheet |
| frontend rendering (`verify-3d.mjs`) | 17/17 | `npm run verify:3d` — needs live servers |
| frontend robustness (`verify-ux.mjs`) | 21/21 | `npm run verify:ux` — needs live servers |
| frontend chat panel (`verify-chat.mjs`) | 17/17 | `npm run verify:chat` — needs live servers; the chat stream is mocked, so it needs no language model |
| frontend navigation (`verify-nav.mjs`) | 16/16 | `npm run verify:nav` — needs live servers; the help opens once and stays dismissed, the tilt buttons and keys move the real camera, the compass turns and re-centres |

`test_overlay_masking` is pytest-style with no `__main__`, so run it with
`python -m pytest`; the `for f in tests/test_*.py` loop prints nothing for it.
`test_temperature_field` is the opposite case — it has no pytest-collectable
functions, so `pytest tests/` reports "no tests ran" for it and the standalone
script is the only way to run it. Two suites, two runners; run both, or one of
them silently reports nothing.

On Windows set `PYTHONUTF8=1`, or three suites crash on a cp950 decode error
before running a check.

The two `verify-*` suites are **not** part of `npm test`: they need the dev server
on `:5173` and the backend on `:8001`, and folding them in would let a missing
server read as a pass. Both were rewritten on 14 Oct (the previous versions were
bound to one machine's Playwright path and gated on the deleted procedural
model's entity count) and both now exit `2` rather than `0` when a server or
Playwright is missing.

The chat's model call is the one thing no suite exercises — open work item 6.

`scipy>=1.16.1` is required by `test_ventilation` and `test_contract` (the earlier `<1.14` ceiling had no Python 3.14 wheel). Two timing
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


---

## Open work

Each item is self-contained. Ordered by what blocks Stage 3 first.

**Blocks Stage 3**

1. **The project catalog is a placeholder.** `ProjectCatalog.tsx` hardcodes four
   projects whose phases are English sentences piped through `ai_plan` on click.
   No fixed footprint, no land-cover conversion, no stated assumptions, and
   placement is re-derived per click — so runs are not reproducible, which a
   before/after demo requires. Close it with `backend/data/projects.json`:
   footprint rings, the conversion each implies, source/date/confidence per field.

**Blocks a live link**

2. **An intervention is an axis-aligned slab, not a shaped object.** `extent_ring`
   and `_fraction_ring` emit five-point axis-aligned rings, and `STYLE_FOR_KIND`
   gives each kind one `extrusionHeight` — so a reservoir is a box, not a basin.
   `_components` already computes the true cell mask; contour it, then give each
   kind a massing profile.
3. **No suggestion engine and no Pin/Unpin.** The chips are hardcoded strings
   (`ChatPanel.tsx`); a grep for `pin` returns zero hits. Add
   `suggest_interventions(run_result, grounding)` over the existing
   `reservoir_sites` / `green_sites` / `paving_sites` pools, and a `pinned: bool`
   on `InterventionRow`.
4. **The NL loop has no owner end to end.** NL → geometry exists but is reachable
   only from the plan panel, and the vocabulary is massing-only with no `remove`,
   so "remove this" cannot be expressed. Expose the ops as chat-callable, add
   `remove`, and make an addition *shaped* rather than extruded.
5. **No parameter card.** The brief puts a validated parameter object between
   "read scenario" and "calculate"; nothing of it exists. The numbers are
   scattered across the ledger and the stats dict, with no per-assumption source,
   date or confidence. Nothing estimates a user intervention's *own* numbers
   (usable depth, permeability, area reworked), so every intervention falls back
   to a fixed value in `INTERVENTION_EFFECTS`. The sink is wired and empty:
   `apply_interventions` already reads `params.storageDepthM`, validates
   0.05–10 m, and emits the per-cell override the run scores. Add
   `parameter_card.py` plus an endpoint, cached on (plan ids + intervention hash);
   have `ai_plan` estimate the params and return `questions[]` for what it cannot
   ground. **The model supplies an assumption, never a result.**

**Configuration and environment**

6. **A live provider is still verified only by hand.** `test_llm_provider.py`
   drives the real chat through a scripted `FakeProvider`, with no key and no
   network. It covers every retry and fallback path, plus the request shapes both
   real providers send. What it cannot cover is a real provider's *response*.
   That is still checked by hand, and the free tier has been returning 429s.
7. **A rate-limited provider costs a round trip.** The free tier is throttled and
   slow. A 429 is caught and reported, and `LLM_FALLBACK_MODELS` now walks three
   additionally-verified free ids (`google/gemma-4-26b-a4b-it:free`,
   `cohere/north-mini-code:free`,
   `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` — each called and
   confirmed to return prose on 14 Oct). A paid model id — one `.env` line —
   removes the problem instead of routing around it.
8. **Storage is JSON files, not PostGIS.** The seam is `app/db.py` and
   `scene_service.py` only, so this is a swap rather than a rewrite.
9. **Two view modules have no unit coverage** — `view/onlineProps.ts` and
   `view/interventionLayer.ts`. Separately: Cesium's credit logo link has no
   accessible name (third-party markup, excluded rather than stripped, because
   removing it would break Cesium's attribution terms).

**Not started, deliberately**

10. **No water-pour animation.** The wet grid is a static PNG swap.
11. **No `remove`/undo anywhere.** A planner can add but not take away.

**Wired to nothing**

12. **Four endpoints serve verified data that no client displays.**
    `/ledger`, `/sea-level`, `/scenarios`, `/scenario/evaluate`. They are not
    dead: the rung declaration claims the first three as delivered features
    (`docs/RUNG_AND_BUSINESS.md`), and `/scenario/evaluate` is the retained back
    door for the dormant `place` op (Decision 14). They are **tested** — 85 checks
    across `test_ledger`, `test_sea_level` and `test_published_scenarios` — but
    only reachable by hand. `/ledger` is nearly redundant with `POST /run`, which
    carries the same figures from the same arithmetic; the other three have no
    run-path equivalent. The panel that displayed them (`ImpactPanel.tsx`) was
    deleted as an orphan on 13 Oct. Either re-surface them in Stage 3, or state in
    the rung document that they are API-only.
13. **`FIT_TOOL` is offered to the model but its result is never read.**
    `assess_building_fit` is in the tool list and `route_tools` forces it for
    "what should I do" phrasing, but `ai_plan`'s result parser handles only
    `CHOOSE_TOOL` and `MASSING_TOOL`, so the call is discarded. Wire it to the
    endpoint or drop the tool from the list.
14. **A duplicate test name makes one check unreachable.** → fixed, see
    "Resolved" below.

**A duplicate test name hid a check for an unknown length of time.** On 14 Oct
`test_constants.py` defined `test_no_class_is_counted_as_both_generation_and_retention`
twice, with different bodies. Python keeps the later definition, so the earlier
one was collected as a duplicate and silently discarded — it had never run. The
suite counted 16 because that is what the runner collected, so the count was
honest and the gap was invisible. Both now have distinct names and both pass:
16 → 17. Worth remembering as the general shape: **a green suite count is
evidence about the checks that ran, not about the checks that were written.**



These were open items on 13 Oct and are closed. They are kept in one short block
rather than deleted, because each was a decision someone will otherwise re-litigate.

**The `place` op is dormant, not deleted.** It is gone from the parser, the tool
schema and `_massing_feature`, so no instruction can reach it; a model that emits
one is refused. `scenario.apply_massing` still *scores* a `place` op handed to it
directly through `POST /scenario/evaluate`, and `placement_footprints` still
exists. That is a deliberate half-state: the front door is closed, the capability
waits for Stage 4 to decide whether the massing vocabulary survives the NL
rewrite at all.

**The seeded and published artefacts are purged.** The derived-intervention seed,
its plan, and the loose residue around them were authored against the retired
786 ha extent and fell outside the current grid, scoring exactly zero — so the
store now holds only the seeded scene and the baseline plan.

**The terrain is the real 5 m DTM.** `dem_hsk.npy` is the Open3Dhk / LandsD tile
surface (117 × 148, 3.4–17.3 m). It is the tile-mesh *surface*, not a surveyed
bare-earth DTM release, and a run re-solves the water level on the conditioned
surface without re-conditioning it — so a DEM change needs `make_wetgrid.py`
re-run.

**The base model is real footprints, not procedural blocks.** See "What works".

**The heat weights are the residual, not the field.** `HEAT_INDEX`'s land-class
weights stay hand-authored apart from `green 0.64`, which is calibrated from the
LST scene. They perturb an observed base; they do not constitute it.

**The temperature slider sets the scenario mean and says so.** The slider is no
longer a silent display-stretch: it sets the mean surface temperature in °C,
re-anchors the whole field so its mean matches, and carries HKO-named presets
(*Hot night* ≥ 28 °C air, *Very Hot Weather Warning* ≥ 33 °C, *Extreme heat*
≥ 35 °C) the way the rainfall buttons name the rainstorm signals. The spatial
pattern still decides which cells are hotter or cooler, so a well-designed site
reads near-neutral rather than "all red". It changes no rainfall or runoff figure
— temperature cannot drive SCS-CN without an evapotranspiration term the project
does not have.

**The outlet water-level condition is a screening factor, not a hidden limit.**
`fill_and_spill` takes a `tailwater_m` boundary condition, and a Low / Mean / High
tide toggle (HKO Quarry Bay, 0 / 1.4 / 2.2 mCD) threads through the run, the stats,
the sidecar and the frontend. **The honest finding: this site is landward** — the
grid minimum is 3.41 m, above the 2.2 m high-tide surface — so tidal backwater has
~zero effect here and flood area is unchanged across the three states. The feature
is wired and tested and effectively inert in this window; say so rather than
implying a tidal story.

**The heat field is anchored to observed LST.** The base is the Landsat scene of
2022-09-03, resampled 30 m → 5 m, so the absolute level is measured and the
geometry terms only move a cell away from the site mean.

---

## Constraints that must survive future work

These are the rules the project is built on. Every one of them was a decision, and
each was made because the honest alternative was to quietly publish something
false.

- **Generative 3D belongs on proposals, not on the existing district.** An
  AI-generated mesh is fiction, so it must not be presented as the current state of
  HSK/HT — the existing buildings come from measured data. Interventions are
  labelled `draft_pending_human_review`, so a generated glTF is honest there and
  dishonest as "what is there now".
- **The 3D city model is measured geometry, not generated massing.** The 396
  footprints are extracted from Open3Dhk tiles fused with Overture and keep their
  provenance disclosure; no generated mesh is ever presented as what is there now.
  Heights are a 90th-percentile surface-minus-terrain, never "surveyed storeys".
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
  simulation inside a Q&A would turn a fast reply into a full simulation run *and* let the
  answer drift from the numbers on screen. No new arithmetic in `app/agent/`.
- **Grounding is code, never the model.** A card's numbers are checked against the
  facts by `grounding.check_card`, and a card that fails is either revised once or
  shown with the problems named. The model is never asked whether its own numbers
  are right.
- **`source: "template"` is not a degraded mode to hide.** When no model is
  configured the card is assembled by code from the run's own figures and the UI
  says so. Every number in it is one the run actually produced, which is more than
  a hallucinated model answer can claim.
