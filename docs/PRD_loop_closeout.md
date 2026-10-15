# PRD — Loop Close-out (branch `feature/intervention-loop`)

**Author:** 许清楚 (PM) · **Date:** 15 Oct 2026 · **Status:** proposed
**Deadline this drives:** 18 Oct pitch (P0 complete on screen), 24 Oct final demo (P0+P1).

An incremental PRD. It closes the read → suggest → author → parameterise → pin →
run/compare loop and settles the endpoint/tool questions the branch has been
carrying. It adds no physics and no new debate: the arithmetic stays in the
ledger, and the model supplies assumptions, never results.

---

## 1. Scope decisions

### 1.1 The orphan endpoints — declare API-only, surface none

The endpoints under `backend/app/routers/scenes.py` with no UI call fall into
one canonical four (STATUS open item 12) — `/ledger`, `/sea-level`,
`/scenarios`, `/scenario/evaluate` — and a wider analysis set:
`/ventilation`, `/ventilation/corridors`, `/design-metrics`, `/heat-field`,
`/drainage/routed`, `/drainage/hourly`, `/buildings/{id}/fit`.

**Recommendation: all of the above are API-only for this branch.** Record the
verdict per endpoint in `docs/RUNG_AND_BUSINESS.md` — the decision, not a build.
Rationale per endpoint, judged by demo value against build cost before 18 Oct:

- **`/ledger` — API-only.** Its figures come from the same arithmetic as
  `POST /run`'s ledger, but a GET that scores a *stored* plan would create a
  second composition path beside the pinned-set run the loop is about. The demo
  already has the better version of this endpoint; surfacing the lesser one
  invites the audience to ask which one is real.
- **`/sea-level` — API-only, plus one copy line.** The measured finding is that
  the site is landward (grid min 3.41 m vs the 2.2 m high-tide surface), so
  tidal state has ~zero effect in this window. The honest demo beat is a caption
  on the existing tide control stating that — copy, not an endpoint. A
  sea-level panel that mostly shows "no change" would spend scarce pre-pitch
  hours to make the tool look inert.
- **`/scenarios` (published CEDD programmes) — API-only, pitch appendix.**
  Honest display of these needs a paragraph of caveats per programme, and their
  demo value overlaps the fixed catalog (P0.1), which already surfaces authored
  footprints with provenance. Two scenario sources in one demo confuses the
  authoring story for zero new capability.
- **`/scenario/evaluate` — API-only.** It is the retained back door for the
  dormant `place` op (Decision 14). Surfacing it reopens a capability the
  project deliberately closed on 14 Oct; keep the back door closed in the UI and
  documented in the rung.
- **The wider analysis set — API-only for this branch.** Ventilation, corridors,
  design metrics, heat field, routed/hourly drainage and building fit are
  tested, load-bearing pitch-slide evidence and CI coverage — not loop
  features. Any of them can be surfaced after 24 Oct without touching the loop.

### 1.2 `FIT_TOOL` — drop from the tool list, keep the endpoint

**Recommendation: drop.** `assess_building_fit` is offered to the model
(`ai_plan.py`), forced by `route_tools` on "what should I do" phrasing, but the
result parser handles only `CHOOSE_TOOL` and `MASSING_TOOL`, so a fit call is
silently discarded. An offered tool whose result is never read is worse than an
unoffered one: the model spends a turn on a dead end and the user waits.
Wiring it means building a consumption path for building-level massing
candidates — work that belongs to the NL massing flow (P2.1), which the loop
is superseding with catalog + suggestions + pins. The endpoint and its 32
tests stay untouched; only the tool list and the `route_tools` branch change
(route "what should I do" toward the suggestions panel instead). Reversible in
one commit if P2.1 resurrects massing.

### 1.3 Paid-model / 429 — configuration, confirmed

**Confirmed: this is env configuration, not code.** One paid model id in
`backend/.env` removes the free-tier 429 round-trip problem in the suggest/author
path; the existing `LLM_FALLBACK_MODELS` walk over three verified free ids is
the workaround, not the fix. Critically, the loop's determinism never depends
on the provider: `suggest.py`, the catalog and `pins.py` are pure code, and the
chat card degrades to a labelled `source: "template"` with no key at all.

---

## 2. The loop as a demo — user stories + acceptance criteria

| # | Story | Acceptance criteria (all checkable on screen) |
|---|---|---|
| US1 | As a planner, I read the baseline run (sliders → ledger → overlays → warnings) so I know what the site does today. | At 450 mm the run reproduces the pinned figures (180,566.3 m³ unstored, 16.89 ha wet, 6,725 wet cells) to the digit; every sanity warning is visible, none is silent. |
| US2 | As a planner, I see ranked candidate sites per axis, each showing **its own arithmetic and evidence**, so I can choose where to intervene. | Every chip/row shows: the per-cell estimate with its basis string, the source raster, date, confidence, and the run's own deficit beside it (`runContext`). Candidates refused by the 25 % cap are named with the reason. No row shows a cross-axis ordering or a "best" badge. |
| US3 | As a planner, I author a change from the fixed catalog so the same choice is the same footprint. | Toggling a phase twice produces byte-identical geometry and the same estimate; a phase's footprint is exactly the ring generated by `make_project_catalog.py` (no per-click re-derivation). |
| US4 | As a planner, I parameterise each intervention (area, depth, permeability — each with source/date/confidence) so the numbers are mine. | A basin over a 3 m depression gets a deeper default than one over a 0.2 m swale; every parameter shows its provenance; a parameter with no ground appears as a visible `questions[]` item, never as an invented default. |
| US5 | As a planner, I pin/unpin so the run scores exactly the set I chose. | Pin two, run, unpin one, run again — the second run **equals a fresh run with only the remaining pin** (no compounding). Toggling a pin twice changes nothing after the first toggle. The HUD shows "N pinned". |
| US6 | As a planner, I run and compare so I see what moved, per axis. | The ledger shows each axis delta signed, with its unit, baseline beside scenario; no combined score anywhere; a cap refusal (25 % single / 40 % cumulative) states the refusal reason in the sanity-check pattern. |

Pipeline note: US1/US6 exist today; US2–US5 are this branch's build.

---

## 3. Requirement pool

### P0 — loop visible and deterministic on screen by 18 Oct

| Item | State on this branch | Remaining |
|---|---|---|
| **P0.1 Fixed catalog** | **Delivered:** `backend/data/projects.json` generated by `scripts/make_project_catalog.py`; served via `GET /scenes/{id}/project-catalog`; frontend `catalogApi.ts` + rewritten `ProjectCatalog.tsx`; `tests/test_project_catalog.py`. | Mount/verify in the demo panel order; confirm no `ai_plan` per-click call remains on the catalog path. |
| **P0.2 Suggestions** | **Delivered:** `app/services/suggest.py` (+ `siting.py`) over the reservoir/green/paving pools, per-axis ranking, `runContext`, signed `runoffDeltaM3`, `candidatesRefused`; `GET /scenes/{id}/suggestions`; frontend `suggestApi.ts` + `SuggestionsPanel.tsx`; `tests/test_suggest.py`. Panel mounted in the HUD column (`frontend/src/App.tsx`, between ProjectCatalog and StatsPanel). | Verify every chip's quoted number traces to ledger arithmetic, not to the suggester; confirm no `ai_plan` per-click call remains on the catalog path. |
| **P0.3 Pin/unpin** | **Delivered:** `app/services/pins.py` — one staged plan per scene (`params.stagedScenario`), content-hash idempotence, unpin = `pinned: false`; routes wired: `POST /pins`, `POST /pins/{feature_id}/unpin`, `GET /pins` (`scenes.py:124–181`). Panel mounted; run selection reconciled with the staged scenario via `StagedScenarioLink`. | `tests/test_pins.py` asserting the pin-two/unpin-one equality (task #4, yan). **P1 follow-up:** replace `StagedScenarioLink`'s 5-second poll of `GET /pins` with an event-driven update — it polls only because `SuggestionsPanel` is UI-owned and emits no pin event. |

### P1 — honest and shaped by 24 Oct

| Item | State | Remaining |
|---|---|---|
| **P1.1 Shaped geometry** | **Delivered:** `app/services/intervention_geometry.py` contours the true cell mask (no new dependency); `tests/test_intervention_geometry.py`. | Confirm polygon encloses ≥ 95 % of its scored cells and is not axis-aligned; check `STYLE_FOR_KIND` profiles render it. |
| **P1.2 Parameter card** | Not started. Sink wired: `apply_interventions` reads and validates `storageDepthM` (0.05–10 m). | `app/services/parameter_card.py` + endpoint, cached on (plan ids + intervention hash); per-field source/date/confidence; `questions[]` for ungrounded estimates; UI card. |
| **P1.3 Remove + undo** | Not started — zero `remove` op in parser/tool schema/store. | `remove` op + undo stack in the plan store; "remove the pond in the south" parses; remove-then-run equals baseline on those cells. |

### P2 — loop edges, as time allows

- **P2.1 NL loop owned end to end:** ops chat-callable; vocabulary extended to
  retain / plant / permeate / remove. (Supersedes much of the old massing flow.)
- **P2.2 Orphan endpoints:** decided here — API-only, declared in
  `docs/RUNG_AND_BUSINESS.md` (§1.1).
- **P2.3 `FIT_TOOL`:** decided here — dropped from the tool list (§1.2).
- **P2.4 Paid model id:** one `backend/.env` line (§1.3).

---

## 4. Honesty requirements carried into the UI

**The panels must always show:**

- The machine-readable `status` and human `warning` from the closed set — on
  every suggestion, catalog entry and run card.
- **Provenance per field:** which raster or document, what date, what
  confidence. A screening parcel is labelled screening: raster-read candidate,
  not a surveyed site with an owner or works programme (the `projects.json`
  warning travels with every footprint).
- **Per-axis numbers, signed, with units** — never a single collapsed figure.
  `runoffDeltaM3` is signed; an absolute value here is a lie about direction.
- The run's own deficit beside any estimate, so the chip's number and the
  ledger's number are visibly the same arithmetic.
- **Refusals and reasons:** `candidatesRefused` names what the 25 % cap kept
  out; cap and CN sanity checks state why they fired.
- `questions[]` rendered as questions, and `source: "template"` shown, not
  hidden.

**The panels must never show:**

- A **combined score**, a "best"/winner badge, or any ordering across axes.
- A **pass/fail verdict** — comparing schemes is authorised; grading is not
  (TC 1/06 §6; AVA policy).
- A screening parcel presented as a surveyed site; a generated/extruded
  geometry presented as the current district (authored interventions stay
  `draft_pending_human_review`).
- A velocity ratio from frontal-area density; the routed depth as a correction
  to the published deficit; a tidal story the measured data does not support.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| **~11 s per loop iteration.** The pinned-set run costs the same as today; a scripted demo of ten iterations is unaffordable. | Demo rehearses 2–3 iterations; sliders are set (not fidgeted) before the run; terrain conditioning and shade rasters stay cached; the backup recording covers a live-server hiccup. |
| **A ranked suggestion sits one lip from a grade.** Ranking inside an axis is one edit away from "the best reservoir". | Per-axis ranking only; code-review gate forbids any cross-axis comparator or winner badge in the UI; chips carry their axis label; the acceptance test asserts no combined field exists in the suggestions payload. |
| **The demo depends on deterministic runs.** Any clock, randomness or model call in the path breaks before/after reproduction. | `suggest.py` and the catalog are pure functions of (rasters, run result) — no model, no time; pins are content-hashed and idempotent; a regression test re-runs a seeded pinned set and asserts ledger identity to the digit. |
| **Catalog regeneration produces a phase the ledger would refuse** (cap, bounds, CN band). | `make_project_catalog.py` validates each generated phase through the ledger's own arithmetic and fails loudly at generation time; `test_project_catalog.py` pins the current catalog; the API's 503 already names the script to re-run. Regeneration is a reviewed, diffed change — never mid-demo. |

---

## 6. Out of scope for this branch

- Surfacing any orphan/analysis endpoint in the UI (§1.1 decides otherwise).
- Wiring `FIT_TOOL` result parsing (§1.2 decides otherwise).
- New physics: suggestions and parameters reuse the ledger's existing arithmetic.
- PostGIS/MinIO, CFD, comfort indices, water-pour animation, typhoon surge,
  kappa validation, InVEST (already declared out of scope).
- Re-opening the `place` op; procedural massing of the existing district.
- Commits: this PRD and the branch's code stay uncommitted until the team lead
  green-lights; `.env` changes (paid model id) are local only.
