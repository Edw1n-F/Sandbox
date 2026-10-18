# Status

The live state of the HSK / HT planning sandbox, **measured on 2026-10-16**.
Nothing here is a target. The architecture rationale is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

> **Read the stage table below before reading the flow.** "Works" here means
> *verified by running it on 16 Oct* — not *designed*. Anything authored against
> the retired 786 ha extent was purged on 14 Oct and is not described here.
> The remaining-work list, with the current re-scope, is
> [`docs/TODO_remaining.md`](docs/TODO_remaining.md).

---

## Where the project is

| Stage | Content | State |
|---|---|---|
| 0 | Scaffold and the first feature buildout | Discarded — built on a placeholder world |
| 1 | Real spatial foundation: terrain, land cover, buildings | **Done** |
| 2 | Deterministic run loop on today's state: sliders → runoff/storage → flood + heat overlays → stats → LLM panel | **Done** |
| 3 | Scenario composition: planned project footprints and toggles | **Done** — two planned projects (HSK MTR station, HSK Park), scored together; the Projects panel lists only declared projects |
| 4 | Intervention authoring: suggestions, NL → proposal gate, input card, add / modify / remove, parameter card | **Built** — see "The sandbox loop" and "The parameter card" below; no committed browser suite covers the loop yet |
| 5 | Demo narrative, validation, pitch | Not started |

Stage 2 is closed. Stage 3 is partially open: the Projects panel lands the first
planned-footprint project and the machinery it rides on. **A planned project is a
government project to be built** — it is data (`backend/data/projects/<id>.json`)
declaring its effects, seeded into a plan by `scripts/seed_projects.py`; toggling
it puts that plan in `selectedPlanIds`, so the run scores it and the
cumulative-coverage guard counts it with no special case anywhere. It is the
**application scenario** the sandbox exists to help plan, and it is *not* a user
intervention — the intervention loop (Stage 4) is how a planner proposes changes
*to* such a project. A second project is a data edit, not a code change: the
scene record lists every project that has a model (`terrain.projects`, built from
the same files), the viewer keeps one model per project, and the Projects panel
builds a row per seeded plan. HSK Park (`park.json`, 16 Oct) proved it: the second
project landed as data with no code change, and the two score distinctly alone and
compose when toggled together.

A project's run effect has two halves that behave differently. Its storage moves
the water ledger like any intervention. Its massing moves the heat geometry, and
that effect is **local**: the temperature field is re-anchored so its mean equals
the scenario mean by construction, so the site-mean rows can never show it. The
run therefore reports it separately (`stats.temperature.local`: cells changed,
signed peak) and the panel shows it as `Δ surface temp (local)`.

The building **style** switch (3D / 2.5D) is deliberately separate from the
project toggles. It changes which layer the city is drawn from and nothing else:
selecting a project no longer swaps the depiction, and switching to 2.5D no
longer spawns the station. Those were one flag, and the fusion was the bug.

### The project catalog

The derived project catalog was **retired on 16 Oct 2026**: its four placeholder
parcels are gone, along with `data/projects.json`,
`scripts/make_project_catalog.py`, `app/services/project_catalog.py` and
`GET /scenes/{id}/project-catalog`. The Projects panel now lists only the
**declared** projects seeded from `backend/data/projects/<id>.json` — the MTR
station and HSK Park. A derived parcel beside a declared project read as a plan
that actually exists, which is why it went. The suggester still derives candidate
sites (`app/services/suggest.py`, `GET /scenes/{id}/suggestions`); that is a
different step and it stays.

### The natural-language path

**The natural-language → intervention path works, end to end.** Measured on 16 Oct
against the live server, no model key configured:

| Step | Endpoint | Result |
|---|---|---|
| A planner types "add a water reservoir in the north" | `POST /api/scenes/{id}/chat` | `answer.intent = "intervention"`, with the hint carried back — the deterministic classifier raises proposal-shaped turns without a model |
| The hint opens the confirm gate | `POST /api/scenes/{id}/ai-plan` (`apply=false`) | one **served** proposal: 1,876 cells, `size`, `constraints`, `parameters`, `status`, `warning` — geometry re-derived from the rasters, never taken from the model |
| A candidate the constraints refuse | same | returned in `rejected[]` with a closed `reasonCode` and a written reason, instead of being silently dropped |

The proposals are screened by code before they are shown: `proposal_constraints.py`
checks building overlap (zero tolerance), overlap with anything already staged, and
the coverage caps, and re-derives every number from the rasters. A paving site at
39 % of the grid is refused `OVER_SINGLE_CAP` rather than proposed.

### The parameter card

**The parameter card works** (`GET /api/scenes/{id}/parameter-card`). It is the
brief's §2 table, assembled entirely from the dicts `ledger_for_scenario` computed in
the same request — it never touches `grids.land_class`, so a card and a run cannot
disagree about a figure. Three rows deliberately carry **no value** and say why:
soil group (no soil survey exists — `CURVE_NUMBER_HSG_D` is a table *named* HSG D),
heat preset (`SCENARIO_PRESETS` has no callers; the run takes a raw temperature), and
tree cover (the land-cover raster has no tree class). Storage is printed as **two**
numbers side by side — gross `area × depth` (the brief's own formula) and net
`storageCapacityM3` change (the run's own figure) — because they are different
quantities and net is smaller whenever the ground gave up storage.

### The sandbox loop (Stage 4)

After a run, there are two ways to add an intervention, and both end in the same
place:

1. **A suggestion under the run's card.** Three, one per axis (drainage, heat,
   runoff): the best free site for *this* run, read from the land cover, wet grid
   and building footprints by `services/suggest.py`. They used to be three fixed
   sentences, the same on every run; `run_card.py` now emits none, and the chat
   asks `POST /sandbox/suggestions` once the run returns. A site already taken by
   a placed intervention or a toggled project is skipped for the next one.
2. **A sentence in the chat**, through the existing `ai-plan` proposer and its
   confirm gate.

Either way the user gets an **input card**: the values the run reads for that
kind, each typed by the user or left to the AI's estimate. Only fields the ledger
reads are offered — storage depth for a reservoir, tank or floodable ground, and
nothing for green space or permeable paving (the ledger ignores a supplied curve
number, so offering one would be a control that does nothing). Which it was is
stored per field in `params.inputSources` (`user` / `ai_estimate`) and shown in
the Interventions panel. A value the run would refuse is caught at the card.

Confirming **pins** the feature into the scene's staged plan — the one place
interventions live (`services/pins.py`). The **Interventions** panel lists it,
the 3D view draws it, and Run scores it with the toggled projects. Deleting a row
asks first and unpins it.

**Removal** is resolved against what is on the sandbox, never sent to the
proposer (which used to read "remove the reservoir" as a request to *add* one): a
placed intervention is unpinned, a planned project is toggled off and its plan
kept. Existing buildings and today's land cover are not removable. Several
matches ("remove the park" can be HSK Park or a green space the user placed) are
listed for the user to pick.

**Modification** changes the values of something already placed, never its
footprint: the ✎ on an Interventions row, or a sentence like "make the reservoir
5 m deep" (the named depth opens as the user's own value). A refused value leaves
the sandbox as it was. Planned projects are not modifiable; their values are their
project file's.

What it does not do: shape is still the candidate's contour, not something the
user draws, and green space and permeable paving have nothing to set.

**Three honesty holes were closed on 17 Oct**, all in the sandbox's own path:

1. **A hand-set depth was recorded as a measurement.** `POST /ai-plan/apply` with
   a client-set `params.storageDepthM` persisted it and labelled it
   `inputSources: ai_estimate` -- a served number whose provenance said the AI
   chose a value it never saw. A depth arriving with a request is now the
   *sender's* own, labelled `user`, and one outside the ledger's range is a 422
   rather than a silent fall back to the class default.
2. **`/ai-plan/apply` persisted whatever it was handed.** The client owns the
   proposals between the gate and the apply, so a geometry could change in
   between; Phase 2's M6. The batch is now re-validated before anything is
   pinned, and a proposal moved onto surveyed buildings comes back
   `BUILDING_OVERLAP` in `rejected[]` with nothing persisted. A feature already
   on the sandbox is not a conflict with itself, so re-confirming stays the
   no-op the pin fingerprint already made it.
3. **An empty region blamed a rate limit.** "add a water reservoir in the south"
   has no candidate (every reservoir is in the north) and answered with the
   generic "no candidate survived the constraints" plus a 429 note that had
   nothing to do with it. It now names the region, the kind, and where that kind
   does have candidates. Deliberately not a `rejected[]` entry: that array is the
   constraint engine's, and this was never a constraint outcome.

One reading to know before a demo: on the large reservoir site, **unstored runoff
does not move with depth** (7,777.7 m³ at 0.05 m and at 5 m, 50 mm storm) while
storage capacity does (10,561 → 317,593 m³). The unstored volume is water that
never reaches the site, so a deeper reservoir there adds capacity nobody fills.
That is the model being honest, not a bug, but it reads like one.

Verified on 17 Oct by `tests/test_sandbox.py` (32 checks) and
`sandboxStore.test.ts`, and once by hand in a real browser against a temp copy of
the store: suggestions, preview, input card, add, edit, NL removal and
confirm-to-delete, 17/17. That walk is not a committed suite; none of the four
`verify-*.mjs` drives the panel yet.

**Still open in Stage 4:** an intervention shape the user draws rather than a
candidate's contour; and the parameter card is a read-only table beside the run,
not yet shown in the Interventions panel.

`scripts/verify-sandbox.mjs` is the committed browser suite for the loop, added
17 Oct (`npm run verify:sandbox`, 14 checks). It is the only suite that stubs
**just** `/run`: the confirm gate, the input card, the preview endpoint, the
apply, the edit and the removal all go through the real backend against the real
rasters, so the numbers the gate shows are the numbers the API produces.

`questions[]` and editable assumptions landed on 17 Oct (Phase 2 R8-R20). A
proposal now carries the assumptions it rests on, generated by code from the
parameters the run published -- four per proposal (`location`, `footprint`,
`surfaceReplaced`, `usableStorageDepth`) plus two for the batch as a whole
(`site.drainageOverflow`, `site.treeCover`), so a question can never exist for a
number nothing reads. Each carries a stable id derived from its parameter alone,
so an answer matches its question by string equality with no server state.

**An answer is honoured, not merely suppressed.** An answered
`usableStorageDepth` that parses to a number in range replaces the class default
in `params.storageDepthM` -- the one key `apply_interventions` reads -- and the
parameter block becomes `USER_ASSUMPTION` with `defaultValue` naming what it
replaced. The parse is the **server's**, never the client's: "about 80 cm"
becomes 0.80 m, and an answer with no number in it is reported as unparsed with
the class default left standing, rather than guessed at.

**Prior work is filtered at selection, not refused at the gate** (Phase 4
T04a/b, 17 Oct). `/ai-plan` gains `planIds` and the proposer's pools are narrowed
by the claimed cell set before any candidate is offered, so a site already under
the staged plan or a toggled project is **skipped** and the next free candidate
is proposed. Before this, pinning one reservoir made the whole kind come back
empty while two free basins existed. The overlap guard is **not** weakened — it
stays exactly as it is, as the backstop for a coordinate request or a race with
a concurrent pin, and `test_sandbox` asserts it still refuses. Skip at
selection, refuse at the gate; both, for the same reason the region work needed
both. The pool filter subtracts the *gate's* building mask, not the pool's own:
they differ by a couple of cells in how a footprint rasterises, and the
difference is enough to keep a wholly-claimed site in the pool.

**`POST /sandbox/preview` measures without running** (Phase 4 T01/T02). The
dashed layer already showed a proposal's *shape*; this shows its *consequence*.
A dedicated route rather than a `dryRun` flag on `/run`, because a run writes
overlays, prunes the run history to 20 entries, mints a `runId` and calls a
model for an explanation card — none of which a preview may do. The response
carries **no `runId`, no `overlayUrls` and no `explanationCard` key at all**,
absent by construction rather than empty.

The three deltas are kept apart, which is the load-bearing part: the run's own
`runDelta` (scenario − today's land cover), `now` (the current sandbox state)
and `previewDelta` (preview − now, the **marginal** effect). Once prior work
exists they differ by exactly that prior work, so `previewDelta` deliberately
does not reuse the run's key, both are present, and each is labelled by what it
is the difference of. The preview runs candidates through `sandbox.apply_inputs`
first — the same function `/ai-plan/apply` uses — because the input card changes
the ledger, and previewing the AI's estimate while the planner has typed a depth
would show a number that will not happen. `status` / `warning` / `scope` are
rendered verbatim from the payload, not re-worded in the UI, so no other client
can mistake these numbers for a run's.

`POST /ai-plan/revalidate` is the gate's third call and writes nothing: it
re-runs the same constraint engine over the proposals as they now stand, so a
planner sees what their edit did -- and whether it made the placement illegal --
before pressing Apply. It returns `geometryAffectingParameters: []` and
`valuesAffectingOnly: ["parameters.storageDepthM"]` as explicit lists rather
than by omission, and the tests assert the geometry is **byte-identical** across
an edit. A depth outside the ledger's range is a 422 on both endpoints; the
closed `reasonCode` set stays exactly its nine members, because a client typo is
not a placement predicate.

### What a "plan project" is — and what it is not

A **plan project is a government-planned project to be built.** Two are modelled:
the HSK MTR station and HSK Park (`backend/data/projects/<id>.json`: a real footprint,
a real GLB, project-supplied effects). It is the **application scenario** — the thing the
sandbox exists to help plan — and it is **not** a user intervention.

The four-project catalog that used to sit in the HUD (`detention-reservoir`,
`underground-tank`, `green-retrofit`, `blue-green-park`) was raster-derived candidate
parcels, not planned projects. It was removed on 16 Oct. See `docs/TODO_remaining.md` for
the re-scope.

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
7. **Describe an intervention in the chat.** A sentence the classifier reads as
   proposal-shaped ("add a water reservoir in the north") is routed to the
   **confirm gate** rather than answered as a result card — with no model key
   configured, by a deterministic classifier that shares its vocabulary with the
   client. The gate shows each proposal's served geometry, size, the constraints
   it was screened against, the parameters the run will read, and a `status` +
   `warning`. Refused candidates come back in `rejected[]` with a `reasonCode`.
   Accepting persists a draft plan through the normal plan path; nothing is
   written until the planner confirms. The massing ops (`scale_all`, `set_site`,
   `rotate`, `move`, `resize`, `reset`) still apply scoped by a ring, and ops are
   idempotent. **`place` is not among them**: it was removed from the parser, the
   tool schema and `_massing_feature` on 14 Oct, so no instruction can add a
   building.
8. **Read the parameter card.** `GET /api/scenes/{id}/parameter-card` gives the
   brief's §2 table for whatever plan is selected, with the three unknowns named
   as unknowns and storage shown as both gross and net.

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

Counts measured by running every suite on **2026-10-16** (the browser suites were
last run on 14 Oct and are not re-measured here). Lint and typecheck are clean.
**Every backend suite is green.** `.github/workflows/ci.yml` runs every
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
| backend `test_run` | 49/49 | `python tests/test_run.py` — the live run loop |
| backend `test_ventilation` | 45/45 | `python tests/test_ventilation.py` |
| backend `test_ledger` | 42/42 | `python tests/test_ledger.py` |
| backend `test_ai_plan` | 40/40 | `python tests/test_ai_plan.py` — includes the 429 handling; ~5 min |
| backend `test_contract` | 36/36 | `python tests/test_contract.py` — now on a temp copy of the store |
| backend `test_building_fit` | 32/32 | `python tests/test_building_fit.py` |
| backend `test_hydrology` | 32/32 | `python tests/test_hydrology.py` |
| backend `test_llm_provider` | 31/31 | `python tests/test_llm_provider.py` — the chat and the run card through a scripted `FakeProvider`, no key |
| backend `test_agent` | 31/31 | `python tests/test_agent.py` — grounding and the confidence ladder, no key |
| backend `test_published_scenarios` | 28/28 | `python tests/test_published_scenarios.py` |
| backend `test_hourly_storm` | 27/27 | `python tests/test_hourly_storm.py` |
| backend `test_sandbox` | 52/52 | `python tests/test_sandbox.py` — Stage 4: suggestions, input card, add, modify, remove; the three honesty checks (a hand-set depth is never recorded as a measurement; `/ai-plan/apply` re-validates before it pins; an empty region names the region); and Phase 4's prior-work skip (the proposer moves to free ground instead of dead-ending) and preview (self-labelled, no run identity, three deltas kept apart, byte-identical on repeat, writes nothing) |
| backend `test_pins` | 18/18 | `python tests/test_pins.py` |
| backend `test_intervention_geometry` | 18/18 | `python tests/test_intervention_geometry.py` |
| backend `test_proposal_contract` | 38/38 | `python tests/test_proposal_contract.py` — the proposal wire, constraints, parameter card and routing; clears what it stages, since apply now pins to the sandbox. Six Phase 2 checks: every silent assumption becomes a question; an answered question is honoured, not re-asked; a hand-edited depth changes its provenance label; an edit leaves geometry byte-identical; apply revalidates and refuses an illegal edited proposal; an out-of-range depth is a 422. |
| backend `test_constants` | 17/17 | `python tests/test_constants.py` |
| backend `test_sea_level` | 15/15 | `python tests/test_sea_level.py` |
| backend `test_intent_routing` | 14/14 | `python tests/test_intent_routing.py` |
| backend `test_grounding_pools` | 13/13 | `python tests/test_grounding_pools.py` |
| backend `test_temperature_field` | 13/13 | `python tests/test_temperature_field.py` |
| backend `test_suggest` | 11/11 | `python tests/test_suggest.py` |
| backend `test_openapi_contract` | 5/5 | `python tests/test_openapi_contract.py` — fails if `contracts/openapi.json` is stale |
| backend `test_overlay_masking` | 3/3 | `python -m pytest tests/test_overlay_masking.py` |
| frontend unit (`vitest`) | 98/98 | `npm test` — includes `streamChat` against real streams, the provenance receipt, the wheel maths, the navigation cheat-sheet, and the sandbox's run composition and input-card answers |
| frontend rendering (`verify-3d.mjs`) | 27/27 | `npm run verify:3d` — needs live servers; `/run` is stubbed from `scripts/fixtures/run-result.json`, so it needs no language model |
| frontend robustness (`verify-ux.mjs`) | 21/21 | `npm run verify:ux` — needs live servers |
| frontend chat panel (`verify-chat.mjs`) | 17/17 | `npm run verify:chat` — needs live servers; both the chat stream and `/run` are stubbed, so it needs no language model |
| frontend sandbox loop (`verify-sandbox.mjs`) | 15/15 | `npm run verify:sandbox` — needs live servers; **only `/run` is stubbed**, so the gate, the input card, the preview, the apply, the edit and the removal all hit the real backend and the real rasters. Drives: the gate opening on a natural-language instruction; the details card asking what the run reads, and no unanswerable read-only questions; the estimate shown with its basis, and the field revealed by "enter my own"; Preview labelling itself not-a-run and keeping the marginal delta off the run's key; the intervention reaching the Interventions panel; an edited value accepted with the row still there; a removal sentence resolving to a confirmation rather than a silent delete; and no uncaught page errors through the whole loop |
| frontend navigation (`verify-nav.mjs`) | 29/29 | `npm run verify:nav` — needs live servers; the help opens once and stays dismissed, the tilt buttons and keys move the real camera, the compass turns and re-centres, tilting past the pitch limit leaves the heading alone, rotating at the limit leaves the pitch alone, zooming leaves both alone, two quick zoom presses add up, zooming after a pan keeps the point under the view centre where it was, a vertical right-drag tilts without rotating and a horizontal one rotates without tilting, the mouse wheel zooms while a trackpad-drag stream tilts |

`test_overlay_masking` is pytest-style with no `__main__`, so run it with
`python -m pytest`; the `for f in tests/test_*.py` loop prints nothing for it.
`test_temperature_field` is the opposite case — it has no pytest-collectable
functions, so `pytest tests/` reports "no tests ran" for it and the standalone
script is the only way to run it. Two suites, two runners; run both, or one of
them silently reports nothing.

**The runner is chosen by the `__main__` guard, so a script-style suite must have
one.** CI runs `python <file>` when the guard is present and `pytest <file>` when
it is not — and pytest exits **5** ("no tests collected") for a script with no
`__main__` and no `def test_*`, which reads as an opaque red backend job.
`test_intervention_geometry.py` shipped that way on 16 Oct: 18/18 under `python`,
0 checks and exit 5 under pytest, so CI failed a suite whose every assertion
passed. It now has a `main()` and a guard, and CI's loop says so by name if
another suite is ever written this way.

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

Each item is self-contained. The full remaining list, with the re-scope, is in
`docs/TODO_remaining.md` — read that first; this section is the short version.

**Closed since this file was last measured**

1. ~~**The project catalog is a placeholder.**~~ **Retired on main, 16 Oct 2026.**
   The four projects were raster-derived candidate parcels, not planned projects.
   `ProjectCatalog.tsx`, `catalogApi.ts`, `project_catalog.py`,
   `data/projects.json`, `scripts/make_project_catalog.py` and
   `tests/test_project_catalog.py` are all gone; the planned-project machinery
   (`data/projects/<id>.json`, `terrain.projects`, the MTR station and HSK Park
   plans) is untouched and stays.
2. ~~**No suggestion engine and no Pin/Unpin.**~~ Both exist: `suggest.py` +
   `GET /api/scenes/{id}/suggestions` (ranked per-axis candidates with the
   per-cell arithmetic shown) and `pins.py` + three pin routes. What remains is
   placement — the panel moves into the chat beneath the result card.
3. ~~**The NL loop has no owner end to end.**~~ The intervention path now runs
   from the chat through the confirm gate. Still open: the `remove` op, and making
   an addition *shaped* rather than extruded.
4. ~~**No parameter card.**~~ `parameter_card.py` + `GET /api/scenes/{id}/parameter-card`
   exist, assembled from the ledger's own dicts. Still open: `questions[]` and
   editable assumptions before confirm.
5. **Two declared projects exist; the target is four.** `backend/data/projects/`
   holds `mtr-station.json` and `park.json`, each with a real GLB and a footprint,
   and both score distinctly in a run and compose when toggled together. What is
   missing is projects 3 and 4 — added as data, no code change. One caveat: a
   project whose GLB or footprint is not on disk is silently omitted rather than
   reported, so an incomplete project disappears instead of failing.

**Still open**

5. ~~**The AI does not ask for what it assumes.**~~ **Done 17 Oct.** Every
   proposal carries its `questions[]` (four per proposal, two per batch),
   generated by code from the parameters the run publishes, and an answered
   depth is honoured end to end. See the Phase 2 paragraph above.
6. ~~**A planner cannot edit an assumption.**~~ **Done 17 Oct.** The gate edits
   a parameter in place, the label becomes `USER_ASSUMPTION` with the replaced
   default named, and `apply_proposals` runs `validate_batch` before it pins.
   The disclosure hole this item described is closed and asserted by
   `test_sandbox`: a hand-set depth is kept and labelled the sender's own.
7. **An intervention is an axis-aligned slab, not a shaped object.** `extent_ring`
   and `_fraction_ring` emit five-point axis-aligned rings, and `STYLE_FOR_KIND`
   gives each kind one `extrusionHeight` — so a reservoir is a box, not a basin.
   `_components` already computes the true cell mask; contour it, then give each
   kind a massing profile.
8. **The second planned project.** Adding one is a data edit under
   `backend/data/projects/`, not a code change — but the project itself does not
   exist yet.
9. ~~**`siting.py:150-152` still uses the loose bounding-box mask.**~~ **Done
   17 Oct.** Both pools filter with the gate's own union. Measured before the
   swap: no site moved more than 16 cells and the smallest (83 cells) was
   unchanged.
10. ~~**`clip_window` under-reports `clippedCells`.**~~ **Done 17 Oct.** It
    reports `window − served`, so a fragment stranded by the largest-ring
    contour is counted with the buildings.
11. **The two rainfall defaults differ** (`RunRequest.rainfall_mm = 50.0` vs
    `DEFAULT_RAINFALL_MM = 450.0`). Deliberately not unified: it would move
    published run numbers and deserves its own decision.
12. ~~**`docs/DESIGN_intervention_proposals.md:84`** describes `building_mask`
    as `building_footprint_mask(...)`~~. **Corrected 17 Oct**: it states the
    union, the ~23-cell difference, and the whole-shortfall rule for `clippedCells`.

**Configuration and environment**

13. **A live provider is still verified only by hand.** `test_llm_provider.py`
    drives the real chat through a scripted `FakeProvider`, with no key and no
    network. It covers every retry and fallback path, plus the request shapes both
    real providers send. What it cannot cover is a real provider's *response*.
    That is still checked by hand, and the free tier has been returning 429s.
14. **A rate-limited provider costs a round trip.** The free tier is throttled and
    slow. A 429 is caught and reported, and `LLM_FALLBACK_MODELS` now walks three
    additionally-verified free ids (`google/gemma-4-26b-a4b-it:free`,
    `cohere/north-mini-code:free`,
    `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` — each called and
    confirmed to return prose on 14 Oct). A paid model id — one `.env` line —
    removes the problem instead of routing around it.
15. **Storage is JSON files, not PostGIS.** The seam is `app/db.py` and
    `scene_service.py` only, so this is a swap rather than a rewrite.
16. **Two view modules have no unit coverage** — `view/onlineProps.ts` and
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

**Rotate, tilt and zoom are three independent axes, and the gestures now match
what each device is expected to do.** Two button-press complaints — "zoom doesn't
zoom based on where the user is, but relative to default", and "tilt past a
certain limit, it just starts rotating instead" — traced to one design error.
`orbitBy`/`zoomBy` read `camera.heading` and `camera.pitch` back off the camera
and rebuilt the *whole* pose from them, so heading and pitch were coupled on
every move, and near the pitch limit that heading is ill-conditioned (`getHeading`
is the angle of `cross(direction, up)`, whose length vanishes towards nadir).
Each axis is now a delta applied to the live camera inside a frame centred on the
orbit pivot: heading turns about the local vertical, pitch tilts about the
camera's own horizontal `right`, and a zoom dollies along the line to the point
under the view centre. None reads another's value. Measured: tilting past −88
leaves the heading unchanged to 0.00°, rotating at the limit leaves pitch
unchanged to 0.00°, a zoom moves neither, and the point under the view centre
moves **0.0 px** through a zoom after a pan (it moved 464 m under the old
behaviour).

**The gesture map**: mouse wheel zooms, right-drag tilts (vertical) and rotates
(horizontal); trackpad pinch zooms, two-finger drag tilts and rotates; keyboard
`+`/`−` zoom and the arrow keys tilt and rotate. The wheel and the trackpad drag
are the *same DOM event*, so `classifyWheel` separates them on shape — a mouse
notch is one large `deltaY` with no horizontal component and over 120 ms since
the last one; a trackpad streams many small fractional deltas. This is why the
wheel used to tilt: the two could not be told apart, so the gesture that had to
be chosen for the trackpad was applied to the mouse as well. Touch-screen `PINCH`
stays bound to zoom; it arrives as touch events, not wheel events, so it does not
compete.

**The "second quick press does nothing" freeze was the frame rate, not a Cesium
flight.** It was logged as unexplained during the earlier attempts: about 1 run in
10–20, a second quick zoom or tilt press left the camera at the first step with a
flight still marked active. The buttons glided for 0.3 s on
`requestAnimationFrame`, and in the headless software renderer the browser suites
run in, rAF fires roughly once every two seconds — so the "flight" got a single
tick and a press arriving before the next frame got none. Every camera move is now
applied outright, so it cannot be starved of frames, and two presses add up
because each applies its whole delta to the live camera. `verify-nav` asserts the
two-press case instead of skipping it: 1437 m → 447 m on two presses, against 791
for one.

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
