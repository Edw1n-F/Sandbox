# Architecture

Design decisions and the reasoning behind them. The
[README](../README.md) has the condensed version and the local setup guide; this
document is for whoever changes something and needs to know what would break.

---

## The shape of it

A 3D planning sandbox. You load a study area, put a development plan on it, and
see where water collects and where heat is trapped.

The interesting constraint is not technical. It is that **the team has to be
able to run this on a laptop, with no Docker, no database and no API keys, on
the day.** Almost every decision below follows from that.

```
┌─ open data (fetch_dem.py, fetch_landcover.py) ──────────────┐
│  Open-Meteo elevation ~90 m         ESA WorldCover 10 m    │
│  verified against control points     CC BY 4.0, COG window │
└──────────────────────────┬─────────────────────────────────┘
                           │ writes
                    backend/data/*.npy, *.png, *.json
                           │ read by
┌──────────────────────────▼─────────────────────────────────┐
│ scripts/  _overlay_common  build_grid  SCS-CN  level-pool   │
│           make_wetgrid   make_heatmap                      │
│           each attaches its output to the scene            │
└──────────────────────────┬─────────────────────────────────┘
                           │ file store (STORE_MODE=file)
┌──────────────────────────▼─────────────────────────────────┐
│ app/routers   HTTP only: parse, delegate, serialise        │
│ app/schemas   the wire contract + RowModel translation     │
│ app/services  cascade, bbox repair, URL resolution         │
│ app/db.py     the ONLY module that knows storage is files  │
└──────────────────────────┬─────────────────────────────────┘
                           │ /api, /static
┌──────────────────────────▼─────────────────────────────────┐
│ frontend  api/ (typed fetch)  state/ (Zustand)             │
│           view/ (Cesium)  components/ (MUI panel)          │
│           no filesystem, no DB, no Cesium above this line   │
└────────────────────────────────────────────────────────────┘
```

Each arrow is an HTTP call or a file write. The frontend never touches the
filesystem. The backend never knows what Cesium is.

---

## Decision 1 — Store mode is configuration, not architecture

**Decision.** `STORE_MODE=file` (JSON on disk) today, `postgis` is the target.

**Why.** The host has no Docker, no Postgres, no Homebrew. The spec's target is
PostGIS + MinIO.

**How it's kept honest.** The storage boundary is drawn at exactly **two
modules**: `app/db.py` and `app/services/scene_service.py`. No route, no schema,
no frontend code knows which store is active. The 11 contract tests run against
file mode today and run unchanged against PostGIS when it exists.

**Why it matters.** If the routes knew about storage, "temporarily" would have
become permanent by the third endpoint. The seam is what makes the PostGIS
migration a swap rather than a rewrite.

---

## Decision 2 — Degrees on the wire, metres in the maths

**Decision.** Every coordinate crossing the API is EPSG:4326. Exactly one
function converts degrees to metres: `build_grid()` in
`app/services/geo_import.py`.

**Why.** The spec mandates EPSG:4326, and Cesium and GeoJSON both speak it. But
degrees are not a unit of length, and summing rainfall over "a degree" is
meaningless.

`build_grid()` uses a local equirectangular approximation about the extent
centre. At a 3 km extent the error against HK1980 Grid is far below the 5 m cell
size. Every raster script calls it; nothing else converts units.

**Where it will need to change.** Volume reporting should move to HK1980 Grid
(EPSG:2326). The comment at the choke point says so.

---

## Decision 3 — `RowModel`: one translation point for the wire

**Decision.** Storage uses `id` (the spec's DDL). The API uses `sceneId` /
`planId`. The translation lives in `RowModel.from_row`.

**Why an alias and not a rename.** The DDL is part of the specification.
Renaming the storage column to satisfy the API would have quietly diverged from
it.

**Why one function.** The alternative — `@computed_field`, or `Field(alias=...)`
scattered across models — works right up until something bypasses it, and then
storage IDs leak onto the wire and nobody notices. `extra="forbid"` on the
response models is what turned that from a silent leak into a startup error
during the build.

---

## Decision 4 — The spec contradicts itself in six places

`技术文档` §3 and §4 disagree. Each conflict is resolved in **one named place**
rather than by picking a reading and hoping:

| Conflict | Resolution | Where |
|---|---|---|
| §3.2.2 `bbox` example has 2 coordinates — not a valid ring | Normalised to a closed ring on ingress | `schemas/scene.py`, `services/scene_service.py` |
| §4.2.1 `defaultCamera` has `lon`/`lat`; §3.2.1 does not | Model is the **union**; `lon`/`lat` default to the scene centre | `schemas/scene.py` |
| DB `id` vs API `sceneId`/`planId` | Alias via `RowModel` | `schemas/scene.py` |
| `plan_type` CHECK allows 5 values; nothing creates `economic`/`environmental` | Left legal and unreachable, noted in the model | `models/plan.py` |
| Both docs reference a `migrations/` directory that does not exist | **Still missing.** Unnecessary in file mode. Flagged, not faked | — |
| §3.5 mandates both a FastAPI `detail` array and `{"error": {...}}` | **Both emitted.** Frontend has one error branch | `main.py` |

The pattern: where the spec is ambiguous, the code is permissive on input and
strict on output, and the decision is written down where the next reader looks.

---

## Decision 5 — A data source that cannot prove itself is rejected

**Decision.** `fetch_dem.py` asserts Everest ≈ 8,000–9,500 m and the open ocean
≈ 0 m **before it writes a single file**.

**Why.** The first source tried, AWS `elevation-tiles-prod`, returns HTTP 200
with plausible-looking PNGs whose pixels are not terrarium-encoded. Everest
decoded to ~1,000,000 m. It would have produced a beautiful, silent, wrong
landscape.

**The general rule.** Open data is not trustworthy by virtue of being open. The
test is cheap: two control points, a known answer, abort on mismatch. Detail in
[DATA_SOURCES.md](DATA_SOURCES.md#rejected-aws-elevation-tiles-prod-terrarium).

A second instance of the same principle: the first land-cover read filled 21 % of
the study area with a default class because it read only one of two tiles.
Nodata quietly becoming a real class is the same failure — confident, wrong, and
invisible in a picture.

A third: **resolution was measured rather than assumed.** The DEM was requested
on a 1 arc-second lattice, which returned 78 × 110 cells containing only
28 × 37 distinct values — three-fold oversampling, because the API serves a ~90 m
global model. The array was deduplicated back to its true posting, and every
document now says ~90 m. Sampling finer would have duplicated the data again,
not resolved it.

---

## Decision 6 — Continuous fields are images, not polygons

**Decision.** Wet grid and heat overlays are `SingleTileImageryProvider` rasters.
Extrusion is reserved for discrete features (projects, interventions).

**Why.** One georeferenced 256×256 PNG is ~16 KB. The same field as polygons
would be thousands of features, tens of MB, and a different frame rate.

**The async trap.** `SingleTileImageryProvider` starts with no image. Adding it
to a scene in that state makes Cesium's render loop throw a bare
`DeveloperError` with an empty stack, which takes the React tree down with it.
Hence `SingleTileImageryProvider.fromUrl()`, which resolves only once the image
is ready. See the `createOverlayLayer` docstring.

---

## Decision 7 — Real terrain without a token or a tile server

**Decision.** Elevation is published as a mapbox terrain-RGB raster and served
through `CustomHeightmapTerrainProvider`.

**Why.** Cesium's stock `CesiumTerrainProvider` wants quantized-mesh from a
tiling service. The Lands Dept quantized-mesh tiles need approval we do not
have, and Cesium ion needs a token we deliberately do not use. The study area is
2.4 × 3.3 km — a single 1.6 KB image, not a tile pyramid.

`CustomHeightmapTerrainProvider` is a first-class Cesium API, not a workaround.
Returning `undefined` from its callback is what makes tiles outside the raster
fall back to their parent, so flying away from the study area shows flat ground
instead of a stretched slab.

---

## Decision 8 — Every viewer touch is guarded by `isLive()`

**Decision.** One predicate, used everywhere the Cesium `Viewer` is touched.

**Why.** React StrictMode mounts, unmounts and remounts every effect in
development. The create-effect's cleanup calls `viewer.destroy()`; the *other*
effects' cleanups then run against the destroyed instance, and
`viewer.imageryLayers` dereferences an internal `destroy()` already released.

**Also learned the hard way:** with `requestRenderMode: true`, a layer whose
texture finishes loading asynchronously never paints until something asks for a
frame. Hence `scene.requestRender()` after each overlay is added.

---

## Decision 9 — Honesty is a feature, not a disclaimer

**Decision.**

- Every raster writes a `.json` sidecar listing every constant, assumption and
  source, *including which data source it actually used*.
- The viewer reports a failed overlay in the panel.
- The UI states the prototype limitation on screen.
- The provenance string is **derived at runtime**, never hard-coded.

**Why the last one matters.** The provenance string *was* hard-coded. When real
ESA WorldCover data replaced the synthetic land class, the sidecar kept claiming
`SYNTHETIC PLACEHOLDER` — a stale label contradicting the data beside it. A
label that can go stale without anyone noticing is worse than no label, so the
scripts now report `ov.landcover_source` / `ov.terrain_source` from the object
that produced the data.

**Why any of it.** Three separate bugs presented as "the toggle does nothing" —
an unregistered Vite path alias, a StrictMode-destroyed viewer, and an image
that failed to decode. None was diagnosable from the code. Making failure
visible is what turned a guess into a diagnosis.

---

## Decision 10 — A clean `tsc` is not evidence the frontend works

**Decision.** Frontend changes are verified by driving a real headless-Chromium
render and asserting zero console errors, not by the compiler.

**Why.** Four runtime bugs passed `tsc --noEmit` cleanly:

1. `tsconfig.json` declared `paths: { "@/*": ["src/*"] }` but `vite.config.ts`
   never registered a matching `resolve.alias`. TypeScript resolved the imports;
   Vite did not. Five modules failed at runtime — while the type checker was
   green.
2. The StrictMode-destroyed viewer (Decision 8).
3. `/static` missing from the Vite dev proxy, so the overlay PNG request returned
   `index.html`.
4. **Click-to-select never worked, while the panel said it did.**
   `GeoJsonDataSource` copies `properties.title` or `properties.name` onto
   `entity.name` and nothing else; these features carry `properties.id`, which
   Cesium does not copy anywhere. So `picked.id.name` was `undefined`, every click
   resolved to "no building", and the instruction "click a building to select it"
   was fiction. 915 clicks across the district selected nothing. `tsc` cannot see
   this, and neither could a unit test, because the bug is a disagreement between
   two libraries about a naming convention.

The compiler validates types. It does not validate module resolution, lifecycle
ordering, third-party naming conventions, or that a URL serves the bytes you
expect.

**A note on writing the browser checks.** Three of them were wrong before the
product was, and each produced a confident false result:

- `readPixels` and `createImageBitmap` on Cesium's canvas return nothing, because
  the WebGL context does not preserve its drawing buffer. Pixels have to be read
  from a saved PNG, decoded as an ordinary image.
- The canvas is full-width and the panel floats over its left edge, so the
  leftmost ~270 px of every screenshot are UI. Clicks there hit the panel — and
  MUI's pale-blue info `Alert` matches a loose "green and blue above red" hue
  rule, so a check for "did the scene draw the new blocks" was partly measuring
  the alert box that appears when a plan is applied.
- Synthetic `MouseEvent`s never select anything, because Cesium picks with
  `event.offsetX/offsetY` and a constructed event reports 0.

The rule that came out of it: assert on what the *viewer* reports, never on what
the backend asked for.

---

## Decision 11 — The model selects sites; it never emits coordinates

**Decision.** The AI module shows the model a menu of candidate sites, each with
an opaque id, and asks it to choose. Geometry is attached afterwards from the
real analysis.

**Why.** Ask an LLM for "a reservoir in this development area" and it returns
confident, plausible, invented coordinates. In a flood-planning tool that is
worse than no answer, because it is indistinguishable from a site survey. Every
generated feature records the raster it came from, so the output is auditable —
but that only works if the geometry is real to begin with.

**How it holds up.** It is a *selection* problem rather than a *generation*
problem, which is what language models are reliable at, and it makes hallucinated
geometry structurally impossible rather than filtered. Two tests defend it: one
asserts the catalogue contains no coordinates at all (so the guarantee cannot
quietly erode if a field is added later), and one asserts an invented site id is
dropped rather than guessed at. A third asserts the id's pool wins over the
model's own type field, so a park cannot be relabelled a reservoir.

**Where the spec already anticipated it.** `plan.nl_instruction`, `source='ai'`,
ADR-030's draft rule, and §3.3.2's "用户绘制与 AI 方案落地共用同一套管线". This
fills the slot the spec reserved rather than inventing a parallel design.

## Decision 12 — Geometry, not a mesh, for the city model

**Decision.** The 2,639-building massing model is Cesium entities (per-feature
`Property` bags), extruded, not a glTF mesh.

**Why.** A mesh is one draw call and would be faster. It is also *opaque*: you
cannot change a building's height inside a glTF without regenerating and
re-uploading it. The user's central action is changing the model, so the
representation is chosen for editability, and the performance trade is accepted
knowingly. A height edit becomes a `ConstantProperty` assignment.

**What the footprints are.** Procedural, from the ESA WorldCover `paved` class.
OSM was tried and is unreachable here (Overpass 504/000 across five mirrors,
Microsoft GlobalML now 409, Geofabrik extract too large and its parser will not
build). Land use is real; footprints are not surveyed and heights are an assumed
distribution. `citymodel_meta.json` says so and the panel shows a *provisional*
chip, because a skyline that looks measured is the failure mode here.

## Decision 13 — A scope is resolved in code, or the operation does not happen

**Decision.** Every massing operation carries a **scope**: a polygon resolved by
the backend from one of ten named regions of the locked bbox, or from a grounded
catalogue site id, or nothing at all (the whole study area). An unrecognised
scope produces **no feature**.

**Why.** This is the second-worst failure mode in the project after a wrong
number, and the first version of the massing tool had it in production. "Make the
western blocks 30% taller" was parsed into `scale_all` and the region word was
discarded, so the entire district was scaled and the panel reported success. The
screen filled with a plausible, entirely wrong skyline. No test caught it,
because the feature returned was *valid*: right type, right multiplier, real
geometry.

Regions are a **fraction of a bbox we already own** rather than a list of
polygons, which is what makes accepting a region name from a language model safe:
there is nothing to hallucinate, only a box to divide.

The important half is the fallback. An unknown scope falls back to *nothing*, not
to "everything". Falling back to "all" would reproduce the original bug with an
extra step, and would do it quietly.

**How it is checked.** The browser test asserts what the *viewer* reports —
"1,232 of 2,639 buildings" — because a 3D view looks identical whether an
operation reached 400 buildings or all of them. `cityModel.applyMassingOps()`
returns a result per operation, and the panel prints it.

## Decision 14 — Placement generates its geometry; the model supplies the parameters

**Decision.** `place` is the one operation that creates geometry, and the
footprints are generated by `placement_footprints()` from the ESA WorldCover
`paved` mask, using the same `make_citymodel.build_blocks` the city model itself
uses. Cells already occupied by an existing envelope are excluded. The model
supplies a count, a height and a scope.

**Why.** The alternative — letting the model emit coordinates — is exactly what
Decision 11 forbids, and for placement it would be worse: a tower needs a
footprint, not a point. Reusing the generator rather than writing a second block
definition means the placed blocks and the generated blocks cannot drift apart in
pitch, minimum size or built-up threshold.

**The clip.** A candidate block only has to be *mostly* built up, so one near a
scope edge overhangs it by up to a block width. Every footprint is clipped to the
intersection with its scope, because the scope is a promise about where the edit
lands. Both shapes are axis-aligned rectangles by construction, so the
intersection is arithmetic; a genuinely irregular scope would need
Sutherland–Hodgman, and that assumption is stated at the function rather than
assumed silently.

**What it is not.** A scenario, not a scheme. Placed blocks sit on real land
cover at a height the model asked for in a region the model named. The panel says
`added by the plan` and the README says provisional, because a coloured block on
a map is otherwise indistinguishable from a design decision.

## Decision 15 — Verify the reference implementation, then reimplement if it will not install

**Decision.** `richdem` and `hydrotools` are the standard open-source tools for
terrain conditioning. Both were installed; neither is usable. The algorithms are
implemented directly in numpy, citing the papers.

**Why.** `richdem` needs GDAL headers to build and no wheel exists for this
interpreter; `hydrotools` imports but gates every hydrology class behind it, so
`FlowAccumulator`, `FlowDirection` and `TopologicalStreamOrder` are all absent.
A dependency that cannot be installed is a dependency that fails on someone
else's machine too, so a dependency that cannot be installed is a dependency
that fails on someone else's machine too.

**What it bought.** Priority-Flood depression filling (Barnes et al. 2014) and
D8 flow accumulation, both tested against known-answer cases. It is not a
reimplementation for its own sake: the shipped DEM has **10.3% of its cells in
closed depressions**, and without filling them the level-pool solver floods pits
to arbitrary depth. That was the largest single weakness in the wet grid.

## Decision 16 — The chat explains a run; it never computes one

**Decision.** `app/agent/` answers questions about a run that **already
happened**. The run result is handed to the model as a FACTS block, verbatim.
The reply is a card, and every number in it is checked against those facts by
code before it is shown.

**Why.** Two temptations were refused.

The first is recomputation. It is trivial to re-run the simulation inside a
question handler, and it is wrong twice over: a fast reply becomes a 0.7 s
simulation, and — worse — the explanation can then disagree with the numbers the
planner is looking at. An answer that contradicts the panel is not an
explanation of the panel.

The second is asking the model to check its own work. "Are your numbers
grounded?" is not a check, it is a second opinion with the same failure mode. So
`grounding.check_card` does it in code, at any scale or rounding, and a card
that fails is sent back **once** with the offending figures named. A second
failure is reported on the card rather than retried: a planner shown an
ungrounded number with a note on it is better served than one watching a
spinner.

**Why a card and not a paragraph.** The same fields are always present, so the
UI renders them in the same place every time and the reader learns where to
look. A paragraph can hide a missing cause; a card with `cause: null` shows one,
which is the honest outcome when the numbers do not support one.

**Structured output is forced, not requested.** A schema in the prompt is a
suggestion. A tool the model must call (Anthropic `tool_choice`) or a
`response_format` (OpenAI-compatible) is a guarantee, and the difference matters
because the card is parsed by code and rendered by field. Where neither is
available — a stream, or a free model that 400s on `response_format` — the
contract is also in the prompt and the outermost `{…}` is taken, so a model that
wraps its JSON in a sentence still parses.

**Where the model is still trusted.** Site selection, not coordinates — that is
Decision 11, and the chat inherits it unchanged. The chat has no tools of its
own: it cannot write geometry, start a run, or change a plan. Interventions
typed into the chat are routed to the AI planner, which is the module that owns
geometry (and ADR-030's draft rule).

**How it holds up.** With no provider configured the identical card is assembled
by code from the run's own figures and labelled `source: "template"`. That is
not a fallback to be hidden: every number in it is one the run actually
produced, which is more than a hallucinated model answer can claim. The stepper
always emits three finished steps, so the template path is visible rather than
indistinguishable.

## Deliberately not used

| Not used | Why |
|---|---|
| Docker | Cannot be provisioned; the demo must run natively |
| PostgreSQL / PostGIS | The documented target. `STORE_MODE=file` covers the same contract (Decision 1) |
| MinIO | Rasters are small enough for the file store, and `/static` serves them |
| `alembic` / `migrations/` | Referenced by both spec documents and **still missing**. Unnecessary in file mode — flagged, not faked |
| GDAL | `rasterio`'s wheels bundle it, and only one script needs it |
| A physics engine | Out of scope for this phase; named in the task plan |
| Cesium Ion | Requires a token; the app runs with zero credentials instead |
| A paid elevation API | Open-Meteo and WorldCover are open, key-less, and verified |

---

## Where the seams are

If you are changing something, these are the places that must move together:

| Change | Touches |
|---|---|
| New endpoint | `routers/` + `schemas/` + a contract test |
| New raster field | `scripts/` + a field in the terrain/overlay response + a UI toggle |
| Swapping to PostGIS | `app/db.py` + `app/services/scene_service.py` **only** |
| A different study area | `data/scene_hsk.json`, then re-run all four scripts |
| A better DEM | Drop in `dem_hsk.npy` + set `terrain_source`. Nothing else |
| Real land cover | Drop in `landclass_hsk.npy`. Nothing else |
| A new front-end layer | `view/` for rendering + a control in `components/` for the switch |
| A new massing operation | `MASSING_TOOL` + `parse_massing_intent` + `_massing_feature` + `applyMassingOps`, **and a test that an unrecognised scope is dropped rather than widened** |
| A new field on the answer card | `agent/answer.py` (model **and** `ANSWER_SCHEMA`) + `CardView` in `ChatPanel.tsx` + anything `grounding.check_card` should now read |
| A new stream event | `agent/chat_service.py` + the `Step`/event union in `api/chatApi.ts` + `ChatPanel.tsx`. The stream always ends with `done` |
| A new LLM provider | `agent/llm.py` only — `complete_json` and `stream_text` are the whole surface |

---

## Testing strategy

Two suites, each guarding a different class of mistake.

**`tests/test_contract.py` (11 tests)** — the API is a contract with an external
consumer, so the contract is asserted: every endpoint, the cascade delete, the
`sceneId` alias, bbox ring closure, and that the error envelope carries both
required shapes.

**`tests/test_constants.py` (18 tests)** — guards the two constants the source
documents say *fail silently*: the SCS-CN form and the mCD/mPD datum offset.
Neither raises an error when wrong; both produce a plausible number. One of them
was caught in practice — `S = 25400/CN − 2540` returns negative retention above
CN ≈ 91, which made `Q`'s denominator negative and produced a first run
reporting **5.4 × 10¹⁸ m³** of runoff while rendering a plausible image. Also
covers the ADR-015 double-counting partition and ADR-017 monotonicity.

Three of them guard the raster and feature pipeline against a specific class of
mistake — a *render* being mistaken for *data*, and a derived feature whose
stated size disagrees with its own geometry:

- the depth field must reach well beyond the PNG's saturation cap, because the
  render clips at 0.30 m while the wet area averages ~1.5 m;
- wherever the PNG is unsaturated it must agree with the depth field, or the two
  came from different runs;
- a seeded intervention's polygon must cover the area its `params` claims
  (this caught a feature that said 155.4 ha and was 0.003 ha of geometry);
- seeded interventions must carry a `params.basis` and be `status="draft"`.

It also guards the terrain pipeline: the committed `dem_terrain_rgb.png` must
decode back to `dem_hsk.npy` within terrain-RGB's 0.1 m quantum — because the
Python encoder and the browser decoder are two implementations of one formula in
two languages, and if they ever drift the terrain renders at the wrong height
with nothing erroring — and the DEM's height range must be plausible for a
coastal Hong Kong floodplain.

**`tests/test_ai_plan.py` (30 tests)** — drives the LLM path through an injected
transport that replays a recorded Messages API response, so the request shape,
the tool-use parsing and the grounding constraint are all exercised with no key
and no network. A test that only runs when a credential happens to be present is
not a test.

Half of them exist because a local model behaves differently from a large one,
and both differences produced **silently wrong** results rather than errors:

- `qwen2.5:3b-instruct` answers `modify_massing` with the operation *itself*
  (`{"op": "place", "count": 3, "scope": "east"}`), dropping the schema's
  one-element `edits` array. Reading `args["edits"]` found nothing, so a working
  model returning a correct answer produced zero features and a green tick.
- When the JSON-schema retry is enforced for a massing instruction, the same model
  fills in the *intervention* shape anyway — `selections` with real, valid
  catalogue ids. The parser believed it, and "add three 60 m towers in the east"
  produced three water reservoirs: a complete, plausible, wrong plan for a
  question nobody asked. The fallback now reads only the routed tool's key.

The rest guard the scope and placement guarantees: an unrecognised scope produces
no feature rather than a global one; a hostile response cannot place a block by
coordinate; placed footprints lie inside their scope, sit on WorldCover `paved`
cells, and do not duplicate an existing envelope.

**The browser check** — a headless-Chromium script driving the real app against a
real local Ollama, asserting what the *viewer* reports. It is the only layer that
can see three of the bugs above, and it caught two more: click-to-select never
worked (Decision 10), and the AI's scope was discarded (Decision 13).

**`scripts/preflight.py`** — environment readiness, exiting non-zero when the
project cannot run. It exists because the most common failure here is an
*environment* failure that presents as a code failure: a stale 3.10 `.venv`
without numpy raises `ModuleNotFoundError` from inside a raster script, which
reads as "the script is broken" rather than "you are on the wrong interpreter".
It also catches the reverse trap — `STATIC_BASE_URL` disagreeing with the
`/static` mount — and prints the `VITE_SCENE_ID` the frontend needs.

**Frontend** — `npx tsc --noEmit`, plus a headless-Chromium render asserting zero
console errors (Decision 10). The render is the test that would have caught all
three runtime bugs.

---

## Related documents

- [`README.md`](../README.md) — setup guide, tech stack, troubleshooting
- [`../STATUS.md`](../STATUS.md) — what works, measured
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — provenance, licences, rejected sources
- [`research/`](research/README.md) — the five cited research chapters
- [`../CLAUDE.md`](../CLAUDE.md) — repo conventions
