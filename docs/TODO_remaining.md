# Remaining work — Hack4SDG / T1, branch `feature/intervention-loop`

**Re-scoped 16 Oct 2026 (evening) after the owner's review.** The first 30 lines of this
file were written against a misunderstanding of what a "plan project" is, and that
misunderstanding shaped the whole remaining-work list. It is corrected here, at the top,
where the next reader will see it before the task tables.

---

## The correction that changes everything: what a "plan project" is

A **plan project** is a **government-planned project to be built** — the HSK MTR station is
the one modelled today (`backend/data/projects/mtr-station.json`, a real footprint, a real
GLB, project-supplied effects). It is **not** a user intervention and never was.

It is the **application scenario example**: the thing the sandbox exists to help plan. The
product question is *"how do we use AI to plan these government projects better?"*, and the
**user intervention loop** is the mechanism — a planner describes an intervention in natural
language, the AI proposes a specific sited footprint with provenance, the planner edits and
confirms, and the ledger scores it against the plan project already on the ground.

Two things in the tree got this wrong and are being removed:

1. **The four-project "catalog"** (`backend/data/projects.json`, served by
   `GET /api/scenes/{id}/project-catalog`, rendered by `frontend/src/components/ProjectCatalog.tsx`).
   These are screening-level candidate parcels derived from rasters — `detention-reservoir`,
   `underground-tank`, `green-retrofit`, `blue-green-park`. They are **not** planned projects
   and they are **not** the application scenario. `STATUS.md` already recorded them as a
   placeholder ("four hardcoded projects whose phases are English sentences piped through
   `ai_plan` on every click… do not demo it as a working feature"). **Removing them** — see
   R1 below.
2. **`suggest.py` as a standalone HUD panel** (`SuggestionsPanel.tsx`, ranked candidate sites
   per axis). Its content is good and deterministic; its *placement* is wrong. AI advice
   belongs **under the result card inside the AI chat**, where the conversation that produced
   it is still on screen — see R2.

---

## Rulings for the re-scope (R1–R5)

| # | Ruling |
|---|---|
| **R1** | **Remove the four-project catalog.** Delete `GET /api/scenes/{id}/project-catalog` (the route in `app/routers/scenes.py:98-124`), `backend/app/services/project_catalog.py`, `backend/data/projects.json`, `frontend/src/components/ProjectCatalog.tsx`, `frontend/src/api/catalogApi.ts`, and the tests that exist only to serve it (`tests/test_project_catalog.py`, `frontend/src/api/catalogApi.test.ts`). **Note: `scripts/make_project_catalog.py` does not exist** — the generator is not in this tree, so `data/projects.json` is hand-maintained or came from elsewhere; check `git log` for it before assuming a regeneration step. The planned-project machinery (`data/projects/<id>.json`, `terrain.projects`, the seeded MTR station plan) is **untouched** — that is the scenario, not the thing being removed. |
| **R2** | **Move suggestions into the AI chat, under the result card.** `SuggestionsPanel.tsx` leaves the HUD. Its ranked per-axis candidates become a section rendered beneath the answer card in `ChatPanel.tsx`, fetched for the turn just asked. **The flow gains LLM participation**: the model names which axis it is advising on and why, in its own words, with the deterministic ranking underneath as the evidence it must agree with. A suggestion the model cannot justify is not shown. |
| **R3** | **The NL → intervention path is the priority, and it already exists end-to-end on the server.** Verified live on 16 Oct: `POST /chat` with "add a water reservoir in the north" returns `answer.intent = "intervention"` with a hint; `POST /ai-plan` (apply=false) returns a served proposal (1,876 cells, provenance, constraints). What is missing is the **frontend path being usable and visible** — see D1. |
| **R4** | **Every number keeps its provenance.** The card prints gross *and* net storage side by side and refuses to value soil group, heat preset and tree cover. Nothing in the re-scope may soften that. |
| **R5** | **Honesty invariants are unchanged:** no combined score across heat/wind/drainage; no pass/fail verdict; a `status` + non-empty `warning` on every AI output; provenance (`source`/`date`/`confidence`) on every number. |

---

## D — defects to clear first

| # | Defect | State |
|---|---|---|
| **D1** | **`project-catalog` and `suggestions` are unusable in the browser with console errors.** Measured: both endpoints return **200 with correct data** against the live server (`project-catalog` → the four projects; `suggestions` → `drainage`/`heat`/`runoff`, `status: SITES_FROM_MEASURED_RASTERS`), and `/ai-plan` and `/chat` both work. So this is a **frontend runtime fault, not a backend one**, and it could not be reproduced without a browser (Playwright is not installed in `frontend/node_modules`, so `verify-*` harnesses cannot run). **Needs the actual console error text from the owner** — the fastest path to the truth, and guessing has already cost more than asking would. |
| **D2** | **The parameter card's gross row was permanently `UNAVAILABLE`.** `GET /parameter-card` never passed `served_cells`/`storage_depth_m` to `parameter_card.build()`, so R4's "two numbers side by side" printed one number and a sentence explaining the absence of the other. **FIXED** in this working tree: `parameter_card.served_extent()` derives both from the plan's features using the ledger's own depth-resolution rules, and the test now asserts the card's gross equals an independently computed figure. Verified by breaking the service's multiplication and watching the test fail. |
| **D3** | **`classAreaM2` broke two suites.** `_area_stats` now publishes a *dict*-valued key, and `test_published_scenarios` / `test_building_fit` both did `{k: round(v, 1) for k, v in stats.items()}` → `TypeError: type dict doesn't define __round__`. **FIXED** with `sandbox_model._rounded_stats()`, which rounds scalars and passes structured values through, naming the rule rather than swallowing it. |

---

## Phase 2 — where the code actually is (committed 17 Oct 2026)

Phase 2 (P0-2 parameter card, P0-3 `questions[]`, P0-4 editable assumptions) is
**implemented, committed and green**. The stale `.git/index.lock` that blocked writes is
gone; every task below landed in `01d335c` and `850a972`.

| Task | State |
|---|---|
| **T01** `rejected[]` reaches the wire | **Done.** All three `AIPlanResponse.model_validate` sites in `app/routers/scenes.py` now pass it, hoisted into one local so they cannot drift. The skipping `test_PENDING_proposals_do_not_overlap_each_other` is **deleted** and replaced by two tests that build their own fixtures and fail loudly. Gate 18 → **19**. |
| **T02** parameter card | **Done.** `app/services/parameter_card.py` + `app/schemas/parameter_card.py` + `GET /api/scenes/{id}/parameter-card`; `_area_stats` gains `classAreaM2`; gross/net printed side by side; soil group, heat preset and tree cover refuse a value with a reason; `date` is `null` unless a real acquisition date exists. D2 and D3 fixed on the way. Gate → **21**. |
| **T03** `questions[]` | **Done** in `01d335c`. `ProposalQuestion` + `AnsweredQuestion`; `proposal_constraints.questions_for_kind` / `site_questions` generate them from code beside `parameters_for_kind`, so a question exists only for a parameter the run publishes. `id` is derived from `parameter` alone. An answered depth replaces the class default in `params.storageDepthM`. |
| **T04** editable assumptions + `apply_proposals` validates | **Done** in `01d335c`. `parameters_for_kind(kind, override)` emits `USER_ASSUMPTION` + `defaultValue` + `edited`, and `_accepted_warning` replaces the class-assumption sentence rather than joining it. `apply_proposals` runs `validate_batch` before pinning (M6 closed), and an out-of-range depth is a 422 on both endpoints. `POST /ai-plan/revalidate` is the gate's third call. The disclosure hole described here is **closed and asserted** by `test_sandbox`. |
| **T05** mask unification + docs | **Done** in `850a972`. `siting._rasters` and `ai_plan.load_grounding` both filter with `proposal_constraints.building_mask` — the gate's own union — and `DESIGN_intervention_proposals.md:84` states the union and the whole-shortfall rule for `clippedCells`. |

**Verification already done on this tree (all under the *pinned* dependency set, not the stale
`backend/.venv`):** `test_proposal_contract` **21/21**, every other backend suite green,
`test_overlay_masking` 3/3 under pytest, frontend `tsc` + `lint` + **98/98** + `build` clean,
`contracts/openapi.json` regenerated with pydantic 2.13.5 (233 added lines) and
`test_openapi_contract` 5/5.

---

## The re-scope work, in order

1. **Clear D1** — get the console error text from the owner, fix the frontend runtime fault.
   Nothing else in the UI can be trusted until the panels render.
2. **R1 — remove the four-project catalog** (files listed above). Keep `test_project_catalog`
   only for what it still legitimately covers, or delete it with the generator.
3. **R3 — make the NL → intervention path visible and usable.** The server side is done and
   verified; this is a frontend task: the chat must reach the confirm gate, show the proposal
   with its provenance, and let the planner accept/reject. Then P0-3/P0-4 finish the loop.
4. **R2 — move suggestions under the chat result card**, with the LLM naming the axis it
   advises on and the deterministic ranking as the evidence it must agree with.
5. **T03 → T04 → T05** from Phase 2 above, then P1 and P2 as previously listed.

---

## Deferred defects — do not lose these

**Closed on 17 Oct 2026** (all three verified by a suite, not by reading):

- ~~`siting.py:150-152` and `ai_plan.load_grounding` still use the loose bounding-box mask.~~
  Both now filter with `proposal_constraints.building_mask(ov)` — the gate's own
  union — so the pool and the gate cannot disagree about the same building.
  Measured before the swap: no site moved more than 16 cells (the largest basin
  1,878 → 1,876; the largest paving site 3,596 → 3,580) and the smallest (83
  cells) was unchanged, well above the 40-cell floor, so no site vanished.
- ~~`clip_window` under-reports `clippedCells`~~ — it now reports `window − served`,
  where `served` is the contour actually handed back, so a fragment stranded by
  `contour_ring` returning the largest ring is counted with the buildings.
- ~~`docs/DESIGN_intervention_proposals.md:84` describes `building_mask` as
  `building_footprint_mask(...)`~~ — corrected, with the two-mask union and the
  ~23-cell difference stated and the whole-shortfall rule for `clippedCells`.

**Still open:**

- **The two rainfall defaults differ** (`RunRequest.rainfall_mm = 50.0` vs
  `DEFAULT_RAINFALL_MM = 450.0`). Deliberately not unified: it would move published run
  numbers and deserves its own decision.
- **P2-8** `curveNumber` is reported as *unread*; a per-intervention override would move
  published numbers and needs its own decision.

## DNR — deliberately not started

Water-pour animation · PostGIS/object store · CFD, comfort indices, kappa validation, InVEST,
typhoon surge · procedural massing of the existing district · re-opening the dormant `place`
op · model-authored polygons / free-hand ring drawing.
