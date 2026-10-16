# Remaining work — Hack4SDG / T1, branch `feature/intervention-loop`

Audited 16 Oct 2026 against the tree after merging main (`611ef13`) and landing the
intervention-loop work. Every item says what exists today and what is missing, so the
two are never confused.

Follow-up audit **17 Oct 2026**: the push unblocked (branch `feature/intervention-loop`
is at `2ad7176` on `origin`, tree clean). F1/F2/F3 are closed. P0-1 is **wired** — the
constraint engine is now called from `generate_proposals` and `rejected[]` flows through
`/ai-plan`; `test_proposal_contract` rose 14 → **15/18**. The three remaining reds are now
the only thing between P0-1 and green, and they are characterized below. Everything else
(P0-2/3/4, P1, P2) is unchanged from the 16 Oct audit.

Audit **16 Oct 2026 (later) — P0-1 is CLOSED at `cd7a87a`, 18/18.** All three reds landed.
Their recorded root causes were **wrong** and are corrected below. New state:

- **F4 — `contracts/openapi.json` was stale against the pinned pydantic. FIXED here.**
  The committed contract had been generated under pydantic 2.9.2; `requirements.txt` pins
  **2.13.5**, which renders `additionalProperties: true` for every free-form
  `dict[str, Any]` field. Local runs passed because `backend/.venv` is stale at 2.9.2;
  the `contract` CI job regenerates with the pinned version and saw 47 differing lines.
  Regenerated with the pinned version — the diff is **only** those `additionalProperties`
  keys, which is how you tell a version drift from a real schema change. **Lesson recorded
  in `requirements.txt`: generate the contract with the pinned versions, never with whatever
  the local interpreter carries, and check `pip show pydantic` before trusting a green run.**
- **P0-2 / P0-3 / P0-4 remain open** — designed in `docs/PHASE2_DESIGN.md` (rulings R1–R23),
  not yet implemented. Note R19: `apply_proposals` must run `validate_batch` before
  `create_plan`, because a client can already write `params.storageDepthM` and have the
  ledger score it while the published provenance still claims the class default.
- **`rejected[]` still does not reach the wire** (`routers/scenes.py:289-302`), so
  `test_PENDING_proposals_do_not_overlap_each_other` skips instead of testing. Phase 2, R21.
- **`siting.py:150-152` still uses the loose bounding-box mask**, so the catalog/suggester
  path can still site on a real polygon-exact building cell. `load_grounding` is unified as
  of Phase 2 T05; `siting.py` is deferred to Phase 3 — **do not let this be lost.**
- **`clip_window` under-reports `clippedCells` on the coordinate path** (it contours only the
  largest ring, so a stranded fragment is not counted). Same class as the site-path defect QA
  found and fixed in Phase 1, different path. Needs its own ticket.

Legend: **P0** = pitch-critical (blocks the loop on screen) · **P1** = makes an
intervention honest and shaped · **P2** = the loop's edges · **FIX** = must land before
any push · **DNR** = deliberately not started, do not pick up by reflex.

---

## FIX — before the next push

| # | Item | State |
|---|---|---|
| F1 | **`contracts/intent_vocabulary.json` lost its `version` key** when the fixture was regenerated (rewritten with only five vocabulary keys). **FIXED**: regenerated with `version` + `note`; `backend/tests/test_intent_routing.py::test_the_fixture_matches_the_backend_vocabulary` passes; the TS mirror (`frontend/src/api/intentVocabulary.ts`) carries `version` + all six vocabulary keys; frontend is 98/98 again. | done |
| F2 | **`git push` was blocked by the network** (`Empty reply from server` / `CONNECT tunnel failed, response 502`). **RESOLVED**: `feature/intervention-loop` is pushed to `origin` at `2ad7176` (HEAD == origin/feature/intervention-loop; working tree clean). | done |
| F3 | **Other integration workflows.** Only `.github/workflows/ci.yml` exists; it carries `pull-requests: read` so `dorny/paths-filter` can list PR files. No other workflows to grant. | done |

---

## P0 — close the loop visibly and deterministically

| # | Item | What exists | What is missing |
|---|---|---|---|
| P0-1 | **Wire the constraint engine into proposal generation** (the todo's "add coordinate selection and size constraints"). | `proposal_constraints.py` built+verified (39→`TOO_SMALL`, 40→accept, 4,329→accept, 4,330→`OVER_SINGLE_CAP`; served==requested pre-clip; `validate_batch` owns `claimed \|= cells`). **Now wired**: `generate_proposals` calls `validate_batch` and returns `rejected[]` through `/ai-plan` (`AIPlanResponse.rejected`, `ai_plan.py:2154/2171/2182/2201`); a paving site at 39.4% is now rejected `OVER_SINGLE_CAP` instead of silently proposed; suite rose 14→15/18. | **Still owed (2 minor):** (a) the constraint echo in `/run` — `run_service.py` has no `rejected`/`constraint` handling, so a run can't show *why* a staged proposal was rejected; (b) the optional `placement` block on `CHOOSE_TOOL.selections[]` (`intent.py` has no such block). **The 3 reds are CLOSED at `cd7a87a` (18/18)** — see the acceptance section below. Still owed: the two minor items at (a) and (b). |
| P0-2 | **§2 parameter card** (open item 5). | The ledger reads `params.storageDepthM` and validates 0.05–10 m; proposals carry a `parameters` block (value/unit/status/source). | No `parameter_card.py` and no endpoint. The brief's card is specific: total site area; pond/water/wetland/tank retained *or removed*; green/tree retained or removed; new impervious share; bare ground created; explicit storage added; storage = area × depth; rainfall; heat preset; outlet level; soil group — **each with source, date and confidence**. Those numbers are still scattered across the ledger and stats dict. |
| P0-3 | **§6 the AI asks for missing assumptions.** | `InterventionProposal` carries `rationale` and `basis`. | No `questions[]` anywhere. Today the AI silently assumes a default (location, footprint, surface replaced, usable storage depth, drainage/overflow, tree cover) instead of asking. |
| P0-4 | **§6 review, edit and confirm.** | The confirm gate exists (accept/reject proposals). | A planner cannot *edit* an assumption. "Usable storage depth" — the field the brief calls out — cannot be set by hand. |

### P0-1 acceptance — the 3 `test_proposal_contract` reds — **CLOSED at `cd7a87a`, 18/18**

All three landed in `cd7a87a`. **The root causes originally recorded here were wrong**; the
corrected diagnosis, measured against the live grid, is what the code comments now record.

**1. `test_PENDING_proposals_avoid_existing_buildings`** — the recorded cause (a
`water_reservoir` overlapping 2 building cells because `contour_rings` closes the ring over
a 1-cell gap) was **WRONG**. Every candidate pool has **zero** overlap against the authority
the pools already subtract, so there was no ring-closure leak. The real cause: **one dataset,
two rasterisers.** `building_footprint_mask` routes through `rasterise_height`, which stamps
a bounding **rectangle** per footprint (7,789 cells); the true footprint **polygon** is 3,222
cells. That approximation misses **23** real building cells and over-claims **4,590** phantom
ones. Fixed by unioning both rasters in `proposal_constraints.building_mask` (7,812 cells) —
a union, so the gate is never *looser* than the pool filter that produced the candidate —
plus a site-path clip guard that subtracts buildings and re-contours the survivors. The guard
is **load-bearing, not hardening**: without it the stricter gate empties the proposal list
and this test passes vacuously.

**2. `test_every_proposal_kind_has_a_defined_ledger_effect`** — `drainage` and `road` are in
`POOL_FOR_KIND` with no `INTERVENTION_EFFECTS` entry, so the ledger skipped them with "no
land-cover effect is defined", which reads as an oversight. **Fixed by disclosure, not
removal:** both take the `drainage_alignment` shape `{"to": None, "why": ...}`, so they are
skipped *with the reason*. Neither borrows `permeable_paving`'s CN 72 — inventing a runoff
coefficient would claim a number this model cannot compute. `POOL_FOR_KIND` and
`STYLE_FOR_KIND` unchanged, so no legend row is orphaned. `NO_GRID_EFFECT_KINDS` and the
line-280 test needed **no** edit. This also closes P2-3.

**3. `test_every_proposal_kind_round_trips_through_ai_plan`** — `permeable_paving` emitted
nothing because its only candidate was 6,821 cells against the 4,329 single cap. **Fixed** by
`ai_plan._split_under_cap`: row-coherent banding, then 4-connected components within each
band, with `[:3]` applied to the **emitted sub-sites** rather than the pre-split components.
Naive row-major chunking was measured and rejected — it slices the component into slivers
whose contour closes across the gaps. The split is **not** optional because of the authority
change: with the corrected mask the largest paved component grows 6,821 → 11,224 cells,
breaching the cap by more.

Two defects QA found in review were fixed before the commit: a site-path clip that dropped a
bisected fragment while under-reporting `clippedCells`, and a `bandIndex` label describing
one sub-site but not its sibling. The same label defect pre-existed in `green_sites` (all
three "largest", actual ranks 1/2/3 — now fixed) and worse in `reservoir_sites` ("deepest"
while the pool is ordered by area — now fixed too).

---

## P1 — honest and shaped

| # | Item | What exists | What is missing |
|---|---|---|---|
| P1-1 | **§5 preliminary / user-defined labelling.** | Proposals carry `params.status: draft_pending_human_review` and `source: "ai"` on the wire; the PRD's honesty section requires both on screen. | Nothing in the *results* distinguishes a user-authored intervention from an AI-proposed one, so a preliminary, assumption-dependent number can read as measured. |
| P1-2 | **§5 proposal fields: short name + evidence status.** | Type, location, rationale, basis, area (`size.areaHa`), depth (`parameters.storageDepthM`) are all present. | No short human **name** for a proposal, and no **distinct evidence status** (there is a `basis` string, not a status). |
| P1-3 | **`remove` op + undo** (open items 4, 11). | `REMOVAL_VERBS` are recognised by the classifier (`agent/intent.py`); `DELETE /api/scenes/{id}/interventions/{id}` exists on main; `pins.unpin` takes a feature out of a run. | No `remove` op in the NL parser, no undo stack. A planner can add but not take away, and an unpin is not a removal. |
| P1-4 | **The second real project** (Stage 3). | `backend/data/projects/mtr-station.json`, `scripts/seed_projects.py`, and main's `terrain.projects` machinery — adding a second project is now a data edit, not a code change. | The second project itself does not exist. STATUS: "What is still missing is the **second real project**." |

---

## P2 — the loop's edges

| # | Item | State |
|---|---|---|
| P2-1 | **NL loop owned end to end** (open item 4): expose the ops as chat-callable; extend the vocabulary to retain / plant / permeate / remove; make an addition *shaped* rather than extruded. |
| P2-2 | **Orphan endpoints** (open item 12): decided **API-only** in `docs/PRD_loop_closeout.md` §1.1. Still owed: state that verdict in `docs/RUNG_AND_BUSINESS.md`, which currently claims three of them as delivered features. |
| P2-3 | **`drainage` / `road` are emittable but the ledger cannot apply them** — they are in `POOL_FOR_KIND` with no `INTERVENTION_EFFECTS` entry. Decide: add effects or drop them from the pool. (`test_proposal_contract.py` reports it; the design accepts no-effect kinds *only if disclosed at the gate*.) |
| P2-4 | **End-to-end verification (T04)**: `backend/tests/test_proposal_end_to_end.py` (the three "to verify" items as executable checks) and `backend/tests/test_legend_parity.py` (legend rows == `STYLE_FOR_KIND`, must go red when a row is deleted). |
| P2-5 | **`STATUS.md` is stale**: it still records Stage 4 as "Not started" and the catalog as "a placeholder". Both are now false. T04 owns the correction. |
| P2-6 | **Demo narrative / validation / pitch (Stage 5)**: `docs/DEMO_SCRIPT.md` was assigned and never written — no file exists. |
| P2-7 | **Browser harnesses not run since the merge**: `verify-3d` (40), `verify-ux` (20), `verify-chat`, `verify-nav`. They are excluded from CI by design; they need a local run before any rendering change is merged. |
| P2-8 | **Follow-up F1** — a per-intervention `curveNumber` in `sandbox_model.py`. Deferred deliberately: it moves published numbers, so it needs its own decision, not a drive-by edit. (Today `curveNumber` is reported as *unread*.) |
| P2-9 | **Follow-up F2** — `route_tools` forces `MASSING_TOOL` on height words, so "add a green space and make the blocks taller" never reaches the proposal path. One precedence line; recorded, not scheduled. |
| P2-10 | **Follow-up F4** — `tests/test_scenario.py:438,444` use absolute wall-clock bounds (`< 1.0 s`) while `STATUS.md:229` claims timing uses host-measured ratios. One of the two is wrong; settle it from the sibling suites. Unassigned. |
| P2-11 | **Unit coverage gaps** (open item 9): `frontend/src/view/onlineProps.ts` and `frontend/src/view/interventionLayer.ts` have none. |
| P2-12 | **Paid model id** (open items 6–7): one `backend/.env` line. Env-only and the human's call — not an agent task. The acceptance criterion is that the demo passes with **no key at all** (heuristic, labelled not-AI). |
| P2-13 | **Survey mode is unreachable** from a labelled button (STATUS, "The user flow, today"); `viewModes.ts` still has it. |

---

## DNR — deliberately not started; do not pick up reflexively

- Water-pour animation (the wet grid is a static PNG swap).
- PostGIS / object store (the seam is `app/db.py` + `scene_service.py` only).
- CFD, comfort indices, kappa validation, InVEST, typhoon surge.
- Procedural massing of the existing district; re-opening the dormant `place` op as a front-door capability.
- New physics: proposals, parameters and suggestions reuse the ledger's arithmetic.
- Model-authored polygons / free-hand ring drawing.

---

## Reference: what this branch already closed

These are **done** and should not reappear on a to-do list:

- **Open item 1** — fixed project catalog from the rasters (`data/projects.json` + `scripts/make_project_catalog.py`, every phase validated through the ledger's own coverage guard before it is written), `GET /scenes/{id}/project-catalog`, frontend reads it instead of re-deriving a sentence per click.
- **Open item 2** — `intervention_geometry.py` contours the true cell mask (holes included, no new dependency) instead of the axis-aligned box.
- **Open item 3** — `suggest.py` + `GET /scenes/{id}/suggestions` (per-axis ranking, per-cell arithmetic shown, refusals named); `pins.py` + three pin routes + the staged plan; `pinned` travels on `InterventionProperties` so an unpinned row is not scored.
- **Open item 13** — `FIT_TOOL` dropped from `route_tools` (endpoint and tests kept).
- Plan items P0.1, P0.2, P0.3, P1.1.
- Two defects found and fixed on the way: the generator-expression neighbour list in `connected_components` (8-connected components collapsed to singletons; `place` appended zero envelopes — `test_scenario` 65/65), and the grounding pools that never subtracted buildings (a site could sit on a roof — now 7,789 cells excluded, reservoir splits 1 → 3 sites).
- Routing: a deterministic classifier raises proposal-shaped turns to `intent="intervention"` with no model key; `/chat` and `/chat/stream` now agree; plurals and four missing synonyms reach the gate.

---

## Continuation prompt (for re-dispatching the team, or the lead continuing)

**Goal.** Continue `feature/intervention-loop` from `cd7a87a` (Phase 1 landed, 18/18) to a
pitch-ready, fully honest intervention loop. P0-1 is **done**. What remains: **Phase 2**
(P0-2/3/4, designed in `docs/PHASE2_DESIGN.md`, rulings R1–R23), then **Phase 3** (P1) and
**Phase 4** (P2). Every change must keep `test_proposal_contract` green-or-better and
preserve the honesty invariants: **no combined score across heat/wind/drainage, no pass/fail
verdict, a `status` + non-empty `warning` on every AI output, and provenance
(`source`/`date`/`confidence`) on every number.**

**Frozen references (read before touching anything):**
- `docs/DESIGN_intervention_proposals.md` — Part D decisions, §2.1/§2.2 window semantics,
  §4 frozen interface. This is the contract; do **not** invent a second geometry or score
  definition.
- `docs/PRD_proposals.md` — the product honesty requirements (§5 labelling, §6 questions +
  edit/confirm, §2 parameter card).
- `docs/PHASE2_DESIGN.md` — the Phase 2 rulings of record (R1–R23) plus the ordered task
  list T01–T05 and the 11 named new tests.
- `docs/PHASE1_RED_RULING.md` / `docs/PHASE1_QA_REPORT.md` — why Phase 1 went the way it did.

**Test & submission conventions (non-negotiable):**
- Backend tests are plain scripts: `cd T1/backend && ./.venv/bin/python tests/test_x.py`.
  No pytest, except `test_overlay_masking.py`, which has no `__main__` and is run under
  pytest in CI. Python 3.10.9 in `backend/.venv`. No new deps. scipy stays `<1.14`.
- **`backend/.venv` is stale: it carries pydantic 2.9.2 while `requirements.txt` pins
  2.13.5.** A green local run therefore proves nothing about `contracts/openapi.json`.
  Regenerate the contract with the **pinned** version, and never hand-edit it.
- `test_proposal_contract.py` is the gating suite — at **18/18**, never regress. Phase 2
  should RAISE it to 29/29.
- Frontend vocab pin: `contracts/intent_vocabulary.json` is the single source; regenerate
  it from `app/agent/intent.py` (keep all 7 keys: `version`, `note`, `interventionVerbs`,
  `removalVerbs`, `nouns`, `kinds`, `regions`), then mirror to
  `frontend/src/api/intentVocabulary.ts`. `backend/tests/test_intent_routing.py` and
  `frontend/src/api/intentVocabulary.test.ts` both assert equality — breaking one breaks
  the pin.

**Phase 2 — P0-2/3/4 (designed, not implemented).** Follow T01–T05 in
`docs/PHASE2_DESIGN.md`. The load-bearing items:
- **T01** `rejected[]` must actually reach the wire (`routers/scenes.py`, three
  `AIPlanResponse` sites) and the overlap guard must stop skipping.
- **T04 / R19 is the important one:** `apply_proposals` must run `validate_batch` before
  `create_plan`. A client can already write `params.storageDepthM` and have the ledger score
  it while the published provenance still claims the class default. Provenance labels must
  change SHAPE on edit (`ASSUMPTION` → `USER_ASSUMPTION` + `defaultValue`), and
  `_accepted_warning`'s unconditional "Depth is a class assumption" sentence must stop
  being emitted once a planner sets the depth by hand.

**Phase 3 — P1:** preliminary/user-defined labelling in results; short name + distinct
evidence status; `remove` op + undo stack; the second real project (a data edit under
`backend/data/projects/`). Also **unify `siting.py`'s mask** — deferred from Phase 2 T05.

**Phase 4 — P2:** end-to-end suites (`test_proposal_end_to_end.py`, `test_legend_parity.py`);
`STATUS.md` correction; `DEMO_SCRIPT.md`; browser harnesses run locally; frontend unit
coverage for `onlineProps.ts` / `interventionLayer.ts`. Keep the DNR list DNR. `P2-3` is
closed by Phase 1's Red 2.

**Definition of done for a phase:** relevant backend scripts pass, frontend vocab pin intact,
honesty invariants preserved, and a commit per phase on `feature/intervention-loop` (push
only when connectivity is confirmed). Report each commit hash.
