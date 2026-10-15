# Remaining work — Hack4SDG / T1, branch `feature/intervention-loop`

Audited 16 Oct 2026 against the tree after merging main (`611ef13`) and landing the
intervention-loop work. Every item says what exists today and what is missing, so the
two are never confused.

Legend: **P0** = pitch-critical (blocks the loop on screen) · **P1** = makes an
intervention honest and shaped · **P2** = the loop's edges · **FIX** = must land before
any push · **DNR** = deliberately not started, do not pick up by reflex.

---

## FIX — before the next push

| # | Item | State |
|---|---|---|
| F1 | **`contracts/intent_vocabulary.json` lost its `version` key** when the fixture was regenerated from `app/agent/intent.py` (it was rewritten with only the five vocabulary keys). `frontend/src/api/intentVocabulary.test.ts` compares `INTENT_VOCABULARY.version` to the fixture and now fails — **frontend is 97/98**. | 1 red test |
| F2 | **`git push` is blocked by the network** (`Empty reply from server` / `CONNECT tunnel failed, response 502`). Six commits are local, from `7bc63b0` to `476e05f`. | retry when connectivity returns |
| F3 | **Check the other integration workflows.** Only `.github/workflows/ci.yml` exists in this repo; it now carries `pull-requests: read` so `dorny/paths-filter` can list PR files. If other workflows are added (or exist on other branches), they need the same grant. | done for ci.yml |

---

## P0 — close the loop visibly and deterministically

| # | Item | What exists | What is missing |
|---|---|---|---|
| P0-1 | **Wire the constraint engine into proposal generation** (the todo's "add coordinate selection and size constraints"). | `backend/app/services/proposal_constraints.py` is built and verified: 39 → `TOO_SMALL`, 40 → accepted, 4,329 → accepted, 4,330 → `OVER_SINGLE_CAP`; served == requested pre-clip. `validate_batch` owns the per-batch `claimed \|= cells` loop. | Nothing calls it. `generate_proposals` still returns unconstrained candidates, so a proposal can sit on a building, overlap a staged feature, or exceed the cap. **3 PENDING checks in `test_proposal_contract.py` (14/18) prove this.** Also owed: `rejected[]` through `/ai-plan`, the constraint echo in `/run`, and the optional `placement` block on `CHOOSE_TOOL.selections[]`. |
| P0-2 | **§2 parameter card** (open item 5). | The ledger reads `params.storageDepthM` and validates 0.05–10 m; proposals carry a `parameters` block (value/unit/status/source). | No `parameter_card.py` and no endpoint. The brief's card is specific: total site area; pond/water/wetland/tank retained *or removed*; green/tree retained or removed; new impervious share; bare ground created; explicit storage added; storage = area × depth; rainfall; heat preset; outlet level; soil group — **each with source, date and confidence**. Those numbers are still scattered across the ledger and stats dict. |
| P0-3 | **§6 the AI asks for missing assumptions.** | `InterventionProposal` carries `rationale` and `basis`. | No `questions[]` anywhere. Today the AI silently assumes a default (location, footprint, surface replaced, usable storage depth, drainage/overflow, tree cover) instead of asking. |
| P0-4 | **§6 review, edit and confirm.** | The confirm gate exists (accept/reject proposals). | A planner cannot *edit* an assumption. "Usable storage depth" — the field the brief calls out — cannot be set by hand. |

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
