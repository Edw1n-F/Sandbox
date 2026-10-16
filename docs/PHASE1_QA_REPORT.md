# Phase 1 QA Report — intervention loop, three reds

**Reviewer:** Yan (QA) · **Date:** 2026-10-17 · **Round:** 1 of max 2
**Under test:** uncommitted working tree on `feature/intervention-loop`, base `2ad7176`
**Scope:** `backend/app/services/{proposal_constraints,sandbox_model,ai_plan}.py` (+277 / −14)
**Method:** every suite re-run by me; every claim re-measured; nothing accepted on assertion.

---

## 0. Verdict summary

| Red | Change | Verdict |
|---|---|---|
| **Red 1** | `building_mask` = union of bbox + polygon-exact raster, **plus** the contour clip guard in `validate_proposal`'s site path | **SOUND WITH CAVEAT** — union and guard both do what they claim; but the guard silently drops cells and under-reports `clippedCells` (D1), and the engineer's own comment that it "is a no-op on the current data" is **false** |
| **Red 2** | `drainage` + `road` added to `INTERVENTION_EFFECTS` as `{"to": None, "why": ...}` | **SOUND** — verified end-to-end; no number claimed, no reason softened |
| **Red 3** | `_split_under_cap()` — row-coherent banding + 4-connected components within each band | **SOUND WITH CAVEAT** — geometry, legality and determinism all verified; but the `evidence` provenance string **mislabels one of the three emitted sub-sites** (D2), which is the project's stated worst failure mode |

**Two real defects found (D1 blocking-ish, D2 must-fix), zero regressions, zero weakened
honesty labels.** No combined score or verdict was introduced anywhere. The engineer did
not weaken a `warning`, `status` or `reasonCode` to make a test pass — the diff removes
**no** honesty string at all.

---

## 1. Measured suite scores (all re-run by me)

Every number below is my own run output, not the engineer's report.

| Suite | Claimed | **Measured** | Result |
|---|---|---|---|
| `test_proposal_contract` | 18/18 | **18/18** | ✅ confirmed |
| `test_grounding_pools` | 15/15 | **15/15** | ✅ confirmed |
| `test_ai_plan` | 40/40 | **40/40** | ✅ confirmed |
| `test_ledger` | 42/42 | **42/42** | ✅ confirmed |
| `test_intent_routing` | 14/14 | **14/14** | ✅ confirmed |
| `test_run` | 49/49 | **49/49** | ✅ confirmed |
| `test_scenario` | 65/65 | **65/65** | ✅ confirmed |
| `test_project_catalog` | 9/9 | **9/9** | ✅ confirmed |
| `test_suggest` | 11/11 | **11/11** | ✅ confirmed |
| `test_pins` | 18/18 | **18/18** | ✅ confirmed |
| `test_published_scenarios` | 28/28 | **28/28** | ✅ confirmed |
| `test_intervention_geometry` | all-pass | **all checks passed** | ✅ confirmed |
| frontend `npx vitest run` | 98/98 | **98/98** (10 files, 100.9 s) | ✅ confirmed |

### 1a. `test_overlay_masking.py` — the false pass, resolved

The lead was right to flag this. Run as a script it prints **nothing** and exits 0 — it has
no `__main__` runner (`grep -c __main__` → 0), so `./.venv/bin/python tests/test_overlay_masking.py`
is a **vacuous pass**, not evidence of anything.

I drove its three `test_` functions through an explicit import harness instead. Real result:

```
PASS  test_mask_is_boolean_over_the_grid
PASS  test_rendered_flood_is_transparent_under_buildings
PASS  test_depth_data_is_not_masked
```

**3/3 genuinely pass.** So this file is *verified*, not *unverified* — the earlier 0-checks
reading was an artefact of the runner, not a real gap. Two notes:

- These three tests exercise **`_overlay_common.building_footprint_mask`** (the legacy bbox
  raster), *not* the new `proposal_constraints.building_mask`. So they provide **zero
  coverage of Red 1's union**. Nothing in the suite pins the union's two-sided behaviour.
- CI compensates: `.github/workflows/ci.yml:89-96` explicitly detects the missing `__main__`
  and runs that file under `python -m pytest -q`. So CI is correct here; only the local
  script-run convention is misleading.

**No test was modified by the engineer.** `git diff -- backend/tests/` is empty; the
contract suite was last touched in `4e86b65`. The engineer fixed source, not tests. Good.

---

## 2. Section B — adversarial attack on Red 1

### 2a. Is the gate mask really a superset of the pool filter? **Measured: YES.**

Grid is 117 × 148 = 17,316 cells. `MIN_SITE_CELLS=40`, `MAX_INTERVENTION_COVERAGE=0.25`,
so `maxSingle = floor(0.25 × 17316) = 4329`.

| Mask | Cells |
|---|---|
| envelope (legacy bbox — what `load_grounding` subtracts) | 7,789 |
| exact (polygon-centre) | 3,263 |
| **gate = union** | **7,812** |

- `gate == envelope | exact` → **True** (bitwise identical)
- `cells the pool thinks free but the gate calls building` → **23**
- `cells the gate thinks free but the pool calls building` → **0**
- `(gate >= envelope).all()` → **True**

The engineer's stated safety argument **holds, and is stronger than claimed**: the union is
not merely "a superset of the pool filter", it is a superset by exactly the 23 polygon-exact
building cells the bbox raster misses. `envelope & ~exact` = 4,549 cells — the bbox raster
claims 4,549 cells that are not buildings, confirming the docstring's "it claims courtyards
and gaps" claim with a number. The asymmetry is real and the direction is right.

### 2b. Does any candidate pool go EMPTY under the stricter gate? **No.**

Every one of the nine emitted pool sites survives with room to spare:

| Pool site | served cells | overlap w/ **gate** | overlap w/ pool filter |
|---|---|---|---|
| `reservoir_sites[0]` | 1,878 | **2** | 0 |
| `reservoir_sites[1]` | 468 | 0 | 0 |
| `reservoir_sites[2]` | 83 | 0 | 0 |
| `green_sites[0..2]` | 252 / 154 / 126 | 0 | 0 |
| `paving_sites[0]` | 3,596 | **16** | 0 |
| `paving_sites[1]` | 649 | 0 | 0 |
| `paving_sites[2]` | 1,836 | 0 | 0 |

No pool is empty, and the two that carry building cells (2 and 16) are exactly the case the
guard exists for. **The engineer filed this correctly.**

### 2c. ⚠️ FINDING D1 (deferred-blocking): the clip guard silently drops cells and under-reports them

**The engineer's inline comment is factually wrong.** `proposal_constraints.py:891-894` says:

> *"This is a no-op on the current data (every pool component contours `covered == enclosed`
> and none carries a building cell)"*

**Measured: false.** The guard fires on **2 of 9** pool sites today —
`reservoir_sites[0]` clips **2** cells, `paving_sites[0]` clips **16**. Every comment
sentence after "This is a no-op" is wrong about current data. That matters because the
comment is what a future reader would trust to decide the path is untested.

**More seriously, the guard can drop a large fraction of a site while reporting almost none
of it.** `contour_ring` returns only the **largest** ring (`intervention_geometry.py:516-529`,
`best = rings[0]`). When subtracting buildings **splits** the survivors into two pieces, the
guard serves the larger piece and **silently discards the smaller one** — while `clippedCells`
counts only the building cells it removed.

Reproduction (synthetic, 30×30 candidate block, a 2-cell-wide building stripe through the middle):

```
candidate block            = 900
true survivors             = 840   (two 420-cell pieces)
building cells in candidate=  60
status                     = PROPOSAL_ACCEPTED
served                     = 420   <-- only the largest piece
RECLAIMED building cells   =   0   (the safety property HOLDS)
silent loss                = 420   <-- real, legal, servable ground dropped with no record
constraints.clippedCells   =  60   <-- reports 60, not 480
constraints.clippedFraction= 0.0
warning                    = "…contoured cell mask of the chosen site…"  (no mention of a shrink)
```

**What is honest here:** `size.cells` = 420 matches the served geometry exactly. The
**safety** property holds — in every synthetic and real case I probed, the re-contour never
closes back over an excluded building cell (`RECLAIMED = 0` in all of A/B/C/D below).

**What is not honest:**
1. `clippedCells` = 60 under-reports the real change to the served polygon (480 cells
   differ: 60 buildings + 420 dropped survivors). An auditor computing
   `candidate - served` gets 480 and the wire says 60. The design's own stated purpose for
   this field — *"which is what makes `constraints.clippedCells` auditable"* — is defeated.
2. The `warning` never mentions the shrink. `PRD_proposals.md:86-91` (US1) promises
   *"shrunk — 210 cells removed to clear existing buildings; 1,860 kept"*. On this path the
   user gets **no shrink annotation at all** — the row just silently describes smaller
   geometry. Note `_accepted_warning` only emits the clip sentence when
   `coordinate_based` is true, so the site path can never say it.

**Severity: real, but reachability-limited.** On today's data the loss is 2 and 16 cells
(0.1% / 0.4%) and the split case is synthetic. It becomes user-visible the moment a pool
component is genuinely bisected by a building — which is precisely the "newly-filtered pool"
future the guard was written for. I rate this **must-fix before the guard is relied on, not
blocking the three reds** — it is a reporting defect, not a safety defect.

Note also the guard's reject branch is honest in the right place: with 19 survivors
(< 40 floor) it returns `PROPOSAL_REJECTED` / `BUILDING_OVERLAP` (a frozen reason code),
`geometry=None`, and a warning naming the 81 building cells. That branch is correct.

### 2d. Can the re-contour close back over excluded cells? **No — measured across four shapes.**

I probed the guard with the candidate as the **full** block and buildings punched out (the
only way to actually exercise it):

| Case | shape | reclaimed building cells | over-claim | silent loss |
|---|---|---|---|---|
| A | solid 30×30, central 12×12 building → annular survivors | **0** | 0 | 0 |
| B | building stripe bisecting the block | **0** | 0 | **420** |
| C | 10×10 block, 9×9 building → 19 survivors (< floor) | **0** | 0 | (rejected, correct) |
| D | 3-cell-thick picture frame (crenellated ring) | **0** | 0 | 0 |

In A the served set is **exactly** the survivor set (756 = 756, `served == survivors` as
sets) — the hole is preserved as a hole and not back-filled. The marching-squares
saddle-resolution work in `intervention_geometry` does its job. **The safety claim is
sound; only the reporting is not.**

### 2e. `occ is None` (no city model) — the guard correctly does not fire

With `occupied=None`: `status=PROPOSAL_ACCEPTED`, `buildingsClear=False`,
`warning` contains `BUILDING_CHECK_UNAVAILABLE`, `clippedCells=0`. The design's "no data is
not no buildings" rule is preserved on the new path. `building_mask` returns `None` when
`buildings_hsk.json` is absent (`path.exists()` → `None`; `envelope is None` → `None`), and
the `None` contract is honoured by `_occupied_cells`. ✅

---

## 3. Section C — adversarial attack on Red 3 (`_split_under_cap`)

### 3a. Served geometry legality — every emitted sub-site measured through `_polygon_cells`

`maxSingle = 4329`, `MIN_SITE_CELLS = 40`.

| Sub-site | served cells (rasterised) | declared `pavedCells` | in `[40, 4329]`? | declared == served? | coverage |
|---|---|---|---|---|---|
| `paving_sites[0]` | **3,596** | 3,596 | ✅ | ✅ | 0.20767 |
| `paving_sites[1]` | **649** | 649 | ✅ | ✅ | 0.03748 |
| `paving_sites[2]` | **1,836** | 1,836 | ✅ | ✅ | 0.10603 |

All three are legal **on the served polygon**, not merely on the input cell count — which was
the specific risk. `paving_sites[0]` at 20.8% of the grid is the binding constraint and it
clears the 25% cap with headroom. The recomputation of `pavedCells`/`pavedAreaHa` from the
band's own cells is what makes this true; had the parent's 6,821 been carried over, all
three rows would have been untruthful.

### 3b. Do sub-sites overlap each other? **No.**

Pairwise intersections among the three emitted paving sub-sites: **0 cells** total. They are
bands of one component split at distinct row ranges (band 0 → rows 0..76, band 1 → rows
77..116), so disjointness is structural. ✅

For completeness: paving sub-sites **do** overlap the reservoir/green pools
(`paving[0]∩reservoir[0]` = 1,226 cells, etc.), but that is pre-existing and correctly
handled — `validate_batch`'s claimed-set loop refuses the later one with `OVERLAPS_STAGED`
naming `proposal#n`. Verified: 3 accepted / 6 rejected in a full-batch run, with
`overlapsWith` populated. Not a defect.

### 3c. Determinism — three independent proofs

1. **`load_grounding()` twice, full fingerprint diff** — I compared served cell sets,
   `metrics` (JSON, key-sorted), `evidence` strings and `notes` across two runs:
   **identical**. ✅
2. **Shuffle-invariance** — `_split_under_cap` fed the same 300 random cell sets in
   shuffled order: **0 mismatches**. The band walk depends only on the cell *set*, never on
   input order.
3. **Sort totality** — the sort key `(-size, min_r, min_c)` was brute-forced over **20,000**
   random 7×7 masks: **0 collisions**. So no tie can fall back to set/dict iteration order.
   (`connected_components` also returns a stable order across repeated calls.)

No `time`, no `random`, no `datetime` anywhere in the added lines. ✅

### 3d. `bandIndex` reproducibility

`bandIndex` is the index of the row-band in the ascending-row walk — deterministic by
construction, and exposed in `params` so a client can cite it. On this data:
`paving_sites[0].bandIndex=0`, `[1].bandIndex=0`, `[2].bandIndex=1`; the untruncated split
produces 4 sub-sites `[3596, 649, 1836, 671]` across bands `[0,0,1,1]`, and `[:3]` keeps the
first three. Only one paved component exists (size 6,821), so no component is entirely
dropped by the truncation. ✅

### 3e. ⚠️ FINDING D2 (must-fix): the `evidence` string misdescribes `paving_sites[1]`

This is the question the lead asked me to judge explicitly. My verdict: **the string is not
merely imprecise — for one of the three emitted rows it is false, and it is exactly the
"looks sourced but does not describe the served geometry" shape the project treats as its
worst failure mode.**

The string is now:

> `"largest 4-connected impervious block within row band %d of the largest paved component …"`

Band 0 contains **two** sub-sites: 3,596 and 649 cells. The sort is `(-size, min_r, min_c)`,
so **both** are emitted, and **both** carry the identical string *"largest … within row band
0"*. Measured rank-in-band:

| Sub-site | band | size | rank in band | string says "largest" |
|---|---|---|---|---|
| `paving_sites[0]` | 0 | 3,596 | 1 of 2 | ✅ true |
| `paving_sites[1]` | 0 | **649** | **2 of 2** | ❌ **FALSE** |
| `paving_sites[2]` | 1 | 1,836 | 1 of 2 | ✅ true |

So `paving_sites[1]` is presented to the model and the user as *the largest* block in its
band when it is in fact the second-largest — a fifth of the size. The old string was wrong
in the same *direction* (it claimed "largest contiguous block" for all three of the old
components, which was equally loose), so this is not a regression in kind — but the engineer
rewrote this string **specifically** to be a truthful band-level provenance label, and it
still does not hold for every row it labels. Note the parallel is not accidental: the
`green_sites` pool above it has the **same** latent looseness and was not touched at all
(it emits three components all labelled "largest contiguous vegetated block" — ranks 1, 2
and 3 of 3). That is pre-existing and out of scope, but it means the codebase now has two
pools with the same defect and one of them was supposed to be fixed.

**Why this is the project's worst failure mode and not a nit:** the number itself
(`pavedCells=649`) is correct and traceable; it is the *label* that misdescribes the
geometry. A reader who trusts the label concludes the 649-cell row is the band's dominant
feature. It is cheap to fix and it is the single thing I would not sign off without.

**Suggested direction (engineer's call, not mine):** the string needs to describe *rank*, not
just band — e.g. include the rank within the band, or drop "largest" and state the actual
selection rule ("4-connected block from row band N of the largest paved component,
candidates ordered by cell count"). Rank is already computed; it is simply not carried into
the string.

---

## 4. Section D — honesty invariants: were any weakened to make tests pass?

**No. This is the strongest part of the submission, and I checked it adversarially.**

### 4a. Every removed line, in full (14 removals, all inspected)

```
-from typing import Any, Protocol
-    for comp in _components_cells(paved_mask, 150, connectivity=4)[:3]:
-        g.paving_sites.append(site(
-            comp["cells"], "permeable_paving",
-            "largest contiguous impervious block (ESA WorldCover class 'paved'), "
-            "contoured to its true cell mask",
-            {"pavedCells": comp["size"],
-             "pavedAreaHa": round(comp["size"] * cell_w * cell_h / 10_000.0, 1)},
-        ))
-- the building authority is ``_overlay_common.building_footprint_mask``.
-    Delegates to ``_overlay_common.building_footprint_mask`` -- the same mask
-    ``siting.py`` and ``placement_footprints`` use, so the exclusion is one
-    answer rather than a second opinion.
-    return building_footprint_mask(ov)
```

That is the **entire** removal set: one import, the ten lines of the old paving block, and
three docstring lines whose replacement is a *longer and more accurate* explanation. **No
`warning`, no `status`, no `reasonCode`, no threshold and no predicate was removed.** Nothing
was softened to turn a red green.

### 4b. New combined score / verdict / badge? **NONE.**

Scanned all 277 added lines for `score`, `verdict`, `overall`, `rank`, `best`, `grade`,
`rating`, `composite`, `recommend`: **zero hits**. Invariants 1 and 2 hold. The added strings
are all technical provenance prose (`"whatever the set gave"`, `"no building overlap"`,
`"do not build on a surveyed footprint"`, two `why` reasons) plus one new `why` for the
sub-minimum reject branch. `bandIndex` is an ordering index, **not** a score — it carries no
value judgement and cannot be read as one.

### 4c. `reasonCode` still confined to the frozen set? **Yes.**

Literals assigned as a reason anywhere in the file:
`BUILDING_OVERLAP, CLIP_TOO_LARGE, NOT_A_SITE, OVERLAPS_STAGED, OVER_CUMULATIVE_CAP, OVER_SINGLE_CAP, TOO_SMALL`
— **all inside `REASON_CODES`**; `outside = []`. The new sub-minimum reject reuses the
existing `BUILDING_OVERLAP`, adding no code. Verified live: the sub-40 branch returns
`reasonCode='BUILDING_OVERLAP'` with `reasonCode in REASON_CODES → True`. ✅

### 4d. Does every number still carry accurate provenance?

| number | provenance | verdict |
|---|---|---|
| `pavedCells` / `pavedAreaHa` per sub-site | recomputed from **this** band's own cells | ✅ **correct** — this is the fix; the parent's 6,821 would have been a lie |
| `bandIndex` | index into the deterministic ascending-row band walk | ✅ correct & reproducible |
| `size.cells` on the clip path | `_polygon_cells` of the **served** geometry | ✅ truthful (420 served → 420) |
| `contourCells` / `contourOfComponent` | `contour_ring`'s own counts | ✅ (3,596 = 3,596 = 3,596) |
| `evidence` (band string) | claims "largest … within row band N" | ❌ **D2** — false for `paving_sites[1]` |
| `constraints.clippedCells` on the clip path | counts buildings removed, not survivors dropped | ❌ **D1** — under-reports |

The engineer's own inline reasoning for recomputing `pavedCells` is exactly right and is the
single best decision in the diff: *"Reusing the parent component's size here would publish a
number the served geometry does not support, under a `source` string that points at real
code — the most dangerous failure mode this project has, because it looks sourced."* That
reasoning is correct, and it was applied to the numbers. It was **not** applied to the
`evidence` string sitting right next to them (D2) — the same standard, one line over.

### 4e. Determinism of the whole diff

No `time`, no `random`, no `datetime`, no `time.time()` in any added line. `_split_under_cap`'s
sort is a **total order** (0 collisions in 20,000 brute-forced masks). `contour_ring`'s
`out.sort(key=lambda d: -d["enclosed"])` is likewise total (count + the list is stable).
`_owners_of` iterates `sorted(overlap)`. Nothing falls back on set/dict order. ✅

---

## 5. Section E — the engineer's filed findings, independently verified

| # | Finding | Verdict | Evidence |
|---|---|---|---|
| **F1** | The polygon side is load-bearing; a read-only union of the legacy mask alone would **not** make Red 1 pass | ✅ **CONFIRMED** | Reverting only `_polygon_footprint_mask` (keeping the guard) drops the suite to **17/18**, failing `test_PENDING_proposals_avoid_existing_buildings`: *"water_reservoir overlaps 2 building cell(s); permeable_paving overlaps 16 building cell(s)"* — exactly the 23 cells the bbox misses |
| **F2** | Red 3 still fails if Red 1 is reverted — the fixes are coupled | ✅ **CONFIRMED** | With Red 1 intact and the split reverted → **17/18**, failing `test_every_proposal_kind_round_trips_through_ai_plan`: *"permeable_paving: the proposal path emitted nothing"* |
| **F3** | `load_grounding` and `siting.py` still use the loose bbox mask | ✅ **CONFIRMED, and broader than filed** | **Four** live call sites still use `_overlay_common.building_footprint_mask`: `ai_plan.py:313` (`load_grounding`), `ai_plan.py:611` (`placement_footprints`), `siting.py:152`, plus `scripts/make_wetgrid.py:355` and `scripts/make_project_catalog.py:520`. None reference `proposal_constraints.building_mask` |
| **F4** | No numeric dependency of `buildings_hsk.json` on `MAX_INTERVENTION_COVERAGE` | ✅ **CONFIRMED** | `grep -rn MAX_INTERVENTION_COVERAGE scripts/` → **no matches**. The raster's only metadata key is `{"type": ...}`; no cap-like or `0.25` value anywhere in it. Regenerating the raster cannot move the coverage cap |
| **F5** | `rejected` computed by `generate_proposals` but dropped at the router | ✅ **CONFIRMED** | `ai_plan.py:2293` and `:2312` both return `"rejected": rejected`. `grep -c rejected app/routers/scenes.py` → **0**. The `apply=false` branch (`scenes.py:292-302`) passes `planId, features, provider, types, groundingNotes, message, proposals, candidateCounts, fallbackNote` — **no `rejected`**. `AIPlanResponse.model_fields` includes `rejected`, so it serialises as an empty list. See §6 for severity |

### F1/F2 coupling matrix (measured)

| Scenario | Score | Failing test |
|---|---|---|
| Baseline (both fixes) | **18/18** | — |
| bbox-only mask, guard present | 17/18 | `..._avoid_existing_buildings` (2 + 16 building cells) |
| polygon union, **guard removed** | **16/18** | `..._round_trips_through_ai_plan` (drainage emitted nothing) + `..._moves_the_ledger` |
| Red 1 intact, split reverted | 17/18 | `..._round_trips_through_ai_plan` (paving emitted nothing) |
| Polygon union only (no guard, no split) | 16/18 | both of the above |
| Restored | **18/18** | — |

Two things follow. First, **F1 is confirmed and stronger than stated**: the polygon side is
load-bearing *and* so is the guard — removing the guard alone costs two tests, not one.
Second, the engineer described the guard as a hardening no-op; it is in fact **load-bearing
on today's data**. The two reds are genuinely coupled, and neither alone suffices.

---

## 6. F5 severity — does dropping `rejected` break an honesty invariant?

**Yes, and it is the same invariant the whole proposal loop exists to serve.** This is not a
cosmetic gap.

The design promises refusals travel to the client. `DESIGN_intervention_proposals.md`
defines the batch response with a populated `rejected[]` carrying `reasonCode`,
`buildingOverlapCells` and a `warning`, and `app/schemas/intervention.py:220` states it
outright: *"`AIPlanResponse.rejected[]` and a client that omits this block still…"*.
`PRD_proposals.md:83` (US1) is categorical: **"Rejected and shown as a refused row (never
silently dropped)"**, and the rationale is given at `ai_plan.py:2263-2265`: *"Rejections
travel back with a reason code rather than being dropped: **a short list must never look
exhaustive**, and a planner who is told 'this paving site contains 61 cells under surveyed
buildings' can act on it."*

Measured consequence, on this very data set: a batch of the 9 pool candidates returns
**3 accepted / 6 rejected**, and all 6 rejections carry real, actionable reasons
(`OVERLAPS_STAGED`, naming `proposal#0`/`proposal#1`). Every one of those six refusals is
computed, carries a frozen `reasonCode` and a written `why` — and **none of it reaches the
client.** The user sees `features: 3` and `groundingNotes` and has no way to learn that six
candidates were refused, or why. That is precisely the "short list looks exhaustive" failure
the code comments say the field exists to prevent. On the `apply=true` path it is worse: the
`if not features` early-return (`scenes.py:305-311`) also omits `rejected`, and that is
exactly the branch where rejections are *most* numerous and *most* interesting.

**Severity: blocking for the loop's honesty claim, but PRE-EXISTING and out of Phase 1's
diff.** `scenes.py` is not in the Phase 1 diff; this bug was introduced with the endpoint,
not by these three reds. I rate it **the highest-priority deferred item** — it is a
one-line fix per return (`"rejected": proposed.get("rejected", [])`) but it must not be lost,
because it is the difference between "the model chose 3 of 9" and "6 of your options were
refused for stated reasons".

---

## 7. Defect list, prioritised

### Blocking before Phase 1 is called done

1. **D2 — `evidence` misdescribes `paving_sites[1]`** (`ai_plan.py:~408`, the string passed
   to `site()`). *"largest 4-connected impervious block within row band 0"* on a row that is
   rank 2 of 2 in band 0 (649 of 3,596). This is the project's named worst failure mode: an
   accurate number under a provenance string that misdescribes the geometry. Fix by carrying
   the already-computed rank into the string, or by stating the selection rule instead of
   "largest". **Repro:** `load_grounding().paving_sites[1]["evidence"]` vs its
   `metrics.pavedCells`.

### Must fix, can land after

2. **D1 — the clip guard silently discards survivor components and under-reports the
   change** (`proposal_constraints.py:895-917`). `contour_ring` returns only the largest
   ring, so a bisected site loses the smaller piece with no record; `clippedCells` counts
   only building cells (60) while the served polygon differs from the candidate by 480. The
   `warning` also never mentions a shrink on the site path, where
   `_accepted_warning`'s clip sentence is gated on `coordinate_based`. **Repro:** the 30×30
   block + 2-cell building stripe case in §2c; the served set is 420 while `clippedCells`
   says 60. Two candidate fixes: carry the dropped-component cells into `clippedCells`, or
   re-contour with `contour_rings` and reject when the largest ring is not the whole survivor
   set. Note the safety property itself is sound — this is a reporting defect, not an
   overlap defect.

### Deferred, filed, safe to defer (with reasons)

3. **F5 — `rejected` dropped at `scenes.py:292-302` and `:305-311`.** Breaks the design's
   "refusals are disclosed" promise; 6 of 9 candidates' refusals are currently invisible.
   Pre-existing, one line per return. Highest-value deferred item (§6).
4. **F3 — four call sites still use the loose bbox mask** (`ai_plan.py:313`,
   `ai_plan.py:611`, `siting.py:152`, plus `make_wetgrid.py:355` /
   `make_project_catalog.py:520`). So `siting.py`'s pool can still emit a site on a
   polygon-exact building cell. **Safe to defer:** every one of those paths is re-validated
   by `validate_proposal` before anything is served or persisted, and the gate now uses the
   stricter union, so the *user-visible* guarantee holds. The residual risk is a site that is
   *offered* and then *rejected* by the gate — an annoyance, not a correctness hole. Worth a
   follow-up ticket.
5. **The "no-op on current data" comment at `proposal_constraints.py:891-894` is false** (the
   guard fires on 2 of 9 pool sites, 2 and 16 cells). Cheap doc fix, but it is the comment a
   future reader would trust about test coverage.
6. **`green_sites` has the same provenance looseness as D2** — all three components are
   labelled "largest contiguous vegetated block" while ranking 1, 2 and 3. Pre-existing,
   untouched, but it will look inconsistent once D2 is fixed.

### Explicitly NOT defects (checked and cleared)

- `clippedFraction = 0.0` on the site path — **design-compliant**. The design's field table
  says *"`1 - kept/window`; `0.0` for a site-based proposal"*. Not weakened.
- Paving sub-sites overlapping reservoir/green pools — pre-existing, correctly refused by
  `validate_batch` with `OVERLAPS_STAGED` naming `proposal#n`.
- `test_overlay_masking.py` reporting 0 checks — a runner artefact, not a gap. Driven
  manually it is 3/3 (§1a). CI runs it under pytest correctly.
- `permeable_paving` having `to=None` **and** `cn=72.0` — that is the `cn_only` shape the
  gate is designed to report, and the `why` string discloses it is an assumption, not a
  lookup. Not a number claimed without provenance.
- The engineer modifying tests to pass — `git diff -- backend/tests/` is **empty**.

---

## 8. Bottom line

The engineering is sound. All three reds do what they say, all 12 backend suites and the
frontend suite reproduce exactly as reported, the honesty invariants were **not** weakened to
get there (the diff removes no `warning`/`status`/`reasonCode` and adds no score), determinism
holds under three independent attacks, and the two coupling claims (F1, F2) are real and I
reproduced them. The engineer's judgement on *why* — union direction, split-before-truncate,
recompute-from-own-cells, borrow-the-`drainage_alignment`-shape — is right on every count.

Two things to fix before this is signed off, and neither is a design problem:

- **D2**, the `evidence` string that calls a 649-cell rank-2 sub-site "the largest". Small
  edit, but it is the specific failure this project says it cares most about, and the
  engineer had already written down the correct standard for the numbers next to it.
- **D1**, the clip guard's silent component drop and under-reported `clippedCells`. Safety
  holds; disclosure does not.

And one thing that is not Phase 1's to fix but must not be dropped: **F5**, `rejected`
computed and thrown away, which makes a 3-of-9 result read as exhaustive.

Per my process: I have fixed nothing. D1 and D2 go back to the engineer; I will re-run the
regression suites once the fixes land (round 2), and per the 2-round hard limit anything
still failing after that is documented as a known issue rather than chased further.
