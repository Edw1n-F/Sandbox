# PRD — The LLM Proposal Path (increment on `feature/intervention-loop`)

**Author:** 许清楚 (PM) · **Date:** 15 Oct 2026 · **Status:** proposed
**Branch:** merge `c00a266` brings main's plan-v2 proposal path onto the branch.
**Companion:** `docs/DESIGN_intervention_proposals.md` (高见远) is the system
design of record — module names, predicates and wire shapes come from there; this
document is the product spec: what the user sees, and what "done" means.

Covers the todo's five lines: **input constraints** (coordinate selection, size,
no building contact, no overlaps) · **proposal output** (type, coordinates, size,
parameters) · **2.5D block generation** · **verification** (types, AI routing,
simulation) · **extensions** (UI, LLM workflow).

---

## 1. Scope decisions

**D1 — the model never emits a polygon.** It proposes either a **site id** from
the grounding pools or a **centre + size** (`{centre:{lon,lat}, sizeCells:n}`);
the server snaps the centre to a cell, builds the window, clips it to legal
ground and contours the **cell mask** through `intervention_geometry.contour_ring`.
Rationale: a model-written polygon is rasterised by centre-test into a *different*
cell set than the one it drew, so the picture and the number would disagree —
the exact failure `siting.py`'s docstring exists to prevent. Free coordinates
become "a centre", which is checkable; a ring is not.
*Tool-schema consequence (kou, T02):* `CHOOSE_TOOL.selections[]` gains an
**optional** `placement` block; absent means today's site-based behaviour, so an
older payload behaves identically. The model's coordinates are a **request** — the
served `geometry` is never derived from the model's arithmetic.

**D2 — the constraints are code, not prompt text.** `proposal_constraints.py`
(design §3) with `validate_proposal()` / `validate_batch()`, run on every
proposal from every provider, so a provider that ignores the schema cannot leak
an illegal feature. Three predicates: **no building contact** (zero tolerance
against `building_footprint_mask`, the 396 surveyed footprints), **in bounds**,
**min size** (`MIN_SITE_CELLS` = 40 cells = 1,000 m²). **Prerequisite, and it is
not optional:** `ai_plan.load_grounding()` builds its three pools from raw class
masks and does **not** subtract the building mask, unlike `siting.py`. Fix the
pools *first* — otherwise the validator rejects the very sites the catalogue
offers and every proposal list comes back empty, a worse failure than the one
being fixed.

**D3 — "size" means three numbers, all shown.** Cells (the ledger's unit),
hectares (the planner's unit) and m² (the ring's area). A size constraint is a
per-kind min/max in cells; a target size is honoured by picking the nearest
component and **stating achieved vs requested** rather than rounding the gap away.

**D4 — an overlap is a rejection, never a clip.** The caps already measure the
**union** (`claimed | set(cells)`), so overlapping features are not double-counted
— correct arithmetic, wrong predicate for this requirement. What is missing is
proposals checked **against each other** and against the already-staged set. A
proposal that overlaps another is **rejected** with `overlapsWith` naming the
other site (or `proposal#n`), because clipping one proposal silently re-sites the
*other* one too. Clipping is reserved for the **building** constraint on a
coordinate-based proposal, and nowhere else. The reason to reject is not the
arithmetic (the union is already right) but the physical double-claim: two
accepted proposals reworking the same ground.

**D5 — routing is corrected in code, because the measured cause is
configuration.** `.env` has no key, so `AnswerCard.intent` stays at its safe
default `"question"` (`agent/answer.py:87`) and the only router left is the
client's 12-verb regex (`ChatPanel.tsx:91`) — a smaller vocabulary than the
server's. That is precisely the user's complaint. Fix: a server-side
deterministic re-route when the hint parses to a kind but `intent !=
"intervention"` (`parse_intent` already knows the nouns), and the card states
which path opened the gate ("routed by code" / "routed by model").

**D6 — caps are stated before acceptance, as exact numbers, not a projection.**
`MAX_INTERVENTION_COVERAGE = 0.25`, `MAX_TOTAL_COVERAGE = 0.40`
(`sandbox_model.py:1096,1105`). The `constraints` block carries
`withinSingleCap` / `withinCumulativeCap` computed on the **served** cell set
(`_polygon_cells` over the geometry that will be persisted) plus
`size.cells` / `size.coverageFraction`, and the prompt payload carries
`cumulative_remaining_cells`. A projection is an estimate the model would be
tempted to treat as permission.

---

## 2. User stories + acceptance criteria (screen-checkable)

**US1 — input constraints.** *As a planner, when a proposed footprint would sit
on an existing building I am told, not surprised.*
- **Rejected** and shown as a refused row (never silently dropped) with the cause
  in words: "rejected — the footprint overlaps 3 surveyed building footprints
  (1,240 m² of the ring)".
- **Shrunk** when the *building* constraint clips a coordinate-based proposal and
  ≥ 40 cells remain: "shrunk — 210 cells removed to clear existing buildings;
  1,860 kept", re-contoured from the mask so it is not a bounding box. Below 40
  cells it is rejected, and the message names the nearest viable candidate of the
  same kind. A shrink is an **annotation** on the same row (`requested` plus
  `constraints.clippedCells` / `clippedFraction`), never a second row and never a
  silent stand-in for what was asked.
- **Rejected on overlap** with `overlapsWith` naming the other site (or
  `proposal#n`). Overlaps are never clipped: clipping one proposal silently
  re-sites the other.
- Checkable by eye: toggle the 2.5D footprint layer (`footprintLayer.ts`) — no
  proposed ring has a surveyed footprint under it. A size-constrained request
  shows **requested vs achieved** cells and ha.

**US2 — proposal output.** *As a planner I see type, coordinates, size and
parameters before I accept.* Before Apply the card shows: kind + legend colour
and extrusion height · area in **cells / ha / m²** · centroid in HK1980 **and**
WGS84 · parameters (depth, permeability, CN) each with source / date /
confidence · `basis` (which raster, what date) · rationale · the axis it serves
with its signed estimate · the run's deficit beside it (`runContext`) · cap
headroom (`withinSingleCap` / `withinCumulativeCap`) · `status:
draft_pending_human_review` · and, on the same row, `requested` plus
`constraints.clippedCells` / `clippedFraction` when a shrink happened. A
rejection is not a row here — it travels in `rejected[]`. **Never** a combined
score or a "best" badge.

**US3 — 2.5D blocks.** *As a planner I can tell a proposal from the district,
and I see it become a real intervention when I confirm.*
- **Before confirmation:** proposals render in a distinct proposed style —
  outline-led, no solid extrusion, labelled "proposed — not applied"; visibly
  neither a white 2.5D block from `footprintLayer.ts` nor a stored intervention
  block from `interventionLayer.ts`.
- **After confirmation:** the accepted subset persists through `apply_proposals`
  → `scene_service.create_plan` and renders as a 2.5D block using
  `STYLE_FOR_KIND.extrusionHeight` (`ai_plan.py:851`), with its legend row.
- The stored plan is indistinguishable from a hand-drawn one except
  `source: "ai"` and `status: draft` — both visible.
- `flood_storage` is in `STYLE_FOR_KIND` but missing from `InterventionLegend`;
  every kind the API can emit must have a legend row, or the legend lies by
  omission.

**US4 — verification.** *As the team, we can prove the three things the todo asks.*
- **(1) All types generate.** For each kind in `STYLE_FOR_KIND` × its pool in
  `POOL_FOR_KIND`: instruction → proposal → apply → run, asserted end to end. A
  kind whose pool is empty returns a **refusal naming the missing raster**,
  never a fabricated site.
- **(2) Routing.** A table-driven test over the vocabulary (retain / plant /
  permeate / basin / paving / drain / road) asserts a proposal payload, not a
  result card — including with **no key configured**, which is the measured
  state today. The card states which path routed it.
- **(3) Simulation.** After Apply the plan is selectable and `POST /run` scores
  it: per-axis signed deltas, the applied plan's `draft` status visible, and the
  numbers equal a run over the same features created by hand.

**US5 — extensions.** *As a planner the review surface is readable, and the LLM
workflow stops guessing.* UI: group by kind, show the axis, collapse the
parameter block, keep Apply reachable on a long list. LLM: the prompt carries the
pool ids and the constraint vocabulary so the model selects rather than invents,
and a mis-selection is a refusal it can see and correct next turn.

---

## 3. Requirement pool

### P0 — the proposal contract

| Item | State after `c00a266` | Work |
|---|---|---|
| Confirm gate | **Exists.** `POST /ai-plan` `apply:false` → unpersisted `proposals`; `/ai-plan/apply` persists the accepted subset (`routers/scenes.py:253–370`); `InterventionProposal` (`schemas/intervention.py:162`); `ProposalReview` (`ChatPanel.tsx:265–345`) + `planApi.ts`. | — |
| Grounding pools | **Exists**, but `load_grounding()` does **not** exclude buildings; `siting.py` does. | **Fix the pools before the validator** (D2), or the list comes back empty. |
| Kind styles / 2.5D | **Exists.** `STYLE_FOR_KIND` (6 kinds), `interventionLayer.ts` reads `extrusionHeight`, `footprintLayer.ts`, `InterventionLegend.tsx`. | Legend row for `flood_storage`; a **proposed** (unconfirmed) style. |
| **Constraint gate** | **New.** Nothing on the proposal path enforces building contact, bounds or min size; only the dormant `place` op excludes buildings (`ai_plan.py:443–451`). | `proposal_constraints.py`: `validate_proposal` / `validate_batch`; `rejected[]` with `status` + `warning` + `reasonCode` on `AIPlanResponse`. |
| **Overlap predicate** | **New.** Caps bound the union but nothing tests proposals against each other or the staged set. | **Reject** with `overlapsWith` naming the other site / `proposal#n`. Never clip — clipping one proposal silently re-sites the other. |
| **Routing correction** | **Partial.** `intent`/`intent_hint` and the auto-open exist; with no key `intent` stays `"question"` and only the 12-verb client regex routes. | Server-side deterministic re-route; state the routing path on the card. |
| **Cap headroom pre-accept** | **New.** Refusal happens at apply/run today. | Exact numbers, not a projection: `withinSingleCap` / `withinCumulativeCap` on the served cell set + `size.cells` / `size.coverageFraction`; `cumulative_remaining_cells` in the prompt payload. |
| **Proposal contract QA suite** | **Being written.** | `test_proposals.py` (+ `test_proposal_constraints.py`): kinds × pools, adversarial constraint fixtures (a proposal over the school; two overlapping proposals), the routing table, apply→run parity. Must pass on a cold cache and under load. |
| `place`-op assertions | **Green.** `test_scenario.py` 65/65 ×3, re-run by the lead 15 Oct. The earlier 63/65 predates kou's fix. | No new owner: `test_scenario` keeps covering `place`. Root cause was **not** `place` — see §5. |

### P1 — honest parameters and a readable review

**Parameter card** (loop PRD P1.2): `parameter_card.py` + endpoint — the source
of a proposal's "other parameters" and its confidence block. **UI polish** (todo
extension 1): grouping, axis labels, collapsed parameter block, keyboard-reachable
checklist, the proposed style in both view modes. **LLM workflow** (todo
extension 2): pool ids and constraint vocabulary in `build_prompt`; the model's
own refusal handling.

### P2 — as time allows

User map-click site selection · kinds outside the six · undo of an applied
proposal (that is loop P1.3 `remove` + undo, not this increment).

---

## 4. Honesty requirements specific to proposals

**Must always show:** `status: draft_pending_human_review` and `source: "ai"` on
every proposal and every applied plan · `provider`, with the heuristic labelled
**not AI** · `basis` (raster + date) and per-parameter source/date/confidence ·
the axis, its signed estimate and the run's deficit beside it · cap headroom, and
the reason when a set would be refused · refusals, shrinks and overlaps with
reasons and cell counts · "proposed — not applied" until Apply, and "Applied. Run
again to see the effect." after.

**Must never show:** a proposal as a committed plan or as current state · a
screening parcel (land cover / wet-grid read) as a surveyed site with an owner,
boundary or works programme · a **combined score** or a **pass/fail verdict** · a
silently dropped proposal (a short list must never look exhaustive) · a model
rationale used as evidence for a number · a re-sited proposal presented as the one
that was asked for.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| **The model proposes geometry that violates the constraints.** | Constraints are code (D2), not prompt text; a model that promises "no buildings touched" has told us nothing. The QA suite builds its own adversarial fixture — a proposal deliberately over the school — and asserts the rejection names the building count. |
| **The validator empties the proposal list** (rejecting the sites the catalogue offers). | Fix `load_grounding()`'s pools first (D2); assert in the suite that the seeded catalogue's own sites survive validation. Order matters: fix the pool, then guard the door. |
| **Two accepted proposals overlap.** | Not a double-count — the caps already measure the union — but a **physical double-claim**: two proposals reworking the same ground. Rejected with `overlapsWith` naming the other site / `proposal#n`; **never clipped**, because clipping one proposal silently re-sites the other. Test asserts the rejection, and that an over-40 % set is refused *before* Apply. |
| **The demo depends on a rate-limited provider.** | The heuristic must remain a **complete** fallback, not a stub: `HeuristicProvider` + the labelled rescue in `generate_proposals` (`ai_plan.py:2061–2073`), satisfying every P0 criterion **with no key at all**. A paid model id is one `backend/.env` line and an optimisation, not a requirement — it needs a key and a spending decision, so it is the human's call, not an agent's. The rehearsal records a run with `provider: "heuristic"`. |
| **Proposal-shaped turns still return result cards.** | Code-side re-route (D5) + the routing table test; the card names its routing path, so a miss is visible rather than silent. |
| **A green suite hides the gap.** | The new suite is the deliverable, not a nice-to-have; every guard builds its own fixture rather than relying on store residue, and it must pass cold and under load. **Instance from this slice:** `intervention_geometry.connected_components` used a generator expression as the neighbour list, which the first `stack.pop()` consumed, collapsing every 8-connected component to singletons — so `placement_footprints` found no window and appended zero envelopes, while the suite still reported a count. A **shared helper** was silently wrong for every 8-connected caller; a `tuple` fixed it. Green counts are evidence about the checks that ran, not about the helpers they trust. |

---

## 6. Out of scope for this increment

Model-authored polygons and free-hand ring drawing (D1) · new physics (proposals
reuse the ledger's arithmetic) · procedural massing of the existing district and
re-opening the dormant `place` op as a front-door capability · undo of an applied
proposal (loop P1.3) and the wider orphan endpoints (loop PRD §1.1, API-only) ·
PostGIS / object store, CFD, comfort indices, water-pour animation · and the paid
model id, which is the human's env line, not a deliverable. Commits: this PRD and
the branch stay uncommitted until the lead green-lights.

---

## 7. Decisions (answered by the lead, 15 Oct)

1. **D1 confirmed: site id *or* centre + size, never a polygon.** The model's
   coordinates are a **request**; the served `geometry` is never derived from the
   model's arithmetic. Tool-schema consequence (T02, kou):
   `CHOOSE_TOOL.selections[]` gains an **optional** `placement`
   `{centre:{lon,lat}, sizeCells:n}`; absent = today's site-based behaviour, so an
   older payload behaves identically.
2. **No separate task for the `place` checks; the red is already green.** Lead's
   re-run: 65/65 ×3; the earlier 63/65 predates kou's fix. Root cause was **not**
   `place` — it was the shared-helper bug in §5. `test_scenario` keeps covering
   `place`; no new owner.
3. **Exact numbers, not a projection** (now D6): `withinSingleCap` /
   `withinCumulativeCap` on the **served** cell set, plus `size.cells` /
   `size.coverageFraction`; `cumulative_remaining_cells` in the prompt payload.
4. **The paid model id is not a task and no agent owns it** — one `backend/.env`
   line needing a key and a spending decision, so it is the human's call. It is an
   optimisation: the acceptance criterion is that **the demo passes with no key at
   all** (heuristic, labelled not-AI). `settings.anthropic_model` already defaults
   and is overridable.
5. **A shrink is an annotation on one row; a rejection travels in `rejected[]`.**
   `requested` plus `constraints.clippedCells` / `clippedFraction` on the same row
   — a second row would enter the gate's selection twice. A shrunken row never
   silently stands in for what was asked.
6. **Overlaps are rejected, not clipped** (§1 D4, §5) — the correction that makes
   this PRD agree with the design.
