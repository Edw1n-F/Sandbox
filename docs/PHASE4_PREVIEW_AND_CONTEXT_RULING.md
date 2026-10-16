# Phase 4 RULING — preview the change, account for prior work, and refuse what the executor cannot do

**Author:** 高见远 (Gao), Architect · **Date:** 19 Oct 2026
**Branch:** `feature/preview-and-context` · **Base:** `cbd4cef` (`feat(stage4): the sandbox loop`)
**Baselines:** `test_sandbox` **24/24** · `test_proposal_contract` **23/23** · `test_intent_routing` **14/14** · frontend **98/98**
**Conflict:** a concurrent engineer is rewriting the `/ai-plan` **proposal-selection** code on
`feature/region-selection`. §6 states exactly which functions are theirs and how this work composes with it.
**No production code was written.**

The owner's request, verbatim:

> *"Add an option to preview the changes. In addition, to also have the future response take into
> consideration the prior work (for instance, don't remove the water reservoir that was established
> previously."*

Two features, both underspecified. The first thing both rulings require is an **audit of `cbd4cef`**, because
that commit already ships a partial version of each — and building a second one beside a shipping one is the
failure this document exists to prevent.

**§3B was added on 19 Oct 2026** after the owner's third report — *"When I want to add buildings or other
objects, it does not respond in an intended manner."* It is a **sibling ruling to §3**, not a fourth document:
the same class of defect (an instruction the executor cannot honour, answered by producing something else),
a different mechanism. §3B.1 shows the mechanism is **not** an unmatched noun falling back — it is a **forced
tool with a closed enum**, and the substitution is *the only response the schema permits*.

**§9 was added the same day** after the owner's fourth report — *"Changes to the paving is not visible"* —
and it is part of this ruling rather than a neighbour: Feature A's preview renders through `applyStyle`, and
**four of the six intervention kinds are extruded below the terrain and never visible**. A preview of an
invisible layer is unverifiable. §9 becomes **T00** and blocks T02. Its idiom is verified by *executing* the
installed Cesium's own resolution functions, not from memory (§9.1).

**§10–§12 were added the same day**: the `EN_ALIASES → POOL_FOR_KIND` audit (§10), the closure analysis
answering whether the alias rule *provably* closes the defect family (§11 — it does not, and §11.2 says
exactly which mechanisms it does and does not close), and two conventions for implementers (§12, one of
which is that a **route docstring is a wire field** — it cost a red suite to learn).

---

## 0. AUDIT of `cbd4cef` — what already exists, measured

### 0.1 Feature A — "preview": the **shape** ships, the **effect** does not

| Piece | Where | What it does |
|---|---|---|
| Draft block layer | `frontend/src/view/proposalLayer.ts` | Draws each unconfirmed proposal as a 2.5D block at `opacity × 0.5`, `extrusionHeight × 0.4`, plus a dashed white companion polyline (Cesium's polygon outline cannot dash) |
| Draft store | `frontend/src/state/planStore.ts:43` | `proposals` — the draft set the viewer mounts the layer from |
| Mount / dispose | `frontend/src/view/SandboxViewer.tsx:854-882` | Keyed on the draft array identity; cleanup disposes, so a dismissed draft disappears |
| Suggestion → preview | `frontend/src/components/ChatPanel.tsx:1139`, `:592` | Picking a suggestion converts it to a draft proposal so the same layer draws it |

**Covered:** the **geometry** of a proposal, before it is confirmed. Critically it draws the **served**
geometry the server already clipped and contoured, never the model's `requested` ask — the layer's own
docstring says so, and that is the correct choice.

**Not covered — the whole of the owner's likely meaning:** there is no **effect** preview.
- The layer draws shapes. It computes **no numbers**: no delta, no storage, no green/paved/impervious change.
- There is no path that runs the ledger over an *uncommitted* intervention. `GET /ledger` resolves features
  from a `plan_id` (`routers/scenes.py`, `intervention_features(chosen)`); a proposal is not a plan until it
  is pinned. `/run` composes `plan_ids` from the store.

**Verdict on the lead's reading: CONFIRMED.** "Preview the changes" almost certainly means *preview the
effect* — see the delta before committing. The shape preview already exists and answers a different question.

### 0.2 Feature B — "prior work": **half** ships, and the half that ships is asymmetric

| Piece | Where | What it does |
|---|---|---|
| Suggestions skip taken ground | `services/sandbox.py:180-240` (`run_suggestions`) | `taken = claimed_cells(grids, plan_ids + staged)`; a candidate overlapping it is **skipped for the next one on its axis** |
| Confirm pins into one plan | `services/sandbox.py:248-288` (`stage`) → `pins.pin` | Prior work accumulates in the scene's staged plan — one list, three consumers (panel, viewer, run) |
| `/ai-plan/apply` now pins there | `services/ai_plan.py` `apply_proposals` → `sandbox.stage` | A confirmed proposal joins the same staged plan instead of a new plan that was never selected |
| Removal resolution | `services/sandbox.py:322-395` (`resolve_removal`), `routers/sandbox.py:120` | A staged intervention → **unpin**; a planned project → **toggle off**; buildings and today's land cover → **never match** |

**M1 — the asymmetry, measured on this HEAD.** Pin one reservoir, then ask the identical question again:

```
BEFORE pinning:  /ai-plan "add a water reservoir in the north"  -> proposals=[water_reservoir 1876 cells]
STAGE that proposal onto the sandbox                            -> 200, planId ..., featureIds [...]
AFTER pinning:   /ai-plan "add a water reservoir in the north"  -> proposals=[]  features=0
                rejected=[('OVERLAPS_STAGED', 1876 cells)]
```

So the proposal path **does** consider prior work — and then **dead-ends**. Two free reservoir candidates
still exist (`reservoir#1`, `#2`); the user gets nothing. The **suggestion** path, given the same situation,
skips to the next candidate and offers it. **Same data, same store, opposite behaviour.** That gap *is* the
owner's complaint, and it is measurable rather than a matter of taste.

**M2 — `/ai-plan` cannot see a toggled project at all.** `AIPlanRequest` (`schemas/plan.py:94-125`) carries
`instruction`, `provider`, `max_features`, `apply`, `allowed_types` — **no `planIds`**. And its claimed set is
built by `proposal_constraints.claimed_cells_for(scene_id, grids)`, which resolves **only**
`pins.staged_plan_id`. `run_suggestions`, by contrast, receives the run's `plan_ids`. So the proposal path is
structurally unable to exclude a toggled project's ground; the suggestion path does it. This is not a bug in
the guard — the guard is correct for what it is given.

**M3 — a removal instruction is inverted into an addition on the API, reproduced:**

```
POST /api/scenes/{id}/ai-plan  {"instruction": "remove the water reservoir near the south"}
  -> proposals=[('water_reservoir', 1876 cells)]   features=1      <- it ADDS one
POST /api/scenes/{id}/ai-plan  {"instruction": "delete the pond in the south"}
  -> proposals=[('water_reservoir', 1876 cells)]   features=1
```

The removal capability **does exist** (`resolve_removal`, correctly implemented, reachable at
`POST /sandbox/resolve-removal`). The **dispatch** is what is wrong, and the dispatch lives in the **client**:
`ChatPanel.tsx:113` `isRemoval(text)` → `resolveRemoval(...)` at `:1076-1097`, checked *before* the add path.
The browser therefore behaves; **every other client does not**, and the server contradicts itself: its own
`classify_turn` emits `intent="intervention"` with a `remove the … near …` hint, and `/ai-plan` then executes
that hint as a *creation*.

**M4 — no conversation awareness whatsoever.** `history` reaches `chat_service.answer` / `stream_events`
(`routers/chat.py:29,50`) for the **card**, and `AIPlanRequest` has **no `history` field**. The proposal path
is stateless with respect to the conversation.

---

## 1. RULING — Feature A: preview the **effect**, as a dedicated endpoint

### 1.1 What it is

A new **`POST /api/scenes/{sceneId}/sandbox/preview`** that composes *what is on the sandbox now* with the
*supplied candidates*, runs the project's own ledger over both, and returns **three states per figure** —
now / with the change / the difference — persisting nothing, rendering nothing, and calling no model.

It is placed in `routers/sandbox.py` because it previews a **sandbox** change: candidates that are not on the
sandbox yet, measured against what is. It is the missing half of the existing preview: the dashed layer shows
the shape, this shows the consequence.

### 1.2 Why a dedicated endpoint, and not (a) or (b)

**Against (a) `dryRun: true` on `/run`.** `run_scenario` is contractually *"one live run"* and its side
effects are load-bearing, each of which a preview must not have:

| What `/run` does | Why a preview must not |
|---|---|
| Writes `wetgrid_{id}.png`, `heatmap_{id}.png`, `wetgrid_{id}.json`, `heatmap_{id}.json` (`run_service.py:791-879`) | A previewed overlay is indistinguishable from a real one in `data/rasters/run/` |
| Calls `_prune_run_dir(run_dir, keep=20)` (`:881, :953`) | **A preview would evict real runs from the 20-file history.** Previewing repeatedly would delete the record of what was actually run |
| Mints a `runId`, returns overlay URLs | A preview would consume a run identity |
| Calls `_llm_explanation` → returns an `explanationCard` | Cost, and a *result card* for something that has not happened — the precise confusion this project forbids |

Adding a flag would make one endpoint return two shapes with different side effects. That is the
shape-failure class that has already produced two live bugs in this project (`STATUS.md`), and it would put
the run history at risk. **Rejected.**

**Against (b) a transient plan marked `draft`.** A plan is a persisted store object. The confirm gate
(plan v2 §5) exists *specifically* so that nothing is persisted until the user confirms; a preview that
writes a row inverts the gate. It would then have to be excluded from **every** reader — `pins.staged_features`
(the Interventions panel), `scene_service.list_plans`, the run's `plan_ids` composition, `claimed_cells_for`
(which would start refusing real proposals against a preview), `GET /ledger`, and the cascade delete. Any one
missed exclusion makes a preview visible as committed work, and the failure is silent. It also needs cleanup
or it accumulates. **Rejected.**

### 1.3 The response contract

```
POST /api/scenes/{sceneId}/sandbox/preview
{ "interventions": [ <feature>... ],        // candidates, as /ai-plan/apply would receive them
  "values":        [ {storageDepthM: ...} | null, ... ],   // the input card's answers, per candidate
  "planIds":       ["..."],                 // the run's toggled projects
  "rainfallMm":    450.0 }

200 ->
{ "status":  "PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE",
  "warning": "<non-empty, always — see §1.4>",
  "scope":   "the level-pool ledger only: runoff, unstored volume, storage capacity, green, paved,
              impervious share, green-and-water area. NOT the flood extent, NOT the overlay,
              NOT the surface-temperature field.",
  "rainfallMm": 450.0,
  "now":      { <ledger scenario block> },   // staged plan + toggled projects
  "preview":  { <ledger scenario block> },   // the same + the candidates
  "previewDelta": { <per-key preview - now> },
  "runDelta":     { <ledger_for_scenario's own delta: scenario - today's land cover> },
  "interventionsApplied": [...],             // what the run would apply
  "interventionsSkipped": [...],             // what the run would SKIP, each with its `why`
  "sources": { "terrain": ..., "landCover": ... },
  "ledgerSource": "sandbox_model.ledger_for_scenario / apply_interventions — the same arithmetic a run uses" }
```

Every field defaulted. **No `runId`, no `overlayUrls`, no `explanationCard` key at all** — absent by
construction, not empty. An empty `runId` is a value a client can render; a missing key cannot be.

### 1.4 RULING: the preview must be unmistakable, and the three deltas must never be conflated

**This is the load-bearing part of Feature A.**

**(i) Three different deltas will now exist. They are not interchangeable.**

| Figure | Definition | Where it is legitimate |
|---|---|---|
| `runDelta` / `delta` | scenario − **today's land cover** (no interventions) | `/run`, `GET /ledger` — the existing, published meaning |
| `now` | the **current scenario state**: staged plan + toggled projects, measured against today | new; the answer to "where am I" |
| `previewDelta` | preview − **now** = the **marginal** effect of the candidates | new; the answer to "what would this change" |

Once prior work exists, `previewDelta ≠ runDelta` — and they differ by exactly the prior work. Quoting the
marginal figure under the key `delta` would publish the run's number while showing the change's number: a
wrong figure under a `source` string that points at real code, which this project names as its worst failure
mode.

**Ruling:** `previewDelta` must **not** reuse the key `delta`; both must be present and separately labelled;
and each carries its own `source` naming the two ledger readings it is the difference of. A client that shows
one must be able to see the other.

**(ii) The preview must label itself in the payload, not only in the UI.**
`status = "PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE"` — visibly distinct from the run's own
`SCREENING_LIVE_RUN_FLOOD_EXTENT` — plus a **non-empty `warning`** stating: this is a preview of an
uncommitted change; nothing has been applied; the arithmetic is the ledger's own and is the number a run
would produce, but **this is not a run's record**. A preview whose numbers look like a run's on the wire is
the failure this ruling exists to prevent; a UI-only convention would leave the payload able to mislead any
other client.

**(iii) Scope must be declared, not implied.** The level-pool ledger does not consume `tideState` — the tide
enters the *routed cascade* and the flood-extent figure, not `_scenario_ledger`. So the preview **does not
accept `tideState` at all** (accepting an input it ignores would imply the preview reflects the tide) and it
declares in `scope` that the flood extent, the overlay and the temperature field are out of scope. Declining a
figure is honest; approximating it is not.

### 1.5 Cost and determinism

- **Deterministic, and identical to the eventual run — structurally, not coincidentally.** The preview must
  call the **same** functions the run calls (`apply_interventions` → `_scenario_ledger` /
  `ledger_for_scenario`). Equality with the applied run is then a property of there being one implementation,
  not of two implementations agreeing. A test asserts it (§8, A1).
- **Cheap, and cheaper than a run.** The preview skips exactly the expensive, side-effecting parts: no
  overlay render, no PNG encode, no temperature-field solve, no sky-view/massing terms, no sanity-check sweep,
  no model call. What remains is `storage_ledger` over the land-class grid, which is the cheap half of a run
  (the run's own docstring says the heavy terrain conditioning is cached *because* it does not change with a
  slider; the ledger does). Cost is bounded by the same grid arithmetic the ledger already performs per
  request on `GET /ledger`.
- **Byte-identical on repeat.** Same inputs twice → identical figures. No `time`, no `random`. The one
  ordering hazard is the feature list itself: compose it in a stable order (staged rows by id, then toggled
  plans in the order given, then candidates in the order sent) so `interventionsApplied`'s order — and hence
  any `claimed |=` accumulation — cannot vary.

### 1.6 The input-card trap

The input card changes the ledger: `storageDepthM` is read by `apply_interventions`. So a preview that ignores
the card's answers previews a **different intervention** than the one about to be confirmed. **Ruling:** the
preview must run candidates through `sandbox.apply_inputs(kind, params, values[i])` — the same function
`/ai-plan/apply` uses — before measuring. Previewing the estimate while the user has typed a depth shows a
number that will not happen.

---

## 2. RULING — Feature B, part 1: prior work is the **claimed cell set**, and the response must **skip**, not dead-end

### 2.1 Representation: the claimed cell set. Not the transcript, not a prose list

**Ruling: prior work is represented as the set of cells already claimed, computed by code**
(`proposal_constraints.claimed_cells` / `claimed_cells_for`), sourced from
`pins.staged_features(scene_id)` plus every toggled project's plan.

Why, against the alternatives:

- **The conversation transcript** (`history`) is unverifiable prose. Making exclusion depend on it makes the
  guarantee model-dependent, and the project's rule is *grounding is code, not prompt text* (PRD D2). A model
  that summarises its own earlier turns can be wrong, and it would be wrong *in the direction of silently
  allowing* a proposal over existing work. **Rejected.**
- **A list of placed interventions as objects** is the right *source* but the wrong *interface*: the guard
  needs cells to test geometry against, and `claimed_cells` is already that vocabulary. It also carries an
  owner per cell, which is what makes `OVERLAPS_STAGED` able to say *whose* ground it was (`overlapsWith`).
- **The claimed set is already the project's answer** — it is what the overlap guard and the suggestion path
  both use. One representation, one set of cells, checkable.

### 2.2 Behaviour: skip to the next candidate — do not refuse the whole kind

**M1 is the ruling's justification.** Today the proposal path refuses (`OVERLAPS_STAGED`) and returns an
empty list while free candidates exist. The suggestion path skips. **Ruling: the proposal path must narrow
its pools by the claimed set, so a taken candidate is never offered and a free one is.** The overlap guard is
**not** weakened — it stays exactly as it is, as the backstop for anything that still collides (a coordinate
request, a race with a concurrent pin). Skip at selection; refuse at the gate. Both, for the same reason the
region work needed both: prevention without detection cannot be tested, detection without prevention
dead-ends.

This is not a new mechanism. It is `run_suggestions`' existing rule
(`if taken and _cells_of(...) & taken: continue`) applied to the other entrance to the same sandbox.

### 2.3 `/ai-plan` must be able to see the toggled projects

**M2: it structurally cannot today.** `AIPlanRequest` has no `planIds`, and `claimed_cells_for` resolves only
the staged plan.

**Ruling:** `AIPlanRequest` gains `planIds: list[str] = []` (additive, defaulted — an existing client is
unaffected), and `generate_proposals` passes the composed claimed set into pool selection. The frontend
already holds the selection in `planStore`; it need only forward it.

### 2.4 RULED OUT: conversation awareness as a proposal input

**`AIPlanRequest` must NOT gain `history`.** Three reasons:

1. It would make exclusion depend on the model's memory of its own prior prose — model-dependent grounding,
   against D2 (§2.1).
2. The owner's own example does not need it. *"Don't remove the water reservoir that was established
   previously"* is satisfied by the **placed** reservoir, and once confirmed that reservoir is in the staged
   plan and §2.2 handles it. Conversation awareness would only add the case "proposed but not confirmed",
   which is a *draft* — and a draft has never claimed cells (correctly: `cbd4cef`'s note that "unpinned rows
   no longer claim cells in the overlap check" is the same insight).
3. **"Prior work *in this response*" already holds**: `validate_batch` owns `claimed |= cells` in provider
   order, so two proposals in one batch cannot claim the same ground. That is the only sense in which a
   *response* can consider *its own* prior work, and it is already guaranteed by code.

**If the lead wants the model to see what has been placed**, the honest and cheap shape is a **facts block
read from the store** — the staged rows' kind/area/region, and the toggled projects' names — added to
`build_prompt`'s payload. That is *prompt context*, never a filter, and never the transcript. It is a P2
nice-to-have and I do not recommend it before §2.2 lands, because a model told about placed work and still
offered the taken site would be worse than a model not told.

---

## 3. RULING — Feature B, part 2: the removal inversion

### 3.1 What is wrong is the **dispatch**, and it lives in the client

M3 establishes: `resolve_removal` exists and is correct; the removal verbs are advertised; the server's own
classifier routes a removal as `intent="intervention"`; and `/ai-plan` then **creates** the thing the user
asked to destroy. The browser escapes this only because `ChatPanel.tsx:113` intercepts removals first — a
**client-side** predicate. Every other client, and the API itself, gets the inversion.

### 3.2 RULING: `/ai-plan` must refuse a removal, not perform one

**The executor refuses; the capability stays.**

- **`/ai-plan` detects a removal instruction and refuses it with HTTP 422**, `detail` naming
  `POST /api/scenes/{sceneId}/sandbox/resolve-removal` as the route that honours it. 422 rather than 500,
  matching the existing precedent in the same handler (`PlanGenerationError → 422`: *"the request was
  well-formed, the machine simply cannot honour it, and the client needs to know which"*).
- **The detection is server-side and reuses the server's own vocabulary** — the normalised hint
  `agent.intent.parse_intervention_intent` already produces starts with `remove ` for these turns. Using the
  server's own determination avoids a second, drifting regex. A client-side fast path may remain (it saves a
  round trip); the *guarantee* no longer depends on it.
- **A mixed instruction refuses too.** "remove the paving and add a reservoir" normalises to a `remove …`
  hint. Ruling: refuse, and the `detail` says the sentence mixes a removal with an addition and to send them
  separately. Silently executing the addition half of a destructive+constructive sentence is the same class
  of lie as the inversion.

### 3.3 Why **not** the alternative the briefing offered ("stop the vocabulary advertising the verb")

**Rejected, decisively, and the reason is mechanical.** `contracts/intent_vocabulary.json` is pinned
three ways (`test_intent_routing.py` ↔ `app/agent/intent.py` ↔ `frontend/src/api/intentVocabulary.ts`). The
frontend's `isRemoval` reads `INTENT_VOCABULARY.removalVerbs` **from that same pinned fixture**. So dropping
`remove/delete/drop/take out` from the server vocabulary would, **by the pin's own construction**, drop them
from the client's — `isRemoval` would return `false` for every sentence, removals would fall through to
`/ai-plan`, and the API-only inversion would become a **UI-wide** inversion. The "fix" converts a bug that one
client already avoids into one that no client avoids.

Also: the capability is not missing. `resolve_removal` correctly unpins a staged intervention and toggles off
a planned project, and the sandbox suite pins it (`a planned project resolves to a toggle-off, never a
delete`, `existing buildings are not removable`, `unpinning takes it off the sandbox and out of the run`).
Removing the verbs would delete working, tested, user-facing capability to paper over a dispatch hole.

### 3.4 Severity, stated honestly

This is the worst of the three defects in this document, and it is worse than its size suggests: it is
**wrong in the destructive direction**, **silent**, and it **looks like success** — the user is shown a
plausible, legal, accepted proposal. It is masked in our own UI by a client-side check, which means it will
survive every browser walkthrough and appear only to an API consumer. `docs/TEST_PLAN_intervention_loop.md`
already identifies this class as its test #1.

---

## 3B. RULING — the third case: a **capability** the executor does not have

*(Added 19 Oct 2026 on the lead's measured report. Same class as §3, different mechanism — so the ruling is
the sibling of §3.2, not a repetition of it.)*

The owner: *"When I want to add buildings or other objects, it does not respond in an intended manner."*
Reproduced on `cbd4cef`. **`buildings` / `school` / `hospital` / `office block` / `tower` are not capabilities
this sandbox has**, and nothing tells the user that.

### 3B.1 The mechanism is not "the noun falls back" — it is a forced tool with a closed enum

Three compounding causes, **all on our side**. The heuristic provider is innocent **for buildings**: measured,
`HeuristicProvider().propose()` returns **0 features** for every one of the five building phrasings, so the
substitution happens on the **LLM leg only**, and it is forced.

**SCOPE CORRECTION (19 Oct, §10.2).** That clearance is **specific to this noun class, not general.** The
`EN_ALIASES → POOL_FOR_KIND` audit found a substitution **in the heuristic**: `"add a floodable park"` yields a
`green_space`. I am narrowing the claim here rather than letting the earlier wording stand — see §10.

| # | Cause | Where | Measured |
|---|---|---|---|
| **C1** | `route_tools` **forces** `select_intervention_sites` for any turn that is not massing, and `tool_choice` is `{"type": "tool", "name": forced}` | `ai_plan.py:1989`, `:1721` | `route_tools("add a school")` → `forced=select_intervention_sites` |
| **C2** | That tool's schema **requires** `selections`, and each item **requires** `intervention_type` from a **closed enum** | `CHOOSE_TOOL`, `ai_plan.py:1390`; the enum at `:1407` | `enum=['drainage','green_space','permeable_paving','road','water_reservoir']`; `required=['selections']`; item `required=['site_id','intervention_type','rationale']` |
| **C3** | The system prompt's escape (*"If no site is appropriate, return an empty selection list"*) is **weaker than the schema's `required`**, so declining means violating the schema | `build_prompt`, `:1656` | — |

So for *"add a school"* the server **forces** a tool whose schema **has no way to say "I cannot"**. The
model's only legal move is to return one of the five kinds — `green_space` — and the tool *requires* a
`rationale`, so it narrates the substitution: *"The largest contiguous vegetated block is the most suitable
for adding a school."* **That sentence is not the model disobeying us. It is the model filling the only shape
we permitted.**

And when the model declines anyway (*"add a hospital"*), `generate_proposals` returns the generic
*"Nothing could be placed: no candidate survived the constraints, or no provider returned a usable answer for
this instruction"* — which **blames the instruction or the provider** for a capability gap that is ours.

**Class, stated once.** Same as the removal inversion: *an instruction the executor cannot honour, resolved by
producing something from the pool instead of declining.* Removal's shape was `parse_intent`'s `(kind, count)`
with no removal op; this one's is a forced tool with a closed kind enum.

**The family, counted honestly (§10.5/§11.1).** Six instances are now **named**; **five reproduce on this
HEAD** and **one no longer does**:

| # | instruction | result | reproduces on this HEAD? |
|---|---|---|---|
| 1 | `remove the water reservoir near the south` | adds a 1,876-cell reservoir | **yes** (§3, M3) |
| 2 | `add a school` | `green_space`, with a rationale justifying it | **yes** (§3B.1) |
| 3 | `drop the trees` | *was* three water reservoirs | **no** — the vocabulary moved; `parse_intent` now returns `[]` (§11.1) |
| 4 | `add a floodable park` | `green_space` (a ponding feature returns a park) | **yes** (§10.2) |
| 5 | `add a wetland` | **nothing**, from a gate that opened cleanly | **yes** (§10.4) |
| 6 | `add flood storage` | `water_reservoir` — a 14 m blue block for a flat floodable park | **yes** (§11.1, found by the enumeration) |

That count is the argument for the shared refusal path — **and §11.2 shows R-B closes only two of the four
*mechanisms*, and instance 6 not at all.** Do not read this table as "closed".

### 3B.2 RULING: refuse with **422**, from a guard that runs **before any provider is called**

Not a 200 with an empty `features` and a stated reason. Five reasons, each from the reasoning already used in
§3.2:

1. **A 422 makes the response not-an-AI-output, and that is what keeps invariant 3 honest.** The invariant is
   *"a `status` plus a non-empty `warning` on every AI output."* `AIPlanResponse` has **no top-level
   `status`/`warning`** (`schemas/plan.py:127-155`) — only per-proposal ones. So `200 {features: 0,
   proposals: []}` is an "output" carrying **neither**, and being honest about it would mean widening
   `AIPlanResponse` — a wire change and an openapi regeneration — **for a case where no model ran**. A 422
   sidesteps that: no model ran, so there is no output to label.
2. **A 200 cannot be distinguished from success by a client that does not read prose.** The entire defect is
   that the failure *looks like success*. A status code is the one signal no client can render as a plausible
   result by accident.
3. **It removes the opportunity for substitution rather than filtering its product.** The model is never
   asked, so it cannot confabulate a rationale. Same move as §1.3's short-circuit for an empty region, and the
   same reason: do not pay for a call that can only produce something wrong.
4. **Precedent and consistency.** `PlanGenerationError → 422` is already this handler's rule — *"the request
   was well-formed, the machine simply cannot honour it, and the client needs to know which"* — and §3.2
   ruled removal the same way. One status for "cannot honour", **two distinguishable `detail`s**.
5. **It is already legible to the existing client.** `frontend/src/api/client.ts:75` resolves
   `data.detail` into `ApiError.message`, so a readable `detail` renders today with **no client change**.

**Two empty-result cases that must stay distinct — and this is why 422 rather than a blanket rule:**

- **(i) The executor never ran the model** (no supported capability named) → **422**, no AI output.
- **(ii) The model ran and legitimately chose nothing** (*"no site is appropriate"*) → **200** with `message`
  + notes, exactly as today.

Case (ii) is a real AI output that today carries **no top-level `status`/`warning`**. That is a **pre-existing
latent gap in invariant 3**, not something this ruling introduces, and I am **not** fixing it here — it needs a
wire change and an openapi regeneration, and it should be its own decision. Flagged in §8 so it is not
mistaken for part of this work.

### 3B.3 What the message must name — and a correction to the briefing

Three lists, each **built from one source of truth, never typed**:

| What | Source of truth | Measured value |
|---|---|---|
| the kinds it can **create** | `sorted(set(POOL_FOR_KIND))` — **the same expression `CHOOSE_TOOL`'s enum is built from** (`ai_plan.py:1407`) | **5**: `drainage, green_space, permeable_paving, road, water_reservoir` |
| the massing ops it **does** have | `MASSING_TOOL["input_schema"]["properties"]["edits"]["items"]["properties"]["op"]["enum"]` | **6**: `scale_all, set_site, rotate, move, reset, resize` |
| the regions it understands | `sorted(REGION_FRACTIONS)` | 10, including `all` |

**CORRECTION — the briefing's list named six kinds including *floodable ground*. That is wrong for this
route.** `flood_storage` is in `KIND_WORD` (6) and `STYLE_FOR_KIND` (6) but is **absent from `POOL_FOR_KIND`
(5)**, so the `CHOOSE_TOOL` enum has no such member and the heuristic has no pool for it: **`/ai-plan` cannot
propose floodable ground.** Naming it in the message would promise a capability the endpoint does not have —
the same class of error as the substitution it is meant to explain.

**Why the source matters here specifically.** There are **three** kind vocabularies and they disagree by
design: `KIND_WORD` (6 — the router's nouns), `POOL_FOR_KIND` (5 — what `/ai-plan` can propose),
`STYLE_FOR_KIND` (6 — what the renderer can draw). Typing the list, or reading the wrong one, produces a
message that lies. `/ai-plan`'s message must be **generated from `POOL_FOR_KIND`.**

### 3B.4 The message's shape, and the false positive it must not create

The detector is *"a creation verb is present, **no supported noun matches**, and the turn is not
interrogative"*. That predicate is **also true of phrasings whose intent IS supported but whose noun is not in
the vocabulary yet** — and a message that told such a user their request is unsupported would be **worse than
today's silence.**

**CORRECTION (19 Oct, measured — the T03/T03B implementer measured it first).** This paragraph offered four
`PROPOSAL_SHAPED_GAPS` phrasings as the demonstration. **The live surface is far narrower than that, and the
counts matter:**

| | phrasings listed here | reach `classify_turn` as an intervention | **fire `is_unsupported_capability`** |
|---|---|---|---|
| as written in this section | 4 | 4 | **4 (implied)** |
| measured today | 4 | 4 | **1** — only *"create a wetland"* |
| after R-A (§10.5) | 4 | 4 | **0** |

Three of the four — *"green corridor"*, *"permeable plaza"*, *"basin"* — are now in `ai_plan.EN_ALIASES` (the
16 Oct additions), so `_match_noun` finds them, `classify_turn` **routes** them, and the guard never fires.
The fourth, *"create a wetland"*, fires for a **different** reason than this section gave: it resolves to
`flood_storage`, which has no pool (§10.4) — and **R-A gives it one, so it stops firing too.**

**The ruling stands; only the illustration was wrong — and the narrower surface is an argument *for* the
ruling, not against it.** The constraint is *"the message may never claim anything about the user's sentence"*.
That is a statement about the **shape** of the message, so it holds whatever the alias set happens to be. The
error in this paragraph was proving a shape claim with a **count** — and a count defined by the current
vocabulary silently goes stale, which is the same failure the message rule exists to prevent, one level up.
**So do not rebuild the demonstration on a fixed list; state the invariant.**

**Credit where it is due: the implementer's C4 is better than what this paragraph asked for.** It asserts each
phrasing **routes** *and* that at least one **fires** — `assert refused >= 1`, with the message *"no
PROPOSAL_SHAPED_GAP fires the detector, so this test exercised nothing; if the vocabulary learned every synonym
that is fine, but say so rather than passing vacuously"*. That is the anti-vacuity discipline this project has
needed more than once (a guard that never executes, a suite that passes because it skipped). It is also why
**R-A cannot simply delete the last firing phrasing** — see §10.5.

**Ruling — every clause must be a fact about the *system*, never a claim about the *user's sentence*:**

```
I could not match this to an intervention I can create.
  create: water reservoir, green space, permeable paving, drainage, road
  reshape existing buildings: scale taller/shorter by %, set a height, rotate, move, resize, reset
Existing buildings come from the surveyed city model and can be reshaped, not created.
If you meant one of the above, name it — e.g. "add a water reservoir in the north".
```

- It answers **"add a school"** and **"add 3 buildings"**: the reshape line shows what happens to buildings —
  the user learns the real boundary **without us asserting that we understood the sentence**.
- It answers **"plant a green corridor"**: `green space` is listed, so the retry is obvious.
- **It never says "a school is impossible"**, which we do not know. We know only that no supported noun
  matched, and the message says exactly that.

**Explicitly rejected: a building-noun vocabulary** (`school`, `hospital`, `office`, `tower`, …) for a
custom message. I checked: **there is no reusable building-noun list** — `parse_massing_intent` is regex
patterns plus scope words and has no noun table. Adding one would be a **fourth** vocabulary, incomplete by
construction, and free to drift. The capability statement above needs no noun list and is true for every
trigger.

### 3B.5 The chat side: the card is the seam, and the cause is currently **misattributed**

**Measured.** `classify_turn("add a school in the north", card)` → `intent="ambiguous"`,
`intent_source="parser"`, `hint=None`. And `template_card(facts)` returns `_empty_card()` when `facts` is
empty — the card titled **"Nothing to explain yet"**.

**Ruling: do NOT widen `intent`.** Your instinct is right; here is the full justification.

1. `agent/intent.py` is a **pin** — 14/14 plus the three-way vocabulary round-trip. A new member changes the
   precedence ladder **and** the pinned fixture.
2. **The information needed is not an *intent*, it is a *reason*.** The card already has first-class fields
   for exactly this: `cause`, `todo`, `confidence.note`. It is filling them with the **wrong cause**.
3. That wrongness is itself an honesty defect, and it is measurable. `_empty_card` says
   `confidence.note = "No run in scope yet: there is nothing to explain until a scenario has been run"` and
   `todo = "Press Run Simulation in the Scenario Inputs panel."` For **"add a school"** both are **false** — a
   run may well exist, and pressing Run Simulation does not help. **The card attributes a capability gap to a
   missing run.** Its own docstring is careful that it *"must not blame a missing model"*; it currently blames
   something else that is equally not the cause.

**Ruling: a sibling card**, a pure builder in `answer.py`, e.g.
`unsupported_capability_card(question, card)`, filling `cause` / `todo` / `confidence.note` with the true
cause and carrying §3B.4's text.

**Where it is selected — one seam, not seven.** `classify_turn` is called at **seven** sites in
`chat_service.py` (`:202, :210, :212, :214, :230, :259, :363`). Ruling: add a single wrapper
`_classify(question, card)` in `chat_service` that calls `classify_turn` and then applies the card, and
replace the seven call sites. **`agent/intent.py` is not modified at all** — its ladder, vocabulary and pin
are untouched.

**Trigger precision.** The card applies when `intent == "ambiguous"`, `intent_source == "parser"`, and a
creation verb is present. An interrogative (*"can I add a school?"*) is already `question` and must keep its
normal card — the selector must not fire on a question. The classifier already computes the distinguishing
state, so the selector needs **no new data**.

### 3B.6 Scope separation: **one shared refusal path, two detectors, two messages**

| | removal (§3.2) | unsupported capability (§3B) |
|---|---|---|
| **detector** | a **removal verb** is present (the pinned `REMOVAL_VERBS`) | a **creation verb** is present **and no supported noun matches** |
| **source of truth** | the pinned removal vocabulary | `EN_ALIASES` ∩ `POOL_FOR_KIND` |
| **`detail` names** | the supported kinds, and the route that honours it: `/sandbox/resolve-removal` | the supported kinds **and** the massing ops |
| **shared** | the **refusal path**: one helper raising the handler's existing `PlanGenerationError`-shaped 422 with a code-built `detail` | same |

**Do not write one detector to cover both.** The inputs differ (a verb class vs. a verb-plus-missing-noun),
and merging them would blur the two `detail`s — after which a client grouping by status alone could no longer
tell *"you asked to destroy, which is a different endpoint"* from *"you asked to create something we do not
make"*. **Share the mechanism; keep the detectors and the messages separate.**

**Overlap with `feature/region-selection`: none**, provided §3B.7 is respected. Both detectors live in
`routers/scenes.py` + a predicate in `agent/intent.py`; the chat card lives in `answer.py` +
`chat_service.py`. None of those is the proposal-selection path.

### 3B.7 Do NOT "fix" the forced tool — close the door instead

C1 and C2 are the **mechanism**, and both tempting fixes are wrong:

- **Do not widen the `intervention_type` enum.** That enum is the grounding guarantee — it is why a model
  cannot label a park a reservoir, and why `_materialise` can trust the id's pool over the model's own
  `intervention_type` field. Widening it re-opens the free-form-kind problem the enum exists to close.
- **Do not remove the forced `tool_choice`.** `route_tools` forcing the tool is what makes the routing
  guarantee real — the module's own note says *"The tool is chosen in route_tools(), not by the model … Forcing
  it is what makes that guarantee real."* Making it `auto` would let a model answer a proposal request in
  prose, which the grounding check would then have to police.

The pre-provider guard (§3B.2) makes the forced-tool path **unreachable** for an unsupported request. **Close
the door rather than loosen the schema.** C1 and C2 stay exactly as they are — which is also why this work
does not touch the concurrent branch.

---

## 4. Honesty invariants — confirmation for all three cases

| # | Invariant | Feature A (preview) | Feature B (prior work) | 3B (missing capability) |
|---|---|---|---|---|
| 1 | No combined score across heat/wind/drainage | Per-axis figures only; the preview reports each ledger term independently and adds no ranking | The claimed set is cells, not a score; skipping a taken candidate is not a preference | The refusal ranks nothing; it lists what exists |
| 2 | No pass/fail verdict | A preview states three states and their difference; it does not advise | A refusal keeps its `reasonCode`; a skip is not a verdict on the site | The message states the system's capability, not a verdict on the request (§3B.4) |
| 3 | `status` + non-empty `warning` on every AI output | **Strengthened:** `PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE` + a mandatory warning that says what it is and what it is not (§1.4ii) | Unchanged; the 422 carries a `detail` naming the route | **Satisfied by producing no AI output at all:** the guard runs before the provider, so a 422 is not an output to label (§3B.2.1). *But* it also fixes a live violation: `_empty_card` currently dates the cause wrongly (§3B.5) |
| 4 | Provenance on every number | Each figure is a ledger reading; `previewDelta` names the two readings it is the difference of; `scope` declares what is *not* included | The claimed set is cells read from the store; the exclusion is code | Each list in the message is generated from its owning constant (`POOL_FOR_KIND`, the massing op enum), never typed (§3B.3) |
| 5 | The model supplies an assumption, never a result; grounding is code | The preview computes nothing the model wrote; a preview is arithmetic over the user's candidates | §2.1: exclusion is code, never the transcript | **This is the invariant the substitution violated** — the model was forced to supply a *result* (a kind) for an assumption it could not ground. The guard restores the rule by not asking (§3B.1) |
| 6 | `reasonCode` inside the frozen set | **none added** — a preview produces no refusals of its own; it reports the run's own `interventionsSkipped` | **none added** — the removal refusal is a 422, not a `reasonCode`, for the reason in §3.2 | **none added** — the same 422 path, for the same reason |
| 7 | Determinism | Stable composition order; no `time`/`random`; equality with the applied run asserted | Iteration over an ordered list; no set ordering anywhere | The detector is a pure function of the sentence; the guard runs before any provider call |

### Most at risk: **#4, in Feature A — via the three deltas**

The mechanism is precise and it is the same shape as the project's named worst failure: once prior work
exists, `previewDelta` and `runDelta` differ, and both are legitimate ledger figures with real sources. A
response that publishes the marginal figure under the key `delta` — or a UI that shows one and calls it the
other — puts a correct number under a label that describes a different quantity. **Mandatory mitigations:**
the two keys are distinct and both present; each carries a `source` naming its operands; and test A5 asserts
they differ once prior work exists, so the distinction cannot silently collapse.

### Second most at risk: **#3, in Feature A**

A preview is the most tempting thing in this project to let look like a run: it returns the same ledger keys
with the same arithmetic. The whole of §1.4 exists to keep that honest. The specific trap is the **absent
keys** — the honest way to say "there is no flood extent here" is a missing key plus a `scope` sentence, not
a `null` or an empty string a client can render as "no flooding".

---

## 5. Sequencing, and the conflict with `feature/region-selection`

A separate engineer is rewriting the `/ai-plan` **proposal-selection** code on `feature/region-selection`.
Their scope, from their ruling: `_point_in_region`, `sites_in_region`, `parse_intent_counts` (**new**),
`HeuristicProvider.propose` (`ai_plan.py:1354-1373`), `build_prompt` (`:1640-1651`), `_materialise`
(`:1727`, `:1880`), `_feature` (`:1025-1051`).

**This document's overlap is §2.2 only.** Both changes narrow the same pools by a predicate. Two rewrites of
`HeuristicProvider.propose` will conflict. **Do not land them as two edits to one function.** The resolution
I rule:

```
load_grounding(region: str | None = None, claimed: set[tuple[int,int]] | None = None) -> Grounding
```

Both parameters **optional, default `None` = today's exact behaviour**, so no existing caller changes. Inside,
each pool is narrowed by a single composed pipeline, in this order:

```
pool   ->  sites_in_region(pool, region, bounds)      # feature/region-selection, T01
       ->  sites_free_of(eligible, claimed)           # this branch, §2.2
       ->  pool's own ceiling
```

Both steps are list comprehensions over an already-ordered list; `sites_free_of` is one line
(`[s for s in sites if not (_cells_of(geography, s["geometry"]) & claimed)]`). Whichever branch lands second
adds one line and one optional parameter. Both the heuristic **and** the catalogue then read the same
narrowed pools from one place, which is why the filter belongs in `load_grounding` rather than in
`build_prompt` — otherwise the model would be offered sites the filter already excluded, which is the drift
the region ruling's §3 forbids.

**Ordering rule:** land the **region** change first (it is on its own branch and is the narrower edit), then
rebase this branch and add the claimed filter. Do **not** attempt both at once.

Everything else in this document is **conflict-free**: the preview is a new endpoint and a new service
function; the removal guard and the missing-capability guard (§3B) live in `routers/scenes.py` + predicates in
`agent/intent.py`; the chat card lives in `answer.py` + `chat_service.py`; the `planIds` field is a schema
addition. None of them touches the region engineer's functions.

**One explicit boundary from §3B.7:** that ruling requires **no change to `ai_plan.py` at all** — C1
(`route_tools`'s forced `tool_choice`) and C2 (`CHOOSE_TOOL`'s closed enum) stay exactly as they are, and the
guard makes them unreachable for an unsupported request. So the third case adds **zero** overlap with
`feature/region-selection`, and T03 can land in parallel with it.

---

## 6. Task list (ordered by dependency)

Six tasks (T01–T05, with T03B added by §3B). **No two touch `ai_plan.py`'s proposal-selection functions
except T04b**, which is gated on the region branch merging.

### T01 — The preview endpoint and its ledger path
- **Files:** `backend/app/services/preview.py` (**new**); `backend/app/routers/sandbox.py` (new route);
  `backend/app/schemas/sandbox.py` or the router's local `WireModel`s (**new** request/response models)
- **Functions:**
  - `preview.compose_features(scene_id, plan_ids)` — staged pinned rows + toggled plans, **stable order**
    (staged rows by id, plans in the order given). Reuses `intervention_features_many` semantics for the
    plan side rather than a second composition.
  - `preview.preview(scene_id, interventions, values, plan_ids, rainfall_mm)` — runs `apply_inputs` per
    candidate, composes `now` and `preview` through `sandbox_model.ledger_for_scenario`, returns the block in
    §1.3. **No file writes, no model call, no `runId`.**
  - The `now` reading is one `ledger_for_scenario` call, so "where am I" and "what would change" come from the
    same function.
- **Depends on:** —
- **Wire change:** **yes** — a new path and new schemas ⇒ `contracts/openapi.json` **must be regenerated** as
  part of this task (`/tmp/v_pin/bin/python -m app.export_openapi` from `backend/`), or
  `test_openapi_contract.py` fails. Regeneration is not a separate task; it is the last step of this one.
- **Priority:** P0

### T02 — Preview labelling and the frontend surface
- **Files:** `frontend/src/api/sandboxApi.ts` (+ `types.ts`); `frontend/src/components/ChatPanel.tsx`
  (the confirm gate's action bar); `frontend/src/state/planStore.ts` (hold the preview reading beside the
  draft set)
- **Change:** a **Preview** action beside Apply/Dismiss that calls T01 and renders the three states. It must
  display `status`/`warning`/`scope` **verbatim** and must not present `previewDelta` as the run's `delta`
  (§1.4i). The dashed geometry preview already on screen is the shape half; this adds the numbers half.
- **Depends on:** T01
- **Priority:** P0

### T03 — The shared refusal path, with **two** detectors
- **Files:** `backend/app/agent/intent.py` (a predicate reusing the normalised hint, plus the
  creation-verb-and-no-supported-noun predicate — **no vocabulary or ladder change**),
  `backend/app/routers/scenes.py` (`create_ai_plan`: the guard **before** `generate_proposals` and the shared
  422 raiser), tests in `test_proposal_contract.py` / `test_sandbox.py`
- **Change — one path, two detectors (§3B.6):**
  - **removal (§3.2):** a removal verb is present → 422, `detail` names the route that honours it.
  - **missing capability (§3B):** a creation verb is present and no supported noun matches, and the turn is not
    interrogative → 422, `detail` carries §3B.4's capability statement.
  - The `detail` lists are **generated from `POOL_FOR_KIND`**, the massing op **enum**, and
    `sorted(REGION_FRACTIONS)` — never typed (§3B.3).
  - **Nothing in `ai_plan.py` is touched.** C1/C2 stay exactly as they are (§3B.7): do not widen the enum, do
    not un-force the tool. The guard closes the door; it does not loosen the schema.
- **Depends on:** —
- **Conflict:** none — `agent/intent.py` and `routers/scenes.py` are outside the region engineer's scope, and
  this task does not enter `ai_plan.py`'s proposal-selection path.
- **Priority:** P0 — this covers the worst defect in the document (§3.4) **and** the substitution (§3B.1).

### T03B — The chat card for a capability the sandbox does not have
- **Files:** `backend/app/agent/answer.py` (`unsupported_capability_card`, a sibling of `_empty_card` at
  `:318`); `backend/app/agent/chat_service.py` (a `_classify(question, card)` wrapper replacing the **seven**
  `classify_turn` call sites at `:202, :210, :212, :214, :230, :259, :363`)
- **Change:** when the classifier returns `intent="ambiguous"` **from the parser** with a creation verb
  present, replace the *card* — filling `cause` / `todo` / `confidence.note` with the true cause and carrying
  §3B.4's text. **`agent/intent.py` is not modified.**
- **Why this is the seam, not `intent`:** the card already has `cause`/`todo`/`confidence.note`, and
  `_empty_card` is currently filling them with a **false** cause — it tells the user to press Run Simulation
  for a capability gap, and dates it to a missing run (§3B.5).
- **Depends on:** T03 (shares the detector predicate and the capability statement)
- **Regression risk:** `test_intent_routing.py` **must not move** — no ladder change, so all 14 stay. Watch
  `test_proposal_contract.py`'s four `PROPOSAL_SHAPED_GAPS` phrasings (§3B.4): they must get the *"could not
  match"* wording, never a claim that their request is unsupported.
- **Priority:** P1

### T04 — Prior work: `planIds` on the request, and the skip rule
- **Files:** `backend/app/schemas/plan.py` (`AIPlanRequest.planIds`),
  `backend/app/routers/scenes.py` (forward it), `backend/app/services/ai_plan.py` (`generate_proposals`
  computes the composed claimed set **before** `load_grounding` and passes it in),
  `backend/app/services/proposal_constraints.py` (`claimed_cells_for` gains an optional `plan_ids` so the
  staged plan and the toggled projects come from one call)
- **Depends on:** **the region branch must have landed** (§5) — the skip rule is the second line of the same
  pool pipeline. If it has not, land the schema/routing half (conflict-free) and hold the filter.
- **Priority:** P0 for the schema half; the filter is P0 but **gated**.

### T05 — Tests, then full regression
- **Files:** `backend/tests/test_sandbox.py` (preview cases), `backend/tests/test_proposal_contract.py`
  (prior-work and removal cases)
- **Depends on:** T01–T04
- **Must not regress:** `test_sandbox` 24/24 · `test_proposal_contract` 23/23 · `test_intent_routing` 14/14 ·
  `test_openapi_contract` · `test_contract` · frontend 98/98.
- **Priority:** P0

### Dependency graph

```mermaid
graph TD
  T01["T01 · preview service + POST /sandbox/preview<br/>+ openapi regen"] --> T02
  T01 --> T05
  T02["T02 · Preview action + 3-state render"] --> T05
  T03["T03 · /ai-plan refuses a removal (422)<br/>no vocabulary change"] --> T05
  T04A["T04a · AIPlanRequest.planIds<br/>+ claimed_cells_for(plan_ids)"] --> T04B
  T04B["T04b · pool skip rule in load_grounding<br/>GATED on feature/region-selection"] --> T05
  REG["feature/region-selection<br/>owns load_grounding / propose"] --> T04B
  T03B["T03B · unsupported-capability card<br/>in answer.py + chat_service"] --> T05
  T03 --> T03B
  GATE["GATE: test_sandbox 24 · test_proposal_contract 23<br/>test_intent_routing 14 · openapi · vitest 98"]
  T05 --> GATE
  classDef gated stroke-dasharray:4 3
  class T04B gated
```

---

## 7. Tests Phase 4 must add

### Feature A — preview (7)

| # | Test | Asserts |
|---|---|---|
| A1 | `test_a_preview_equals_the_run_it_previews` | Preview a candidate set, then apply it and run; the **ledger figures are equal**. This is the ruling's central claim, and it is the reason the preview must call the same functions rather than a parallel path |
| A2 | `test_a_preview_persists_nothing` | After a preview: the staged plan's pinned rows are unchanged, no new plan exists, `pins.staged_features` is identical. Guards the "transient plan" design that was rejected |
| A3 | `test_a_preview_writes_no_run_artefacts` | The count of files in `data/rasters/run/` is unchanged by a preview. Guards the history/prune hazard that ruled out `dryRun` on `/run` |
| A4 | `test_a_preview_is_labelled_and_carries_no_run_identity` | `status` is the preview constant (≠ `RUN_STATUS`), `warning` is non-empty, `scope` names what is excluded, and **`runId` / `overlayUrls` / `explanationCard` are absent keys** — not null, absent |
| A5 | `test_preview_delta_is_not_the_run_delta` | With prior work pinned, `previewDelta != runDelta`, both present, each with a `source` naming its operands. **The invariant-4 pin** |
| A6 | `test_a_preview_uses_the_input_card_values` | A candidate whose `storageDepthM` differs from the class default previews the **typed** number: change the value, the preview changes. Guards §1.6 |
| A7 | `test_a_preview_reports_a_skip_with_its_reason` | A candidate the run would refuse on the cumulative cap appears in `interventionsSkipped` with a non-empty `why` — **not** silently absent, and **not** an inflated total |

### Feature B — prior work (5)

| # | Test | Asserts |
|---|---|---|
| B1 | `test_a_removal_instruction_is_refused_by_ai_plan_not_inverted` | For each of `remove / delete / drop / take out`: `/ai-plan` returns **no proposal of that kind**. Today it returns a 1,876-cell `water_reservoir` (M3). This is the document's headline test |
| B2 | `test_a_removal_refusal_names_the_removal_route` | The refusal names `/sandbox/resolve-removal`, and (if 422) the status is 422. A refusal that does not say where to go is not actionable |
| B3 | `test_the_removal_capability_still_works` | `/sandbox/resolve-removal` still unpins a staged intervention and toggles off a project; the vocabulary pin is intact. Guards against "fixing" B1 by deleting the verbs (§3.3) |
| B4 | `test_the_proposal_path_skips_ground_that_is_already_placed` | Pin one candidate, re-ask: a **different, free** candidate is proposed — not an empty list (M1's asymmetry, fixed) |
| B5 | `test_the_proposal_path_accounts_for_a_toggled_project` | With a project toggled on and its `planIds` forwarded, `/ai-plan` excludes its cells. Impossible today: no field carries the selection (M2) |

### Feature B, part 2 — a missing capability (6, by §3B)

| # | Test | Asserts |
|---|---|---|
| C1 | `test_a_building_request_is_refused_not_substituted` | For each of `add a school`, `add 3 buildings`, `add a hospital`, `add an office block`: `/ai-plan` returns **no proposal at all** — and in particular **no `green_space` with a rationale naming the requested building**. Today `add a school` yields `green_space` 252 cells with *"the most suitable for adding a school"* (§3B.1). Mirrors B1 for the LLM leg |
| C2 | `test_the_substitution_is_still_reproducible_from_the_schema` | Drives `LLMProvider` with a **recorded transport** returning a selection for a building instruction, proving the substitution is a *server-permitted* response and not a stray model behaviour — i.e. that C2's forced closed-enum tool is the real cause. Pins the diagnosis so a later reader does not "fix" the model |
| C3 | `test_a_missing_capability_refusal_names_the_supported_set` | The `detail` contains **all five** `sorted(set(POOL_FOR_KIND))` kinds **and all six** massing ops, each read from its owning constant — and specifically **does not contain `flood_storage`** (§3B.3's correction). This is the anti-drift pin: the list must be generated, not typed |
| C4 | `test_a_synonym_phrasing_is_not_told_it_is_unsupported` | For each of the four `PROPOSAL_SHAPED_GAPS` phrasings: the message must **not** assert the request is unsupported, and **must** list `green space` / `water_reservoir` so the retry is obvious. Guards §3B.4's false positive — the failure that would be *worse than today's silence* |
| C5 | `test_the_unsupported_card_dates_the_cause_correctly` | With a **run already in scope**, `add a school` must not return `_empty_card`: `confidence.note` must not say *"No run in scope yet"* and `todo` must not say *"Press Run Simulation"*. Fails today, and it is an invariant-3 defect in its own right (§3B.5) |
| C6 | `test_the_guard_does_not_fire_on_a_question` | *"can you add a school?"* and *"what would happen if I added buildings?"* keep their normal card and are **not** refused by `/ai-plan`. The detector must not fire on an interrogative |

**Also pin one negative:** `test_history_does_not_change_a_proposal` — the same instruction with and without a
`history` payload yields identical proposals, pinning §2.4 (conversation awareness is *not* an input, and a
later contributor who "fixes" that must move this test deliberately).

**And one that must NOT move:** `test_intent_routing.py` 14/14. T03B changes a **card**, never the ladder —
if any of the 14 needs an edit, the change is wrong (§3B.5).

---

## 8. Residual risks I am not ruling away

1. **The preview and the run can diverge in the parts the preview declines.** The preview covers the
   level-pool ledger; the run additionally solves the temperature field, the routed cascade and the flood
   extent. Those are out of scope by §1.4(iii) and declared in `scope` — but a planner who reads a preview's
   storage figure and then sees a *flood extent* in the run has to hold two different scopes in their head.
   The mitigation is the `scope` sentence, repeated on the card, not a shared figure.
2. **The run's own `delta` is still scenario − today**, so "now" is not a figure either endpoint currently
   publishes. A planner comparing "preview vs run" will find the preview's `now` unfamiliar. That is honest —
   it is the state they are actually in — but it is new vocabulary and the UI must introduce it rather than
   assume it.
3. **`previewDelta` assumes nothing changes between preview and apply.** A concurrent pin, or a changed
   slider, invalidates it. The response must carry `rainfallMm` and the composed plan ids so a client can see
   whether the state it previewed is still the state it is confirming. I have not ruled on a staleness
   *invalidation* mechanism; if the lead wants one, it is a follow-up, not part of this change.
4. **Skipping changes which candidate is proposed, so a re-ask after a pin returns a *different* site.** That
   is the fix, and it is also a surprise: the user says the same sentence twice and gets a different site the
   second time. The `evidence` string must therefore always name *why this site*, and the response should say
   that a taken site was skipped — otherwise the new site looks arbitrary.
5. **The removal guard refuses a mixed add+remove sentence** (§3.2). That is deliberate and honest, but it is a
   behaviour change for a phrasing the browser currently half-handles (`isRemoval` fires on any removal verb,
   so today the addition half is dropped anyway). Neither behaviour is good; refusing is at least explicit.
6. **`resolve_removal` matches by name tokens and region** (`_in_region` uses centroid membership — the same
   rule the region ruling adopted, and consistent). It can therefore match nothing for a sentence a person
   reads as a removal, and its no-match message already says so. Not changed here; noted so it is not
   mistaken for a regression when B1 makes refusals more visible.
7. **A preview of a candidate that would be *skipped* is the most useful preview and the least tested.** A7
   covers it, but the fixture must deliberately overshoot the cumulative cap — a fixture that happens to fit
   would exercise nothing (the same trap `test_overlap_guard_...` documents).

### Added by §3B (the missing-capability case)

8. **The guard's false positive is the biggest risk in §3B, and it is silent.** The detector is *"creation verb
   present, no supported noun matched"*, and that is **also true of a synonym** (§3B.4). A user who typed
   *"plant a green corridor"* would be told their request is not recognised when the intent IS supported and
   only the noun is unfamiliar. The message's shape — every clause a fact about the system, never a claim
   about the sentence — is what keeps this honest, and **C4 is the test that holds it**. If a later contributor
   "sharpens" the wording into *"a school is not supported"*, they break this risk and C4 should fail.
9. **The substitution is masked in the browser, exactly like the removal inversion.** `route_tools` forces
   `select_intervention_sites` only on the `/ai-plan` path; the frontend's `isRemoval` already diverts
   removals, and a chat turn for *"add a school"* returns `ambiguous` and never reaches the gate. **So the
   substitution is reachable mainly through the API**, and it will survive a browser walkthrough — the same
   reason the removal bug did. C1 must therefore drive the endpoint directly, not the UI.
10. **A third entry to this family is likely, and the family is the finding.** Removal (a verb with no
   executor), capability (a request with no executor), and earlier `drop the trees` → three reservoirs (an
   unmatched noun falling back to a default kind) are three instances of one signature: *the executor cannot
   honour the instruction, and instead of declining it produces something from the pool.* I have ruled the two
   measured instances; I have **not** audited the vocabulary for further nouns that fall back
   (`EN_ALIASES` → `POOL_FOR_KIND` is the pair to check). That audit is worth one pass before the pitch and is
   not part of these tasks.
11. **Case (ii) of §3B.2 is an open invariant-3 gap I deliberately did not close.** A 200 response with
   `features: 0` carries no top-level `status`/`warning` (`AIPlanResponse` has neither), so the one legitimate
   "the model chose nothing" path is the only AI output in the system that is not labelled. Closing it needs a
   wire change and an openapi regeneration. **It should be its own decision**, and it should not be
   opportunistically folded into T03 — but it should not be forgotten either.
12. **`route_tools` forcing the site tool for a non-massing turn remains a latent trap.** §3B.7 rules against
   changing it *now*, because the guard makes it unreachable for unsupported requests. But any future entry
   point that reaches `route_tools` **without** passing through the `/ai-plan` guard would re-expose C1+C2
   instantly. The guard must live in the shared path, not only in the router, if a second caller appears.

---

## 9. RULING — four of six kinds are buried, and the shared style function is why

*(Added 19 Oct 2026 on the lead's measured report and the owner's console paste. This is in Phase 4's scope
because Feature A's preview renders through the function this ruling changes.)*

The owner: *"Changes to the paving is not visible."* Reported console line, verbatim:

```
cesium.js: Entity corridor, ellipse, polygon or rectangle with heightReference must also
           have a defined height.  heightReference will be ignored
```

### 9.1 What I verified, and how — executed, not remembered

I was asked not to rule from memory, so I did two things: read the installed Cesium's own resolution code, and
**execute it**.

**Cesium 1.146.0 is installed.** Node can load it, and **`GroundGeometryUpdater` is exported** — so its three
pure resolution functions can be called directly. This is the real implementation, not a paraphrase:

```
$ node -e 'const C=require("cesium"); const G=C.GroundGeometryUpdater, H=C.HeightReference; ...'
idiom                                  resolved.height  resolved.extrudedHeight  GeometryOffsetAttribute
CURRENT extruded (h=undef ref=NONE  eh=2 eref=NONE)     undefined   2         undefined
CURRENT plain    (h=undef ref=CLAMP eh=undef)           undefined   undefined undefined
A  both CLAMP    (h=0 ref=CLAMP    eh=2 eref=CLAMP)     0           "clamp"   TOP
B  both RELATIVE (h=0 ref=RELATIVE eh=2 eref=RELATIVE)  0           2         ALL
T1 ref=REL only  (h=0 ref=RELATIVE eh=2 eref=NONE)      0           2         TOP
T2 eref=REL only (h=0 ref=NONE     eh=2 eref=RELATIVE)  0           2         TOP
```

Read against the source that produced them (`getGeometryHeight`, `getGeometryExtrudedHeight`,
`computeGeometryOffsetAttribute`) the facts are these:

- **When `height` is not defined, `getGeometryHeight` emits the warning and returns `undefined`** — and then
  `computeGeometryOffsetAttribute` returns `undefined`, i.e. **no offset is applied at all**. The
  `heightReference` is not merely "ignored"; the geometry is built with no base height, which is absolute 0.
  **That is the burial, and it is the exact branch the owner's console line comes from.**
- With `CLAMP_TO_GROUND`, a defined `height` resolves to **`0`** and the real lift is deferred to
  `TerrainOffsetProperty`.
- **Only setting BOTH `heightReference` and `extrudedHeightReference` to `RELATIVE_TO_GROUND` yields
  `GeometryOffsetAttribute.ALL`.** Setting just one gives `TOP` (traps T1/T2) — so the natural half-fix, adding
  `heightReference` alone, does **not** put the base on the ground.

**Terrain, measured** (`load_grids().elevation_5m`): **min 3.41 m, max 17.29 m, mean 10.45 m** — the lead's
figures reproduce exactly. Buried fraction per kind:

| kind | `extrusionHeight` | cells whose ground is **above** the block's top |
|---|---|---|
| `permeable_paving` | 2.0 m | **100.0 %** |
| `road` | 2.0 m | **100.0 %** |
| `flood_storage` | 1.5 m | **100.0 %** |
| `drainage` | 3.0 m | **100.0 %** |
| `green_space` | 6.0 m | 98.2 % |
| `water_reservoir` | 14.0 m | 2.2 % |

**Four kinds are buried in every cell; a fifth in 98 %. The reservoir is the only one that reliably shows** —
precisely what the owner sees, and why this is not a paving-specific defect.

### 9.2 RULING: use the idiom this codebase already proved, not the Cesium-native one

**Sample the DEM per vertex, take the minimum, and set absolute heights — with no `heightReference` at all.**

```ts
const base = minOverRingVertices(sampleDemHeightClamped(lon, lat))   // metres, absolute
polygon.height = base
if (N) polygon.extrudedHeight = base + N
// and set NO heightReference on either
```

**This is not a new idiom — it is the one already working in this repository.** `footprintLayer.ts:63-83`
places all 396 buildings this way, with a comment explaining why. Reusing it is the point: a second answer to
"stand a polygon on this terrain" is a second thing to drift.

Three independent reasons, all verified, and two of them were **already recorded in this codebase**:

1. **The Cesium-native path discards our material.** `demProvider.ts:148-158` records it: Cesium routes clamped
   geometry through its ground-primitive path, *"which forces `PerInstanceColorAppearance` and so discards any
   custom material — silently deleting the tactical view's entire reason for existing."* `applyStyle` colours
   every intervention with a `ColorMaterialProperty`, and **`STYLE_FOR_KIND` colour is how the legend tells
   flood storage from tree planting.** A ground primitive would delete the legend. Decisive on its own.
2. **The native path is unstable under lazy tiles.** Same comment: `globe.getHeight()` *"returns `undefined`
   until the containing tile has loaded, and these tiles arrive lazily with the camera, so base heights would
   keep moving underneath the model."*
3. **The native offset is uniform, sampled at ONE point.** Read out of Cesium: `TerrainOffsetProperty` takes a
   single `positionProperty` and holds one `_terrainHeight`, and `GroundGeometryUpdater` passes it
   `this._computeCenter` — **one centre position**, applied across the whole geometry. On 3.41–17.29 m of
   relief a polygon's corners float or sink depending on where its centre lands. The footprint layer samples
   every outer-ring vertex and takes the **minimum** so *"sloped sites keep their uphill corner at ground level
   rather than the floor floating over the downhill one."*

**Why the minimum:** a base too low is *buried*, which reads as a block emerging from a slope; a base too high
*floats*, which reads as a bug. Minimum is also deterministic and already the convention here.

**A per-vertex drape is not an option at this layer** — it is only reachable through the ground-primitive path,
which reason 1 rules out. So the trade-off is stated rather than engineered around: **on steep relief a
polygon's uphill portion is partly buried.** For a depiction that is the correct direction to err.

**Both branches change.** The extruded branch is the reported one, but the non-extruded branch is also broken
(`height` undefined → the `CLAMP_TO_GROUND` is ignored → a flat patch at absolute 0 → buried). It is not dead
code either: `_feature`'s fallback style for an unrecognised kind is `{"color": "#8250df", "opacity": 0.6}`
with **no** `extrusionHeight`. Both branches get the sampled base.

### 9.3 `STYLE_FOR_KIND.extrusionHeight` means **metres above the ground**

**Relative — and no value changes; only the base moves.** The existing comment (*"permeable paving, drainage
and road are drawn as 2.5D slabs so the legend can read them"*) confirms relative was always the intent, and
the fix implements exactly that: `N` metres above the ground beneath it, where before it was `N` metres above
the ellipsoid.

One honest note the legend should carry: for `permeable_paving` and `road` a 2 m slab is a **legibility
convention, not a claim about built form** — real paving is a surface, not a 2 m platform. The sampled base
makes the blocks *visible*, not *true*.

### 9.4 The comment that must be replaced

`interventionLayer.ts:146-148` reads:

> *"Extrusion is set from absolute metres, so the polygon must NOT also be clamped to ground — otherwise the
> two offsets compound and the block floats."*

**Both clauses are wrong, and that belief produced this bug.** The two offsets cannot compound (`heightReference`
is per-geometry and is discarded entirely when `height` is undefined), and "absolute metres" is the confusion —
the style's metres were meant to be *above ground* and were being applied *above the ellipsoid*. It must be
replaced with a comment stating what Cesium actually does and why we sample instead. A wrong comment here
regenerates the bug.

### 9.5 RULING (test): a vitest unit test, and it needs no browser

**Verified rather than assumed** — in plain Node, no WebGL, no browser:

```
$ node -e 'const C=require("cesium"); const ds=new C.GeoJsonDataSource(); ds.load(fc,{clampToGround:true})...'
entities: 1 | polygon present: true
BEFORE applyStyle: height= undefined  extrudedHeight= undefined  heightReference= undefined
```

**`GeoJsonDataSource.load()` builds real entities outside a viewer**, so `applyStyle` can be driven from
vitest. Playwright is **not** needed and this does not wait on it.

1. **`interventionLayer.test.ts` — the regression guard.** For each kind in `STYLE_FOR_KIND`: load a
   one-feature collection, call `applyStyle`, and assert on the entity:
   - **`polygon.height` is defined** — the assertion that fails today, because the bug *is* a missing `height`;
   - `polygon.height` **≥ the minimum ground under that polygon**, sampled in the test through
     `sampleDemHeightClamped`, independently of the implementation;
   - where `extrusionHeight` is set, `polygon.extrudedHeight === polygon.height + N`;
   - **`polygon.heightReference` is never set to a non-`NONE` value** — the invariant that keeps the console
     warning from returning and keeps us off the ground-primitive path;
   - the **fallback style** (no `extrusionHeight`) also gets a base, covering §9.2's second branch.
2. **`interventionGround.test.ts` — the Cesium-contract canary.** Call
   `GroundGeometryUpdater.getGeometryHeight` / `getGeometryExtrudedHeight` /
   `computeGeometryOffsetAttribute` with our parameters and assert `resolved.height` is **not `undefined`**.
   Cheap protection against a Cesium upgrade changing semantics under us — a failure mode that is otherwise
   *literally a one-time console warning* no test ever sees.

**Not proposed:** a screenshot or pixel check. Both need a renderer, and the entity-property assertions catch
this bug earlier and more precisely. A browser harness is a different project (Playwright installation) and
this ruling does not depend on it.

### 9.6 Ordering: it becomes **T00**, and it blocks T02

`applyStyle` is exported and shared (`interventionLayer.ts:114-117`); `proposalLayer.ts` styles drafts through
it, differing only in the pre-transform (`opacity × 0.5`, `extrusionHeight × 0.4`). So a buried confirmed block
has an even more buried draft — a 2 m paving draft is **0.8 m**. **Feature A's preview would render nothing and
be unverifiable in a browser**, which makes this a prerequisite, not a neighbour.

- **T00 — ground every intervention polygon (this section).** Files: `interventionLayer.ts` (`applyStyle`, both
  branches, and the `:146-148` comment) and `demProvider.ts` (extract the shared
  `minGroundHeightOverRing(ring)` that `footprintLayer.ts:63-83` hand-rolls), plus the two new tests.
  **Blocks T02.**
- Because the base is computed inside `applyStyle`, **`proposalLayer.ts` needs no change** — the draft inherits
  the corrected base. That is the payoff of the shared function and why the fix belongs there, not in callers.
- **T02 gains one acceptance condition:** the preview's draft blocks must be visible — the feature is only
  verifiable after T00.

**Conflict with `feature/region-selection`: none.** This touches `frontend/src/view/**` and `demProvider.ts`
only — no `ai_plan.py`, no proposal selection. Parallel-safe with the region work and with T03.

### 9.7 Residual risks added by §9

13. **Extracting the shared ring sampler touches a layer that currently works.** `footprintLayer.ts` places 396
   buildings correctly today. The extraction must be behaviour-preserving, guarded by a test asserting the
   helper returns the **same base** as the current inline loop for a real building ring. If that guard is
   awkward, leave `footprintLayer` alone and accept two callers — a slightly worse codebase beats a regression
   in the only layer that currently works.
14. **`whenDemReady()` is load-bearing for the fix and `addInterventions` does not await it.**
   `SandboxViewer.tsx` awaits it for project models (`:915`, `:1031`) but the interventions effect (`:829-852`)
   does not. A layer added before the DEM decodes would sample `null`, fall back to 0, and be buried again —
   **the same bug, intermittently, which is worse than reliably.** T00 must await `whenDemReady()` before
   applying base heights, or re-apply on resolve, and the test must cover the pre-DEM case: the fallback must
   be a *stated* fallback, not a silent 0.
15. **The style heights are a depiction and the legend does not yet say so** (§9.3). Cheap to fix there;
   flagged rather than assumed.
16. **`clampToGround: true` is passed at `interventionLayer.ts:58` and `proposalLayer.ts:148`.** Empirically it
   does **not** set `heightReference` for polygons in 1.146 (the run above shows `heightReference === undefined`
   after a load with that option), so it is not a second source of the warning, and I have not ruled it
   changed. Noted so a future reader does not assume it is inert for every geometry type.

---

## 10. The `EN_ALIASES → POOL_FOR_KIND` audit (residual risk 10, discharged)

*(19 Oct 2026. Promised in §3B.7 risk 10 as "worth one pass before the pitch". Done exhaustively rather than
by sampling, and it found a **fifth** instance of the family — in the leg I had cleared.)*

### 10.1 The inventory, measured

`EN_ALIASES` holds **29 aliases across 8 kinds**. Three kinds have no pool:

| kind | aliases | `POOL_FOR_KIND` | router noun (`KIND_WORD`) | style |
|---|---|---|---|---|
| `water_reservoir` | 7 | ✓ | ✓ | ✓ |
| `green_space` | 6 | ✓ | ✓ | ✓ |
| `drainage` | 4 | ✓ | ✓ | ✓ |
| `permeable_paving` | 3 | ✓ | ✓ | ✓ |
| `road` | 3 | ✓ | ✓ | ✓ |
| **`flood_storage`** | **2** (`wetland`, `floodable park`) | **✗** | ✓ | ✓ |
| **`wind`** | **3** (`wind`, `windbreak`, `ventilation`) | ✗ | ✗ | ✗ |
| **`podium`** | **1** | ✗ | ✗ | ✗ |

Then the decisive sweep — **every** alias driven through `HeuristicProvider().propose("add a <alias>")` and
compared with the kind its own alias declares:

- **23 aliases produce the right kind.**
- **5 produce nothing:** `wetland`, `wind`, `windbreak`, `ventilation`, `podium`.
- **1 produces the wrong kind: `floodable park` → `green_space`.**

### 10.2 F1 — the fifth instance, in the **heuristic**, and I was wrong about that leg

In §3B.1 I cleared the heuristic: *"the heuristic is innocent — measured, it returns 0 features for every one
of the five building phrasings."* **That is true for buildings and it is not true in general.** Here is the
counter-example:

```
parse_intent("add a floodable park")   ->  [('green_space', 1), ('flood_storage', 1)]
HeuristicProvider().propose(...)       ->  green_space  |  "the largest contiguous vegetated block…"
```

`parse_intent` matches **every** alias as a **substring**, so `park` (→ `green_space`) matches alongside
`floodable park` (→ `flood_storage`); the flood-storage half has no pool, the green half does, and the user
asking for **a ponding feature gets a park** — with a plausible evidence string attached. **A floodable park is
a ponding feature; a green space is not one**, and nothing in the response says the request changed.

### 10.3 F2 — two parsers give two answers for one sentence, and only the guard hides it

```
_match_noun("add a floodable park")        ->  ('floodable park', 'flood_storage')   longest alias wins
is_unsupported_capability(...)             ->  True      (flood_storage ∉ POOL_FOR_KIND)
parse_intervention_intent(...)             ->  "add floodable ground in the whole area"
parse_intent("add a floodable park")       ->  [('green_space',1), ('flood_storage',1)]
```

So on **one sentence** the router says *flood_storage, which I cannot create* and the proposer says
*green_space, which I can*. **Today the guard masks this**, because T03 places it in the router **before**
`generate_proposals` — which is exactly residual risk 12, and this is the concrete instance proving it is not
hypothetical. `generate_proposals` and `HeuristicProvider.propose` are directly reachable (the LLM-rescue
fallback at `generate_proposals`, `generate_plan`, and every test that calls the provider directly). **Two
parsers disagreeing about one sentence is the "two opinions" pattern this project forbids for geometry; it is
no better for a noun.**

### 10.4 F3 — `flood_storage` is supported by five subsystems and uncreatable by the sixth

| subsystem | supports `flood_storage`? |
|---|---|
| `KIND_WORD` — the router's nouns | **yes** |
| `STYLE_FOR_KIND` — the legend's row | **yes** |
| `INTERVENTION_EFFECTS` — the ledger applies it | **yes** |
| `_DEPTH_KINDS` — the input card asks for its depth | **yes** |
| `siting.park_candidates()` — real candidates, `_sites(vegetated(), kind="flood_storage")` | **yes** |
| **`POOL_FOR_KIND` — proposable by `/ai-plan`** | **NO** |

And `suggest.suggest()` **can emit it** (measured: `suggest kinds = ['flood_storage', 'green_space',
'permeable_paving', 'underground_tank', 'water_reservoir']`). So **the suggestion route can create a floodable
ground that the chat route never can** — which contradicts `cbd4cef`'s own claim that both routes *"end in one
place"*. The ledger, the legend, the input card and the candidate builder all agree the capability exists; one
dictionary disagrees.

### 10.5 RULINGS

**R-A — add the `flood_storage` pool. Do not withdraw the advertisement.**
One `POOL_FOR_KIND` entry plus one list on `Grounding`, built from the material that **already exists**
(`siting.park_candidates()`). Why this direction:
- The capability is real and already shipped in **five of six** subsystems, including the suggestion route.
  Withdrawing it would mean deleting a legend row, a working suggestion kind, and two aliases — and the aliases
  are on the **three-way vocabulary pin**, so the deletion would propagate to the client.
- Adding it **removes a whole instance of the family**: the router can no longer advertise a noun the proposer
  cannot serve, and `is_unsupported_capability` stops calling `wetland` / `floodable park` unsupported.
- `CHOOSE_TOOL`'s enum is `sorted(set(POOL_FOR_KIND))`, so it grows by itself — no second edit to keep in sync.
- The region filter (`sites_in_region`) and the §2.2 claimed filter apply to the new pool automatically,
  because both narrow whatever `load_grounding` produced.

**R-A carries one extra line, found by the §11.1 enumeration: re-point `EN_ALIASES["flood storage"]` at
`flood_storage`.** It currently maps to `water_reservoir`, so `add flood storage` returns a 14 m blue reservoir
where the user asked for a flat floodable park — the sixth instance of the family, and one that neither R-A's
pool nor R-B's rule would fix on its own (§11.1). It is legal only in the same commit as R-A, because before
R-A it would point at a kind with no pool.

**CONSEQUENCE — this is a coordination item, and R-A breaks _two_ tests, not one.** Both were written by the
T03/T03B implementer and both are **correct for the state they encode**; each must be retired **in the R-A
commit itself, by the same author**, so the red suite has exactly one owner.

1. **C3 flips.** `assert "flood_storage" not in detail` and `assert KIND_WORD["flood_storage"] not in detail`
   encode §3B.3's correction, which was right **for as long as the pool was absent**. C3 becomes: the message
   names every `POOL_FOR_KIND` kind — including floodable ground, once it is one — and omits nothing it can
   create.
2. **C4 loses its non-vacuity assertion, and this one is easy to miss.** C4 ends with
   `assert refused >= 1` over `PROPOSAL_SHAPED_GAPS`. **Measured: exactly one member of that list fires the
   guard today (`create a wetland in the east`), and R-A drops `flood_storage`'s absence, so it becomes zero.**
   The assertion then fails — deliberately, with the message *"say so rather than passing vacuously"*. The fix
   is **not** to delete the assertion: C4 must gain a **genuinely unrecognised synonym** (a noun that is in no
   alias), so the guard stays exercised. Deleting it would convert a live guard test into a vacuous pass, which
   is the failure mode the assertion exists to prevent.

**So R-A's commit touches: `POOL_FOR_KIND`, `load_grounding`, `parse_intent` (R-B), `EN_ALIASES["flood
storage"]` (R-6), the `instructions` dict (R-C), **and both C3 and C4**.** That list is why it cannot be split
across two authors.

**R-B — fix the parser disagreement; do not rely on the guard's placement.**
`parse_intent` must resolve **one kind per noun occurrence, longest alias wins** — the same rule `_match_noun`
already uses — so the proposer and the router cannot answer differently about one sentence. Reasons: the guard
sits at a single point (risk 12, now proven); F1 is reachable wherever the provider is called directly; and a
"will this be created?" question must have one answer. This is the *root* fix for F1; the guard is the
belt-and-braces.

**R-C — a regression flag that will otherwise be discovered by a red suite.**
`test_every_proposal_kind_round_trips_through_ai_plan` builds a **kind-keyed** dict —
`instructions = {kind: sentence}` for the five pool kinds (lines 322-330) — and then does
`for kind in sorted(ai_plan.POOL_FOR_KIND): instructions[kind]`. **Adding `flood_storage` to `POOL_FOR_KIND`
raises `KeyError: 'flood_storage'`** — it does not fail an assertion, it crashes the suite. R-A must add the
kind **and** a phrased instruction for it (e.g. `"add a wetland in the north"`) in the same commit.

**R-D — `wind` (3 aliases) and `podium` (1) need no change.**
They are not router nouns, not pools, not styles, so `_match_noun` returns `None` and §3B's guard fires with
the capability statement — correct behaviour for a capability that genuinely does not exist. They remain a
*latent* hazard only in the sense R-B covers: `parse_intent("add a windbreak")` still returns `[('wind',1)]`
and silently yields nothing if ever called without the guard.

### 10.6 What this audit cost, and what it was worth

One pass, exhaustive over 29 aliases and 8 kinds — not sampled. It found: **one live substitution in the leg I
had cleared** (F1, and I have corrected §3B.1's scope rather than quietly widening it), **one proof that a
guard's *placement* is load-bearing** (F2/risk 12, previously speculation), **one capability shipped in five
places and missing from a sixth** (F3), and **one crash-in-waiting in a kind-keyed test** (R-C). The lead's
instinct that an unaudited fallback "is exactly the kind of thing that survives a walkthrough" was right: F1
would survive one, because the proposal it returns is legal, in-bounds, well-sourced and the wrong feature.

---

## 11. The closure analysis, and the R-A / R-B / R-C task spec

*(19 Oct 2026, in answer to the lead's two questions: does R-B **provably** close the family, and should R-B be
folded into the region task? The answers are measured, and one of them is "no, it does not".)*

### 11.1 The enumeration, and a sixth instance

The multi-match mechanism (F1) can only fire where **one alias is a substring of another**. That relation is
finite and I enumerated it exhaustively rather than sampling — **6 pairs across 29 aliases**:

| shorter alias | kind | inside | kind | cross-kind? |
|---|---|---|---|---|
| `drain` | drainage | `drainage` | drainage | no |
| **`park`** | **green_space** | **`floodable park`** | **flood_storage** | **YES** |
| `basin` | water_reservoir | `retention basin` | water_reservoir | no |
| `basin` | water_reservoir | `storage basin` | water_reservoir | no |
| `reservoir` | water_reservoir | `water reservoir` | water_reservoir | no |
| `wind` | wind | `windbreak` | wind | no |

**Exactly one of the six crosses kinds.** So `add a floodable park` is not one of several F1s — it is *the
only* one the current vocabulary can produce. That matters for the closure argument below.

**And the enumeration found a 6th instance that no rule in this document closes.** `EN_ALIASES` maps **two**
flood-storage nouns to **`water_reservoir`**:

```
flood storage   -> water_reservoir   (pool: reservoir_sites)   <-- hand-authored
storage basin   -> water_reservoir   (pool: reservoir_sites)   <-- fine: a basin is a basin
floodable park  -> flood_storage     (pool: None)              <-- correct noun, no pool
wetland         -> flood_storage     (pool: None)              <-- correct noun, no pool
```

So **`add flood storage` returns a 14 m blue `water_reservoir`** where the user asked for a flat green
floodable park — a different land-class `to` (`water` vs `floodable`), a different legend colour, and a
different storage depth (the 1.5 m `water` default vs the 0.3 m `floodable` one). **There is no collision
here**, so R-B's rule does not touch it, and R-A's pool does not either: the alias simply points at the wrong
kind. It is a plain mapping error, and it is closed by re-pointing the alias — one line, in the same commit as
R-A, because only after R-A does `flood_storage` have somewhere to go.

**Also corrected: instance 3 is no longer reproducible.** `docs/TEST_PLAN_intervention_loop.md` records
`drop the trees` → three water reservoirs. On this HEAD `parse_intent("drop the trees")` returns `[]` and the
heuristic returns `[]` — the vocabulary has moved since. I had counted it as live in this doc; it is history.
The honest live count is **five**.

### 11.2 Does R-B **provably** close the family? No — it closes two of four mechanisms

This is the answer the lead asked for, and the useful answer is the partition, not a verdict.

| mechanism | what generates it | closed by R-B? | **provable?** |
|---|---|---|---|
| **A. multi-match** | one alias a substring of another (F1) | **yes** | **YES — provably.** The surface *is* the substring relation; it is finite, I enumerated all 6 pairs, exactly 1 crosses kinds, and longest-wins resolves it to the more specific phrase. It is a **one-line test over two literal dictionaries**: assert no cross-kind substring collision exists. A future alias cannot reintroduce it silently. |
| **B. routed kind with no pool** | `EN_ALIASES ∩ KIND_WORD ⊄ POOL_FOR_KIND` (`wetland`, and after R-A nothing) | **yes**, via R-A | **YES — provably.** Both sides are literal constants, so `{k for k in EN_ALIASES.values() if k in KIND_WORD} ⊆ set(POOL_FOR_KIND)` is an exhaustive check by construction, and it is a one-line test. |
| **C. no alias at all** | nothing matches → the LLM leg substitutes (§3B.1) | **no** | **NO.** Closed *at the router* and open *anywhere else*. It is a property of **which entry points pass through the guard** — a call-graph fact, not a vocabulary fact. No change to `parse_intent` can close it. |
| **D. removal verb** | the executor has no removal op (§3) | **no** | **NO.** Same reason: it is the guard's placement, not the vocabulary. |

**And instance 6 is closed by neither R-A nor R-B** — it is a hand-authored alias mapping, outside every
mechanism.

**So the honest statement is: R-B provably closes mechanism A; R-A provably closes mechanism B; mechanisms C
and D are closed only where the guard is reachable; and instance 6 is closed by a third, separate one-line
change.** "We fixed the ones we found" is **not** the same as "this class is closed", and I am deliberately not
writing the second. The strongest honest claim available is: *two of the four mechanisms are closed with an
exhaustive guard test, one instance is closed by an alias edit, and the remaining two mechanisms are structural
— they can regress by adding a caller, which is why the guard must eventually live in the shared path rather
than only in the router (risk 12).*

### 11.3 Should R-B be folded into the region task? **No — and the reason is measured, not procedural**

The lead offered this as a real choice. **R-A and R-B must land in ONE commit, in that order, and neither can
ride the region task.** Measured:

```
heuristic("add a floodable park")  before R-A:  ['green_space']                    (1 proposal — wrong)
heuristic("add a floodable park")  after  R-A:  ['flood_storage', 'green_space']   (2 proposals — one right, one wrong)
```

**R-A alone makes the defect worse.** Once `flood_storage` has a pool, `parse_intent`'s two kinds *both*
resolve, and the user asking for a floodable park receives **two interventions — a floodable ground and a green
space** — on overlapping ground. That is worse than one wrong kind: it is a silent double-proposal, and the
second one then trips the overlap guard or the cumulative cap and produces a refusal the user cannot explain.

So the ordering is forced:

1. **R-B's rule must land with, or before, R-A's pool** — never after.
2. **They must be one commit**, because either alone is a regression: R-B alone turns a wrong proposal into a
   *silent nothing*; R-A alone turns one wrong proposal into **two** proposals.
3. **Neither can be folded into the region task**, for the lead's own reasons plus one more: R-A moves the gate
   count the region engineer is targeting (23 → 30), and **R-C crashes `test_every_proposal_kind_round_trips_through_ai_plan` with a `KeyError`** until a phrased instruction is added in the same commit. Two people editing toward two different gate totals in one file is how a suite goes red with no attributable cause.

Also: R-B's correctness argument **depends on R-A's pool existing** (that is the whole point of point 1), so
R-B cannot be specified as a self-contained change for someone else to carry — it is a *pair*.

### 11.4 The task spec — **sequenced after the region branch lands**

**Task R — close mechanisms A and B (`POOL_FOR_KIND` / `parse_intent`), one commit.**
- **Branch:** cut from the region branch **after it lands**. Not before, not in parallel.
- **Precondition, and it is a hard gate:** the region branch's `load_grounding(region=None, claimed=None)`
  shape must be in place. **I do not pin line numbers for `parse_intent` in this spec** — the region engineer is
  rewriting it (`parse_intent_counts`, line numbers moving) and an anchor written against today's file would be
  wrong by the time it is used. The spec below is written as **invariants and tests** for that reason, and the
  implementer pins the anchors at the moment they start.
- **R-A — the pool.** Add `flood_storage` to `POOL_FOR_KIND`, and give `Grounding` the matching list built from
  the material that already exists (`siting.park_candidates()`, which returns
  `_sites(vegetated(), kind="flood_storage", why=…)`). Build it inside `load_grounding` so the region filter and
  the §2.2 claimed filter apply to it automatically — a pool built anywhere else would silently bypass both.
  Consequence to expect: `CHOOSE_TOOL`'s enum grows by itself (`sorted(set(POOL_FOR_KIND))`), and
  `is_unsupported_capability` stops calling `wetland` / `floodable park` unsupported.
- **R-B — the rule.** `parse_intent` must resolve **one kind per noun occurrence, longest alias wins** — the
  same rule `_match_noun` already uses. The implementation anchor is to be pinned against the region
  engineer's landed `parse_intent`, and must not be written against the current file.
- **R-6 — the alias (closes instance 6).** Re-point `EN_ALIASES["flood storage"]` at `flood_storage`. Only
  legal in the same commit as R-A, because before R-A it would point at a kind with no pool.
- **R-C — the test that R-A will crash.** `test_every_proposal_kind_round_trips_through_ai_plan` builds a
  kind-keyed `instructions` dict and indexes it while iterating `POOL_FOR_KIND`; a sixth kind raises
  **`KeyError`**, not an assertion failure. The kind **and** a phrased instruction (e.g.
  `"add a wetland in the north"`) must be added together.
- **The two closure guards (this is the part that makes A and B *provable* rather than merely fixed):**
  1. `test_no_cross_kind_alias_collision` — for every ordered pair of aliases, if one is a substring of the
     other their kinds must be equal. Exhaustive over two literal dictionaries, so it holds for every alias
     that will ever exist, not just today's 29.
  2. `test_every_routed_kind_has_a_pool` — `{k for k in EN_ALIASES.values() if k in KIND_WORD} ⊆
     set(POOL_FOR_KIND)`. Same property: exhaustive by construction.
- **C3 and C4 both move in this commit, same author.** C3's `assert "flood_storage" not in detail` encodes
  §3B.3's correction, which was correct **while the pool was absent**, and R-A retires it deliberately. C4 is
  the easy one to miss: its closing `assert refused >= 1` over `PROPOSAL_SHAPED_GAPS` is currently satisfied by
  **exactly one** member (`create a wetland in the east`) and R-A drops that to **zero** — so C4 needs a
  genuinely unrecognised synonym, **not** the deletion of the assertion, or a live guard test becomes a vacuous
  pass. Measured counts and both assertions are in §10.5. This is also why the lead's instruction to the
  T03/T03B engineer to leave C3 alone for now is right: a test that is correct-today-and-retired-later should
  be retired by the change that retires it.
- **Must not regress:** `test_intent_parsing_reads_counts` (`parse_intent`'s outputs for the counted cases),
  `test_intent_routing` 14/14, `test_every_proposal_kind_has_a_defined_ledger_effect` (`flood_storage` has an
  `INTERVENTION_EFFECTS` entry, so it should pass), and the gate total the region branch establishes.
- **Explicitly NOT in this task:** mechanisms C and D. They are structural (call-graph), they are already
  covered by the §3B and §3 removal guards at the router, and the honest fix is risk 12 — move the guard into
  the shared path — which is a separate decision.

---

## 12. Conventions for implementers — recorded because they cost someone a red suite

*(Both found by the T03/T03B implementer during `37c90d3` + `5735bf2`. Recorded here because this doc is what
implementers read, and its repeated "no schema change" wording is what makes the first one easy to get wrong.)*

**C1 — A route handler's docstring is a wire field, not a comment. Editing it is a wire change.**
In FastAPI the docstring of a route function **becomes** the OpenAPI `description`, so
`contracts/openapi.json` changes and `test_openapi_contract` — which regenerates the contract in memory and
compares byte-for-byte — **goes red**. Measured: the implementer's first cut added the §3B rationale to
`create_ai_plan`'s docstring, the suite went red, and moving the same prose into an ordinary code comment left
`contracts/openapi.json` byte-identical.

**Why this belongs in the doc and not just in a brief:** several sections here say *"no schema change"* when
they mean *"no Pydantic model changes"*. A reader is entitled to conclude that prose on a route is free. **It is
not.** So: a ruling that says "no wire change" permits an ordinary comment and **forbids a docstring edit on a
route handler**; if a route docstring genuinely must change, the same commit regenerates
`contracts/openapi.json`. This is the one place where "documentation" and "the contract" are the same artifact.

**C2 — Use `/tmp/v_pin/bin/python`; do not assume a virtualenv exists in the worktree.**
`backend/.venv` exists **only in the main tree** (`T1/`). The worktrees have none, so
`./.venv/bin/python` is a "no such file" — not a stale-pydantic problem. Run tests and any one-off script with
`/tmp/v_pin/bin/python`, from the target worktree's `backend/` directory so `app` resolves to that branch's
code. (The `T1/backend/.venv` interpreter also works for read-only auditing against another worktree's tree —
`cd <worktree>/backend && /path/to/T1/backend/.venv/bin/python -c …` — because `app` resolves from the cwd,
which is how §9 and §10 were measured.)

**C3 — In a shared worktree, `git add` and `git commit` must be ONE command.**
The main tree and the worktrees are separate checkouts of **shared branches**, and several people commit into
them concurrently. A change that sits **staged** between one command and the next is visible to anybody else's
`git commit`, so it rides along under their message. Measured, in this very file: a 108-line edit to this
document was staged, a teammate committed their test change, and **the doc edit landed inside
`ad2ae5a test(proposal-contract): C4 exercises an unlearned synonym`** — content intact, description wrong,
and an innocent-looking commit that appears to have been written by someone who did not write it.

Nothing was lost and **history was not rewritten** (QA was running; rewriting a shared branch to fix
provenance is a worse trade than recording it). But the rule is worth carrying: **`git add <file> && git
commit …` in a single invocation**, never staged across commands. This is the same discipline §11.4 demands of
R-A's two-test retirement for the opposite reason — there, two authors would leave a red suite with no owner;
here, one staged file left a commit with no author.