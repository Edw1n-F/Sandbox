# Changelog

Every non-trivial change gets an entry. Newest first.

Gap numbers quoted inside an entry are the ones current **on that entry's date** —
they are not retroactively renumbered. The live list is [`STATUS.md`](STATUS.md).

## 2026-10-14 — close stage 2: finish the `place` deletion, purge the orphan store, report a 429

### Removed — `place` from the natural-language front door

`8557458` removed `place` from `ai_plan.py`'s parser but left it declared in
`MassingOp.SUPPORTED` and callable from `scenario.py`, so nine `test_ai_plan`
checks went red against a capability no instruction could reach. The deletion is
now finished on the **front door**: `place` is absent from the parser, the tool
schema and `_massing_feature`, and a model that emits one is refused. The **dormant
capability stays** — `scenario.apply_massing` still scores a `place` op handed to
it directly through `POST /scenario/evaluate`, and `placement_footprints` is kept
rather than restored later. The three placement tests are replaced by one that
pins the new behaviour (a model-emitted `place` returns nothing, the schema no
longer offers it, and a tower sentence is not read as an edit), and the other stale
assertions are repointed at ops that still exist. `test_ai_plan` 32/41 → 40/40.

### Fixed — a rate-limited provider returned a 500

Found against the live free-tier model: `urllib.error.HTTPError 429` escaped
`generate_plan` uncaught, `scenes.py` caught only `PlanGenerationError`, and
`ProjectCatalog.toggle()`'s `try/finally` has no `catch` — so a throttle silently
did nothing and the user saw no error. `generate_plan` now catches
`URLError`/`TimeoutError`/`ConnectionError`. With `provider=auto` the failure is
named in the response (`"rate limited (HTTP 429)"`) and the labelled heuristic
answers if it can; with an explicit `provider=llm` it raises `PlanGenerationError`,
which the router already turns into a 422 — the same rule as "no key → 422". New
test covers both. A 429 is now reported, not hidden.

### Fixed — all three scenario sliders were unnamed to a screen reader

`SliderField` set `aria-labelledby` to `${label}-slider`, an id no element carried,
so every slider's accessible name resolved to nothing. The `<Typography>` caption
now carries the (slugged) id. Found by the rewritten `verify-ux` check; 26/26
focusable controls now have accessible names.

### Removed — the store residue that could never change a number

The seeded derived-intervention plan, its 7 interventions, 2 residue plans and 6
loose interventions were all authored against the retired 786 ha extent, so every
polygon fell outside the 43.5 ha grid and scored exactly zero. Deleted, and
`.gitignore`'s allowlist narrowed to the two files that remain committed (the
seeded scene and the baseline plan). Seven tracked deletions are recoverable from
git; the untracked residue is gone.

### Fixed — `scipy` had no wheel on this host

`requirements.txt` pinned `scipy<1.14`, a macOS workaround, and 1.13.1 has no
cp314 wheel — so `test_contract` and `test_ventilation` could not run here at all.
Now `scipy>=1.16.1` (the first release with a cp314 wheel; 1.16.0 and earlier have
none). Installed 1.18.1. `rasterio` installed so `preflight.py` reports Ready.

### Fixed — hydrology and ledger pins were stating the pre-swap world

`test_hydrology` and `test_ledger` still pinned counts and hashes from before the
real-data swap. Each value was **recounted independently** rather than copied from
the code's output, because re-pinning to whatever the code prints turns a test into
a snapshot: 251 depressions confirmed against a scipy `ndimage.label` count at
8-connectivity, 2,033 depression cells, 35 drainage cells at the accumulation
threshold, ground range 3.41–17.29 m, headline 180,566.3 m³. The two artefact
hashes changed because the rasters were regenerated on 13 Oct.

### Rewritten — both browser suites

`verify-3d.mjs` and `verify-ux.mjs` were bound to one developer's machine (a
hardcoded Playwright path) and gated on the deleted procedural model's entity count
(`n > 2000` against 2,639 envelopes, so the wait always timed out and the script
still exited `0`). Rewritten around what the app actually does: tileset load as the
readiness gate, overlays read from the named Cesium entities that carry the per-run
PNG URL, and camera checks measured on the camera state. New shared
`scripts/_verify-common.mjs` resolves Playwright from `$PLAYWRIGHT_PATH` or the
repo, and both scripts now exit `2` when a server or Playwright is missing — so a
missing server can never read as a pass. `verify-3d` 17/17, `verify-ux` 21/21.

### Deleted — `dump-shader.mjs` and `alembic.ini`

`dump-shader.mjs` printed the GLSL for `techMaterial.ts`, deleted in stage 1.
`backend/alembic.ini` was empty and contradicted the documented "no alembic, no
migrations" position.

### Docs — the substrate swap is now recorded, not just done

`STATUS.md` rewritten: a stage table (0–5) with 2 marked complete, the placeholder
catalog and the massing-only parser named as such rather than described as working,
measured test counts, and the gap list replaced by one "Open work" list with a
short resolved block. The "who does what" lane table is **removed** — it was
written before the substrate swap and its ownership no longer described the work.
`DATA_SOURCES.md` now documents what actually ships: the Open3Dhk terrain tiles
(the real DTM, with the mesh-surface caveat), Overture + tiles for buildings (with
the fusion thresholds and the "not surveyed storeys" caveat), the fused land class
(WorldCover + Sentinel-2 + trees + footprints), and the Landsat LST anchor. It also
records two findings that were only in code: **the window has zero water and zero
wetland because the raw ESA tile really is 87.9 % built-up**, so this is an honest
crop that leaves almost no sponge to squeeze; and the **0.146 m mPD/mCD offset is
now live** rather than dormant, because the tide toggle quotes mCD against
elevation in mPD and no correction is applied. `README.md` and `CLAUDE.md` corrected
throughout: port `8000` → `8001`, Python `3.13` → `3.14`, the stale "~90 m
Open-Meteo" elevation row, the deleted `seed_interventions.py`/`make_citymodel.py`
steps, the removed-venv instructions, and the stale "~786 ha" extent.

### Measured — the run is ~11 s, not 0.7 s

The `0.7 s warm` claim in `STATUS.md`, `README.md` and `ARCHITECTURE.md` was
authored before the real-data swap and does not reproduce. Measured at the API and
again in a browser: **~11 s, cold and warm alike.** The figure is removed rather
than restated, because the honest claim is "far faster than the 10-day workflow it
replaces", not a number that only held on a synthetic grid.

## 2026-10-13 (part 4) — flood no longer paints building roofs

### Fixed — water visible on top of towers

The flood overlay was rendering on building roofs. Cause: with 3D Tiles present
the viewer draws both overlays as a **classified rectangle**
(`classificationType: BOTH`, SandboxViewer) so they drape onto the visible
surface — which is the `terrain(TB)` tileset, not the globe terrain. Cesium's
`ClassificationType` has no per-tileset exemption (`Cesium3DTileset#classificationType`
configures what a tileset *classifies*, not whether it *receives* classification),
so terrain-only classification would have hidden the overlay entirely behind the
terrain tiles. The fix is therefore at the data-to-render boundary, not in Cesium:
the depth field is solved on **bare earth**, so a pond can land where a tower
stands, and those cells are now suppressed in the PNG only.

- `_overlay_common.building_footprint_mask()` — one shared mask (rasterise the
  massing, `height > 0`), used by both the offline script and the live run.
- `scripts/make_wetgrid.py` and the run render path mask the raster before
  painting; the sidecar records `render.buildingsMaskedFromRender`.
- Measure of the size of it: **44.8%** of the grid lies under a footprint and
  **3142 of 6725** wet cells were being painted onto roofs.
- The data are untouched — `wetgrid_hsk.npy` keeps every solved cell, and the
  ledger still counts them. A display transform must not delete a number.

New tests: `tests/test_overlay_masking.py` (3) — mask is a real boolean raster,
the committed PNG is transparent under every footprint, and the `.npy` still
holds water under footprints (so the mask cannot start eating solved cells). 95/95.

**Heat is deliberately not masked.** A roof is a real hot surface, and masking
footprints would delete the built-up heat signal across the largest land class on
the site. The same artefact (a ground-level value painted onto a tall roof) still
applies to the heatmap; fixing it means a per-surface treatment, not a blanket mask.


## 2026-10-13 (part 3) — the heatmap stops reading as a blank, heat buttons get warning names

### Fixed — anomaly ramp scale

The first browser run of the anomaly map was washed out: a faint tint over a
mostly-white grid. Cause: the ramp's near-white neutral band was ±1 °C while the
real site's anomaly distribution is tight (91% of cells within ±2 °C), so
nearly every cell landed in the neutral band. Two further passes settled it:
the first transparent-at-zero attempt left only ~20% of cells see-through and
still read as an opaque cream sheet, so the final ramp (a) carries **no
white/light stops at all** — every colour is a saturated hue — and (b) uses a
0.4 °C dead-band (fully invisible near the mean) with alpha ramping to opaque by
2.5 °C. Result: 0% near-white pixels, ~16% of cells fully transparent, and a
map whose visible marks are genuinely hot or cool spots.

### Fixed — stale overlay after re-render

`scene_service._resolve_overlay_url` now appends an `?v=<mtime>` cache-buster.
Without it the browser served the first copy of a regenerated PNG, so a ramp
change appeared to do nothing until a hard refresh.

### Fixed — ProjectCatalog font

Project names (School, Pond infill, …) used `variant="body2"` (sans) while the
rest of the panel is monospace; they now carry `hud-mono` like every other
readout.

### Changed — heat presets become HKO warnings, baseline overlay reuses the live model

- `SlidersPanel.tsx`: the heat buttons now read as HKO warnings (Hot night
  ≥28 °C air / Very Hot Weather Warning ≥33 °C / "extremely hot weather" alert
  ≥35 °C), styled like the rainfall signal buttons and highlighting the active
  one, mirroring how the rainfall side uses the Observatory's rainstorm signals.
  Dark signal colours were lightened (`#1a1a1a`→`#9aa0a6`, `#d32f2f`→`#ff6b6b`)
  so "Black"/"Extreme" are legible on the dark HUD, labels no longer clip
  (`nowrap` removed, "Extreme heat"→"Extreme"), and captions were trimmed to
  one line each.
- `scripts/make_heatmap.py` rewritten to render through the same
  `temperature_field` + `anomaly_rgba` path as the live run, replacing the old
  0-1 relative index and its `--heatwave` display stretch. Baseline overlay and
  a live run overlay can no longer drift apart. The sidecar now carries surface/
  anomaly/air means and the LST source instead of the index stats.

92/92 backend tests pass; frontend typechecks clean.

## 2026-10-13 (part 2) — the docs stop counting twice

No file was unnecessary; the redundancy was *inside* `README.md`, which had grown
into a monolith restating every other document. Shortened, nothing deleted.

The interesting part is not the line count. `README.md` carried stale per-suite
counts in **three** separate places — `test_contract.py` "11/11" against a real
23, `test_ai_plan.py` "30/30" against a real 41, and a loop labelled "404 checks,
11 suites" against a real 498 across fourteen — and `docs/ARCHITECTURE.md` said
"two suites" and pinned 11 / 18 / 30 tests. `CLAUDE.md` had already written the
rule down ("read STATUS.md — do not trust a number typed into a comment"); the
rule was not being followed. **The counts now exist in exactly one place, and the
other documents point at it.** Updating a number in N places is a chore; the fix
is N = 1.

### Changed

- `README.md`, 1120 → 951 lines. Removed the layer table (a duplicate of
  `CLAUDE.md`), the four-point design-decision restatement (→ ARCHITECTURE, kept
  as four one-line claims so the front page still says what kind of tool this is),
  the test-internals prose (→ CHANGELOG), the deep heat-overlay and wet-grid
  detail (→ DATA_SOURCES and the research chapters), "Next steps" (→ the STATUS
  gap list, the single backlog) and the committed/not-committed tables (→
  FILE_STRUCTURE and `.gitignore`).
- `docs/ARCHITECTURE.md` and `docs/research/ch05_way_forward.md` — counts removed
  rather than updated, for the same reason.

### Fixed

- **`backend/requirements.txt` cited two documents that no longer exist**
  (`HEAT_AND_WIND_PROGRESS.md`, `OCT6_STATUS.md`), in the comments justifying the
  scipy pin and the absent PostGIS stack. A previous cleanup deleted the files and
  left the citations.
- **`CLAUDE.md` stated the opposite of the truth about the test store**: "the
  suites write into the real store — none of them redirects `settings.*_dir`".
  Untrue since `tests/_tmpstore.py`; it now describes `isolate_store()` and why a
  residue *plan* makes a residue intervention read as live.
- One dead link in this file, left by the 12 Oct docs reorganisation — a pointer
  has to resolve.

## 2026-10-13 — the gap list closed out: 29 entries to 16

The 29-entry backlog had drifted into a scrapbook: eleven entries were closed but
still sitting there struck through, and four pairs described one job each. This
closes the list out — the closed entries are replaced by a real history entry
(here), the merges are made, and the survivors are renumbered **1–16** with a
translation table from the old numbering so a number someone already quoted can
still be resolved.

The rule that produced 29 entries is what changes: an entry now earns its place by
being *open work*, and the moment it closes it leaves the list and lands in this
file.

### Closed

- **Cumulative project selection.** `POST /run` accepts `planIds[]` and merges
  every selected plan's features through `intervention_features_many()`;
  `ProjectCatalog.tsx` is multi-select. `MAX_TOTAL_COVERAGE = 0.40` joins the
  per-intervention `MAX_INTERVENTION_COVERAGE = 0.25`, because a list of
  individually-legal interventions can still compose into a district-scale
  land-use change; the cumulative guard is enforced in both
  `apply_interventions` and `sanity_checks`.
- **Two land classes were missing.** `LAND_CLASSES` appends `floodable` and
  `tank` (appending keeps every existing index and raster valid), with entries in
  `CURVE_NUMBER_HSG_D`, `HEAT_INDEX`, `COUNTED_AS`, `STORAGE_DEPTH_M` and
  `CN_CITATIONS`. `flood_storage` is remapped from `water` to `floodable`, so a
  floodable park is no longer modelled as a permanent water body.
- **Storage volume was not parameterised.** A `storage_over` dict mirrors the
  existing `cn_over` mechanism, so an intervention's `storageDepthM` param is
  honoured per intervention rather than via the global `STORAGE_DEPTH_M`.
- **The stats panel showed 4 of the 7 brief stats.** `storageCapacityM3`,
  `imperviousShare` and `greenAndWaterM2` reach the ledger, the delta, the
  TypeScript types and the panel's rows (4 → 7).
- **No sanity checks.** `sanity_checks()` returns warnings — never raises — for
  storage > runoff, wet area > site area, impervious share > 100 %, coverage over
  either cap, and CN outside 30–100.
- **Run output was one shared directory.** A run writes under
  `data/rasters/run/<runId>/…` with pruning, so two concurrent planners cannot
  overwrite each other's overlay.
- **The suites wrote into the real store.** `tests/_tmpstore.py` redirects
  `settings.scenes_dir` / `plans_dir` / `interventions_dir` at a temp copy of
  `data/`, and the creating suites use it. `.gitignore` had stopped the *commit*,
  not the *accumulation*.
- **Error states were only partly closed.** A scene with no terrain and a plan
  with no interventions now render a `SceneNotice` instead of a blank surface.
- **Grounding could flag a correct number.** The fix does **not** loosen the
  check: the run publishes a `stats.ratios` block (`unstoredOverRunoff` plus four
  siblings) and `build_facts` forwards it verbatim, so a derived percentage
  grounds because the run now stands behind it. A test pins the old behaviour too,
  so the guard cannot rot into "accept anything".
- **Hygiene.** Seven orphaned frontend components deleted, with every reference
  that named them; two host-dependent one-second timing assertions converted to
  ratios measured in-process; the chat's pure half given a suite.

### Added

- `backend/tests/test_agent.py` — ten keyless, networkless assertions over
  `app/agent/grounding.py` and `build_facts`, including the "95 %" case.
- `frontend/src/components/SceneNotice.tsx`, and `sanityChecks` on the run
  response.

### Fixed

- **`test_ledger`'s "no shipped seed is over-large" check was red on this host,
  for the store rather than for the code.** It globbed `data/plans/`, which also
  collects residue, so a leftover plan made a leftover intervention read as
  "live". It now asks git which plans are actually shipped (`_shipped_plan_files`)
  and fails loudly if git cannot answer. Its stale docstring — which claimed the
  *shipped* seeds were the badly-sited ones — is corrected: the shipped seven are
  all ≈2 % coverage and the absurd polygons are built in code.
- In-source `gap N` comments renumbered against the new list; the ones that
  pointed at a now-closed entry no longer quote a number.
- `docs/FILE_STRUCTURE.md` and `.gitignore` said the suites "write into these
  directories directly (none of them redirects `settings.*_dir`)". They do now,
  and both say so.
- `STATUS.md` counts re-measured on all fourteen suites: **498 backend** (was 488
  across thirteen, before `test_agent` existed and before `test_run` and
  `test_ventilation` each gained a check) and **56 frontend unit** (was 55). The
  "45th ventilation check fails on this host" note is gone — that check was an
  absolute timing bound and is now a ratio.

## 2026-10-12 (part 4) — the chat agent: an answer you can audit

The run loop answered "what happened" with one paragraph. A planner's next
question is "why, and what should I do" — and that answer is only useful if the
reader can tell which numbers came from the model and which came from the run.
This adds a chat whose answer is a card, not prose, with every figure checked
against the run before it is shown.

Shape follows the Constellation reference (structured `Answer` card, provider-
neutral client, SSE stream events, number grounding) minus the parts a sandbox
run does not have: no skills, no model-chosen tools, no clarification cards, no
thread persistence.

### Backend — `app/agent/`

- `answer.py` — `AnswerCard` and `template_card()`. The card is the unit: the
  same fields are always present, so the UI renders them in the same place and a
  missing cause shows as `cause: null` rather than as a paragraph that omits it.
- `llm.py` — one client, two calls (`complete_json`, `stream_text`). Anthropic
  forces the schema through `tool_choice`; OpenAI-compatible asks for
  `response_format` and drops it on a 400.
- `grounding.py` — every number in a card is checked against the run's facts at
  any scale or rounding; small integers and years are exempt.
- `prompts.py` — grounding rule #1 in the system prompt, no hedging, honest
  confidence.
- `chat_service.py` — three visible steps and **one** grounding revision, then
  report. Constellation loops until the model converges; here a second failure is
  surfaced rather than retried, because a planner shown an ungrounded number with
  a note is better served than one watching a spinner.
- `routers/chat.py` + `schemas/chat.py` — `POST /api/scenes/{id}/chat` and
  `/chat/stream`. The run state is sent by the client per request (ADR-024), so
  there is no server-side thread.

### Frontend

- `api/chatApi.ts` — `streamChat` over `fetch`, not `EventSource`, which cannot
  POST. Abortable.
- `ChatPanel.tsx` — rewritten as a card view plus a stepper. Partial JSON fields
  are extracted by regex mid-stream so the headline appears while the model is
  still writing. Interventions still route to the AI planner; questions go to the
  chat. A stop button cancels the stream.

### Free-tier compatibility

Verified end to end against a live OpenRouter `:free` key. Four things broke and
are now handled:

1. `response_format` is rejected with a 400 by most `:free` models →
   `LLM_JSON_MODE` (`auto` / `json` / `off`), and the JSON contract is in the
   prompt so `off` still parses.
2. Some models reject `stream: true`, and reasoning models can stream **zero**
   `content` deltas (every token goes to the reasoning channel) → one unstreamed
   retry, reported as `card drafted (unstreamed)`.
3. `max_tokens=900` starved reasoning models — 382 tokens of thinking, then
   `finish_reason: length` and a card truncated mid-object, which parses to
   nothing. Measured: 1600 completes. `MAX_TOKENS` is now 2000.
4. One `:free` model is a single point of failure — shared pools throttle without
   warning → `LLM_FALLBACK_MODELS`, tried in order on 429/5xx/400/empty content.

### Also

- `scene_service.py` — resolve `cityModelUrl` and its provenance on `SceneRead`
  too, not only on `/terrain`. A URL that is null on one endpoint and set on
  another is the kind of half-truth that breaks a different consumer later.
- `fix(view)` — the tactical view had washed out to a glowing white slab. Bloom
  was not the cause: `u_lit` was 1 on all 2,639 materials because the `setLook`
  effect ran while `cityModelHandle` was still null, so every material booted in
  the survey branch. Fixed with a module-level `activeLook` plus applying the
  store's look as soon as the handle lands. Bloom rebuilt on measured values
  (contrast 4.5, brightness -0.3, delta 1.0, sigma 2.0 — the old 210 was a
  threshold misread).
- `fix(view)` — Run was killing the frame loop with "This object was destroyed":
  `imageryLayers.remove(layer, true)` and `clippingPlanes.destroy()` both fired
  mid-render. Both now detach and defer the destroy by 2 s.
- `feat(ui)` — Constellation's design language: glass recipe, type scale, eyebrow
  labels, the 1px-gap stats grid, the icon-button pill. Signal-green kept, so the
  2D chrome and the 3D tactical renderer stay one palette.

## 2026-10-12 (part 3) — the run loop: the sliders now mean something

The prototype had a 3D sandbox, a storage ledger and NL interventions, but no
loop joining them: the rainfall and temperature sliders wrote to local state
(ADR-024) and the wet grid / heatmap were static PNGs rendered offline, so the
panel described a simulation the app could not run. This closes that.

### `POST /api/scenes/{sceneId}/run`

One call, in the order the brief specifies: read the scenario (plan features +
climate settings) → apply the land-cover change each intervention actually
implies → re-run the project's own SCS-CN generation/retention arithmetic at the
requested rainfall → re-solve the level-pool fill → re-render both overlays →
report the delta against today, with a plain-language explanation.

- `app/services/run_service.py` (new). Consumes the ledger's own functions,
  never a second copy of them: `apply_interventions` was **extracted** from
  `ledger_for_scenario` so the grid the run renders is exactly the grid the
  ledger scores. `cn_grid_for` was likewise extracted so the per-cell CN override
  path (permeable paving) cannot drift from the class-driven one. The level-pool
  solver and both colour ramps are imported from `make_wetgrid` /
  `make_heatmap`.
- **The live run reproduces the committed overlay exactly.** At 450 mm a run
  reports 3,170,180.3 m³ unstored and 208.05 ha wet — the offline artefact's own
  figures, to 0.04 m³. Pinned by `tests/test_run.py` (29 checks, new suite).
- 0.7 s warm. The terrain is not re-conditioned (that stays offline) and the
  sky-view/shade rasters are cached against the city model's mtime, because
  neither depends on a slider.
- Run output goes to `data/rasters/run/`, never over the canonical
  `wetgrid_hsk.png` / `heatmap_hsk.png`, and is gitignored.

### Frontend

- `run()` in `sceneStore` sends the slider values and the active plan, then
  points the viewer at the new overlay URLs (cache-busted by run id) and
  switches both overlays on — a run whose result is invisible is
  indistinguishable from a run that failed.
- `SandboxViewer` now tracks each overlay's URL and rebuilds the provider when
  it changes. Without this the new PNG was never fetched: the effect only
  toggled `show` on the existing layer.
- `components/RunSummary.tsx` (new): the run's numbers with signed deltas
  (worse red / better green), the explanation paragraph, and a line naming which
  source wrote it.

### Honesty, kept

- The heatwave preset triggered above 35 °C is `make_heatmap`'s documented
  display stretch, not a scenario: `test_run` asserts the land-class heat index
  is **bit-identical** across presets while the blended field moves.
- Explanations are labelled `template` when no model is configured, for the same
  reason the AI-plan badge names the deterministic parser.
- Every response and both sidecars carry `status` / `warning` unchanged.

Refactor, no behaviour change: `_ledger_from_cn` no longer re-implements the
ADR-015 partition, and `ledger_for_scenario`'s dead `cn_override` parameter is
gone (declared, never used by any caller). Backend 479 → 508 checks; all
thirteen suites re-run.

## 2026-10-12 (part 2) — one home per fact, and a stale comment that hid a bug

Second pass at the same cleanup. The first pass removed files with no inbound
references; this one removes files whose *content* was duplicated elsewhere, and
repairs the 21 code and doc citations that pointed at them.

### Removed (duplicated content, not just unreferenced files)

- **`docs/HEAT_AND_WIND_PROGRESS.md`** (27 KB). Every section had a home already:
  the AVA findings and citations are in [`docs/research/ch02`](docs/research/ch02_wind_ventilation.md),
  its eight-stage plan table was a stale copy of `STATUS.md`'s stages table
  (still saying "Stage 2 is next" months after Stage 7 shipped), and its
  progress log restated `CHANGELOG.md` entry by entry.
- **`docs/OCT6_STATUS.md`** (19 KB). A dated snapshot whose own header admitted
  sections 2.2, 4 and 6 no longer held. Its three load-bearing decisions all
  survive elsewhere: D2 (store mode) is Decision 1 in `ARCHITECTURE.md` and is
  asserted by a contract test; D3 (the HSK/HT bbox over the 27-task plan's Deep
  Bay anchor) is in `DATA_SOURCES.md` and `CLAUDE.md`; the assumption register is
  in `DATA_SOURCES.md`. `RUNG_AND_BUSINESS.md` cited it as the evidence base,
  and every claim it rested on is carried in the surviving files.
- **`docs/PHASE1_SCOUTING_BRIEF.md`** (11 KB). An input to the outline planner
  that had already been consumed into the research chapters; zero citations.
- **README's eight duplicated design decisions** (~7.5 KB → 3 KB). Decisions
  1–5 were verbatim `ARCHITECTURE.md`; the file had drifted out of agreement
  with the seven it did not restate. Replaced with a pointer plus the four
  points worth having on the front page — the parts that were *not* duplicated.

`docs/` is now five entries and one directory.

### Fixed (a stale comment that was hiding a real bug)

`scene_hsk.json`'s `dataSources` block claimed **"No real DTM is shipped... the
wet grid therefore runs on a synthetic surface."** That has been false since
6 Oct: elevation is real, sampled from Open-Meteo at a measured ~90 m posting and
verified against Everest and open-ocean control points on every run. The land-
cover note likewise still said the classifier was "a coarse synthetic/OSM-derived
surrogate" awaiting a third party, when it is ESA WorldCover 10 m v200. Both
notes rewritten to state what the data actually is.

Following that thread found a genuine bug. `seed_scene.py` set
`terrain_url=None` on the **create** path with a comment explaining that no real
DEM existed — but it omitted `terrain_url` entirely on the **refresh** path,
where `SceneUpdate` therefore left the existing value alone. So the committed
scene carried `dem_terrain_rgb.png` while a fresh `--reset` seed would have
produced a scene with no terrain at all, and only `fetch_dem.py` re-attaching
the overlay afterwards hid it. Both paths now derive `terrain_url` from whether
`dem_terrain_rgb.png` is actually on disk, and say so on stderr when it is
missing: a URL to a file that does not exist 404s on every tile request, while a
null boots a visibly flat ellipsoid, which is honestly flat. Verified by
re-seeding — the scene keeps `dem_terrain_rgb.png`.

### Changed (citations)

21 references to the deleted files were repointed **deliberately, one at a
time**, not by blanket search-and-replace, because each named a different
rationale: the bbox provenance goes to `DATA_SOURCES.md`, the store-mode
argument to `ARCHITECTURE.md` Decision 1, the contract decisions to
`ARCHITECTURE.md` + their ADR numbers. Ten are in code comments and docstrings
across nine modules (`geo_import`, `config`, `main`, `models/plan`,
`schemas/plan`, `schemas/scene`, `routers/terrain`, `_overlay_common`,
`terrainLoader.ts`, `scene_hsk.json`). The rest are in `README.md`,
`CLAUDE.md`, `STATUS.md` and `research/ch05`.

### Added (the rule that prevents this recurring)

`CLAUDE.md` now states **one home per fact**: `STATUS.md` is the only live
statement of what works, `ARCHITECTURE.md` the only home for design rationale,
`CHANGELOG.md` the only history. A claim repeated in two files is a claim that
will disagree in one of them — which is precisely how the test counts, the stage
table and the progress log all went stale before this cleanup.

### Measured

No code path changed except `seed_scene.py`'s `terrain_url` derivation, so no
number moves: `wetgrid_hsk.npy` and `flow_accum_hsk.npy` md5s are unchanged
(`d7cc052a…`, `4f9c4689…`). Zero broken internal markdown links across the repo.

## 2026-10-12 — repository cleanup, and a test that was passing on garbage

A read-only audit of all 206 tracked files, by inbound-citation count rather
than by eye. Removed 64 files: **206 → 148** tracked. The interesting finding is not what was deleted
— it is the test that only passed *because* of what was deleted.

### Fixed (a test coupled to leftover residue)

`test_ledger`'s over-large-intervention guard scanned
`backend/data/interventions/` for badly-sited polygons and then asserted
`len(absurd) > 0` — "or the guard is untested". It passed because stale
documents from earlier runs were sitting in the store. Deleting those documents,
which is the entire point of the cleanup, turned a real check into a failing
one: **39/39 → 38/39**.

A guard tested by garbage is not a guard. The absurd geometries are now built
synthetically from `grids.bounds`, so the check is a property of the code rather
than of whatever the store happens to hold:

- the site-covering fixture is itself asserted to cover the site (100.0% of
  664×474) — the first attempt built it from an unrelated fixture ring and
  covered 4%, which would have made the guard pass for the wrong reason;
- a site-covering `flood_storage` and `green_space` are each refused, not
  applied;
- a normal 11,433-cell polygon still applies, so the guard is a refusal and not
  a filter that drops legitimate work;
- and separately, **no live seeded intervention is over-large** — that check now
  skips documents no plan references, so it cannot be re-coupled to residue.

`test_ledger` **39 → 42**, and the guarantee is now real.

### Removed

- **`docs/drafts/` entirely** (8 files, ~146 KB): four DESIGN/PRD documents for
  work that already shipped and is recorded in this changelog, plus
  `framework.json`, an intermediate JSON build artifact of the same report. Zero
  inbound references from anywhere.
- **Three superseded `*_draft.md` chapters**, each a strict textual predecessor
  of its `*_revised.md` sibling (29, 12 and 4 differing lines respectively).
  Keeping both meant two files could disagree about the same physics with no way
  to tell which was current.
- **`docs/_技术文档_extracted.txt`** — the raw docx extraction, superseded by
  `_clean.txt` and referenced by nothing.
- **16 ad-hoc AI plans and 34 orphaned intervention documents** from the JSON
  store. Only 2 plans (the baseline and `seed_interventions.py`'s output) and 7
  intervention documents are referenced; `list_interventions` reads the plan
  document and never the loose files, so all 34 were unreachable.

### Fixed (the cause of the above)

**The suites write into the real store.** No test redirects
`settings.scenes_dir` / `plans_dir` / `interventions_dir` at a temporary
directory, so every run of `test_contract`, `test_scenario` and `test_ai_plan`
leaves records behind, and 18 plans had accumulated in git. `.gitignore` now
allowlists exactly the three seeded fixtures and ignores the rest of those three
directories, with a comment saying why. A new test suite that creates records
must now clean up after itself or use a temp dir.

### Changed (docs)

- `STATUS.md`, `CHANGELOG.md` and `HEAT_AND_WIND_PROGRESS.md` cited
  `Hack4SDG_Heat_Wind_Drainage_Research.md` — **a file that does not exist in
  this repository**, in five places. The research chapters it referred to were
  scattered across `docs/`, `docs/drafts/` and under draft filenames. They are
  now `docs/research/ch01`–`ch05` with an index, and every citation points
  there. A dangling reference to a design document is worse than a missing one,
  because it reads as though the reasoning is recorded somewhere.
- The three inline test counts in `CLAUDE.md` (`11/11`, `16/16`, `10/10`) and
  the two in `README.md` (`11/11`, "404 checks") were 3–8 months stale. All five
  replaced with a pointer to the measured table in `STATUS.md`, plus an
  instruction not to retype them.
- `CLAUDE.md`'s "Where things are written down" pointed at
  `docs/OCT6_STATUS.md` for "what actually works". That file is a **dated 6 Oct
  snapshot** whose own header says sections 2.2, 4 and 6 no longer hold.
  Repointed at `STATUS.md`, with the snapshot labelled as such.
- Fixed a duplicated `preflight.py` line in the README project-layout tree.

### Kept, deliberately

- **`validate_geometry()` in `geo_import.py`** has no callers. It enforces the
  spec's EPSG:4326 mandate in §4.4. Deleting an uncalled validator is not
  cleanup, it is removing the thing that would catch the next mistake.
- **The 5 research chapters** are unreferenced by filename but are real
  deliverable content, so they were consolidated rather than deleted.
- `OCT6_STATUS.md` is stale in places but is an explicitly dated snapshot that
  the rung declaration leans on; it says so itself and is kept unedited.

### Measured

Backend **476 → 479** (`test_ledger` 39 → 42). All twelve suites re-run. The
`wetgrid_hsk.npy` and `flow_accum_hsk.npy` md5s are unchanged (`d7cc052a…`,
`4f9c4689…`), so nothing here moved a number — as a cleanup should not.

## 2026-10-10 — direct coordinate and footprint-size editing via GenAI

The GenAI sandbox now lets the model (or the user) directly change the
**coordinates and size** of 3D massing, not just height and orientation.

### Research

Reviewed the entire GenAI → 3D chain: `POST /ai-plan` (NL → tool call → grounded
features), `_massing_feature` (ops → GeoJSON `massing_height` features),
`SandboxViewer.tsx` (features → `applyMassingOps` → live Cesium entities), and
`ScenePanel.tsx` (user input → plan selection → 3D update). The six existing
ops (`scale_all`, `set_site`, `rotate`, `move`, `place`, `reset`) could change
height and position-by-offset but could not:
- name a **target coordinate** for a move ("move to the centre of the site");
- change a **footprint's size** ("widen the western blocks by 20%").

The project's original safety rule ("the model never supplies a coordinate")
was kept: the new `target_lon`/`target_lat` fields carry **study-area bounds**
in the schema and are **validated against the bbox** in `apply_massing` — a
coordinate outside the site is refused, never invented.

### Added

- **`resize` op** — scale the footprint's width and/or depth about its own
  centroid. `width_scale=1.2` means 20% wider; both axes default to 1.0 so a
  caller can resize one only. Frontend `scaleRing()` works in metres (isotropic
  in plan) and is tested.
- **`move` with `target_lon`/`target_lat`** — reposition footprints to a direct
  coordinate, validated inside the study-area bbox. The heuristic parser
  recognises "widen"/"narrow"/"broader" + optional percentage.
- **Schema fields** `target_lon`, `target_lat`, `width_scale`, `depth_scale`
  added to `MASSING_TOOL` with min/max bounds; `_massing_feature` validates
  them.
- **Frontend `scaleRing`** + updated `applyMassingOps` for both modes;
  `ScenePanel` shows resize/move-to-coordinate in plain language.

### Tests

- 8 new backend checks (test_scenario 66/66): resize widens widths, preserves
  heights/positions, depth-only, coordinate move to centre, out-of-bounds
  refused.
- 2 new AI plan checks (test_ai_plan 41/41): heuristic resize parsing, schema
  bounds on coordinate fields.
- 4 new frontend checks (cityModel.test.ts 50/50): `scaleRing` centroid,
  widening, no-op, closure.
- Two existing tests updated: the old "no coordinate field" assertion became
  "coordinate fields are bounded" — the guarantee moved from absence to
  validated bounds.

## 2026-10-10 — hourly storm simulation: rain and drainage, hour by hour

The two existing drainage readings are static: the level pool solves one total
volume, the routed cascade routes one total volume. This adds the time axis
— **`hourly_storm_simulation`** in `sandbox_model.py`, served by
**`GET /api/scenes/{id}/drainage/hourly`**.

### Research first (what the standard is, and what the data is not)

- **DSD design standards** (DEVB LegislCo replies 9316/13895/13898, July 2024):
  urban trunk drains 200-year; **main rural catchment drainage channels
  50-year** — the applicable class for HSK/HT. After the 2024 Stormwater
  Drainage Manual update the 50-year hourly design rainfall is **134 mm/h**
  (raised from 128 after the September 2023 storm); the 200-year parameter is
  155 mm/h. The full published 1-hour spectrum: 2-year 72.5 / 5-year 93.5 /
  10-year 107 / 20-year 119 / 50-year 134 mm (DSD, 2025).
- **The record event**: 2023-09-07, HKO HQ **158.1 mm/h** — the highest ever
  recorded (DEVB 13896). Black Rainstorm Warning threshold: 70 mm/h.
- **"Water pipes / reservoir drainage ability": no pipe asset data exists for
  this site in this project** — and none is invented. The capability read is
  the **published design standard used as a proxy**, stated as such in every
  response; reservoir/pond capacity comes from the project's own
  Priority-Flood depression capacities and STORAGE_DEPTH_M tables, already on
  disk.

### Added

- **`hourly_storm_simulation`** — CN generation per hour through the SAME
  `_runoff_and_unstored` (no second physics), per-cell retention pools carried
  hour to hour (capacity = the LARGER of the Priority-Flood depression
  capacity and the land-class retention allowance, never the sum — the
  cell-scale ADR-15 double count), overflow standing on the ground until the
  drainage capability removes it.
- **Enforced whole-simulation mass balance**:
  `rain_in == stored_gain + drained + standing` raises AssertionError on a
  leak, mirroring `hydrology.mass_balance`.
- **Cited reference hyetographs**: `hko_2023_09_07` (reconstructed hourly
  shape around the published 158.1 mm/h peak, 450 mm total — labelled a
  reconstruction, not a gauge trace), `dsd_50y_design` (constant 134 mm/h),
  `black_rain_70` (70 mm/h). Custom hyetographs accepted as `mm/h` lists.
- **The end-state routing view**: the final standing volume routed once
  through `fill_and_spill` (the one router) to show where it pools and what
  leaves the grid — explicitly never fed back into the march.
- **`drained_m3_per_hour` parameter** with `0` as the no-drainage
  counterfactual. **The capability read, measured**: on the 2023-09-07 storm,
  1,821,982 m³ generated — with the 134 mm/h standard, 1,515,884 m³ drained
  and **0 standing**; with zero drainage, **1,515,884 m³ standing** at the
  peak hour. The difference is what the design standard is worth here.
- **27 new checks** in `tests/test_hourly_storm.py`: the identity on the real
  district, provenance echoing, the proxy label, the counterfactual,
  full-pools-start scenario, four refusal cases, determinism, honest labels.
  Backend total **476**.

### Not claimed

- No pipe network, no outfall hydraulics, no sea-level interaction, no
  sub-hourly dynamics — all stated in every response's `warning`.
- The 2023 storm's hourly SHAPE is a reconstruction around the published peak,
  not a gauge trace; the response says so.

## 2026-10-09 — `place` and `reset` are scoreable, by delegation not duplication

Gap 7 closed. The scenario evaluator can now score the AI's two most-used
operations, and the mechanism is the whole point: **the evaluator does not
re-implement the generator, it calls it.**

### Added

- **`place` in `apply_massing`.** Calls the AI planner's own
  `placement_footprints()` — the same ESA WorldCover paved mask, the same
  `make_citymodel.build_blocks` pitch, the same occupied-cell exclusion — and
  converts each ring through a new shared `footprint_from_ring()` conversion
  extracted from `footprints_from_geojson` so there is exactly one
  ring→footprint definition in the project. Measured: `place count=3 east`
  adds exactly 3 envelopes (2,639 → 2,642), moves shade (+), and leaves the
  drainage delta at exactly 0 because placed blocks sit on already-paved
  cells — arithmetic the response still states. A scope with no unbuilt paved
  land places nothing and reports `placedCount: 0` rather than inventing a
  block in a field.
- **`reset` in `apply_massing`.** Restores the generated baseline the way the
  viewer's reset does — discarding every earlier op in the sequence, removing
  placed blocks — then lets later ops apply on the fresh baseline. Pinned by
  three checks: `[scale, reset]` → baseline heights; `[scale, reset, scale]` →
  the second scale applies; `[place, reset]` → placed blocks gone.
- **`height_m_new` on `MassingOp`**, now parsed in `_parse_massing_ops`
  (camelCase `heightMNew` accepted), so a `place` op can carry its own height.

### Changed

- `evaluate()`'s `unsupportedOps` is now `[]`; the response instead carries
  `supportedOps` (all six), `placedCount`, a `placedCountNote` explaining the
  count, and an `opsNote` stating the delegation design.

### Tests

- 6 new checks in `test_scenario` (58 total): place adds exactly N envelopes,
  place moves shade, place leaves drainage at exactly 0, reset restores, reset
  clears placed blocks, ops-after-reset apply. Backend total **449**.

## 2026-10-09 — Stage 5c at the response level, and one view of two drainage readings

Three things were overdue on the books: the corridors' NE/SW response no longer
carries direction-blind readings, the two drainage readings are reconciled in one
view, and Stage 7 — built and green in a previous increment — was still marked
"not started" in the status page.

### Added

- **Stage 5c (response half): corridors carry the arrangement.** Each corridor
  now carries `entryEdge` (the edge its wind enters from) and
  `firstObstructionMeanM` (the mean first-hit height from the canonical
  `directional_exposure` march in `design_metrics` — not a second march). On the
  real district: NE enters north-east, first obstruction **30.2 m**; SW enters
  south-west, first obstruction **44.9 m**. The paths are still the same 718
  cells walked in opposite directions — that is exact geometry and stays
  stated — but the two readings are no longer interchangeable. Re-weighting the
  cost surface by the arrangement was deliberately **not** done: any such weight
  is invented, and the limitation text says so. 5 new checks in
  `test_ventilation` (45 total).
- **Gap 8 (one-view half): a `reconciliation` block on
  `GET /api/scenes/{id}/drainage/routed`.** The level-pool headline and the
  routed cascade are two pictures of the same water and are never merged — but
  they were also never shown together. The block puts both readings' volumes
  side by side under the same rainfall, states their one shared identity
  (`runoffInM3 == levelPool.unstoredM3`, verified at **3,170,180.3 m³** — the
  published headline, conserved), names each half's source path, and explains
  why they differ in plain language. Computed from the same grids, not restated,
  so it can never drift from either reading. 6 new checks in `test_hydrology`
  (32 total).

### Fixed (status page)

- **Stage 7 was marked "not started"; it is built, wired and green.**
  `app/services/massing_fit.py`, `GET /api/scenes/{id}/buildings/{id}/fit` and
  the `assess_building_fit` LLM tool shipped in commit `652085d` — the model
  states an objective enum (`reduce_flood | reduce_runoff | reduce_heat |
  balanced`) and constraints, the storage ledger does the arithmetic, 32 tests.
  The stages table now says done, with the design's own one-line description:
  neither half can silently corrupt the other.
- **Backend test count: 404 → 416**, re-verified by running every suite.

## 2026-10-09 — status-page truth pass: Stage 4b anchored, gap 13 collapsed

The repo's own documents had drifted behind its code: `STATUS.md`,
`CHANGELOG.md` and `docs/HEAT_AND_WIND_PROGRESS.md` all said the heat anchor
"does not exist", while `backend/data/reference/heat_anchor.json` was on disk,
verified by `fetch_heat_anchor.py --check`, served by the API, described as
Working in `README.md`, and covered by 11 tests. A status page that understates
its own progress is the same failure as one that overstates it — a reader
cannot trust either. This entry closes the doc drift **and** the one code gap
the status page itself listed as collapsible.

### Fixed (documentation drift)

- **`STATUS.md` claimed the anchor was absent.** `heat_anchor.json` exists,
  `--check` reproduces `rangeC=[26.4, 32.7]`, `anchorSource` is `"hko_station"`
  and `test_design_metrics` runs 90 tests (11 more than the recorded 79) —
  the anchor contract tests (fetch, verify, load, no-data branch, malformed-file
  branch, anchored warning text). Stage 4 is now marked **done** in the stages
  table; gap 11 is re-worded from "not anchored to any observation" to "anchored
  to a nearby station, ~20 km away — a provenance limitation, not a data gap";
  the honesty-constraint entry now describes the *anchored* state instead of
  forbidding anyone from describing it.
- **Backend test count was stale.** 393 → **404** (90 + 54 + 39 + 40 + 39 + 32 +
  28 + 26 + 23 + 18 + 15), re-verified by running every suite.
- **The 9 Oct entry below this one already described the anchor as "nothing yet
  to anchor to"** — correct when written, now annotated as superseded by this
  entry (the mechanism entry stands; the *data* landed later the same day).

### Fixed (code)

- **Gap 13: `Overlay.cell_area_m2` collapsed to one spelling.** The
  `_CellAreaM2` float-subclass-that-is-also-callable bridge is deleted; every
  caller now uses the plain-float attribute form `ov.cell_area_m2` (10 call
  sites across `make_wetgrid.py`, `make_heatmap.py`, `sandbox_model.py`,
  `test_constants.py`). Both raster regeneration runs are **byte-identical**
  before and after (`wetgrid_hsk.npy` md5 `d7cc052a…`, `heatmap_hsk.png` md5
  `306b5be9…`), and all **404** backend tests pass — the collapse changed no
  number, which is what the status page predicted.

## 2026-10-09 — heat SVF + anchoring, wind corridors, drainage routing, compare()

Stages 3–6 of [`docs/research/ch05_way_forward.md`](docs/research/ch05_way_forward.md) §5.2. **393 backend tests**
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
  exists. *(Superseded later the same day: the HKO Lau Fau Shan anchor landed
  and `--check` now reproduces `rangeC=[26.4, 32.7]` — see the entry above.
  Kept as written because it was true when written.)* When the file was absent
  `anchorSource` was `"none"`, `anchorRangeC: null`, and `HEAT_INDEX` stayed
  labelled hand-authored. The schema names the physical quantity (`LST_skin`
  vs `HKO_air`) so the two cannot be conflated later.
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
- **The heat field was not anchored to anything at the time of this entry.** No
  Landsat LST scene and no HKO normal was on disk, so the 0–1 scale had no
  observational reference. *(Superseded later the same day by the HKO Lau Fau
  Shan station anchor — see the entry above.)* The mechanism was built; the
  data landed after.
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
