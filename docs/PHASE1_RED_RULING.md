# Phase 1 RED RULING — architect decision record

**Author:** 高见远 (Gao), Architect · **Date:** 16 Oct 2026
**Repo:** `Hack4sdg/T1` · **Branch:** `feature/intervention-loop` · **HEAD:** `2ad7176`
**Baseline:** `tests/test_proposal_contract.py` 15/18 · frontend `npx vitest run` 98/98
**Target:** 18/18 backend, 98/98 frontend, no new deps, `scipy < 1.14` untouched.

This is a **decision document**. No production code was written. Every number below was measured on this
HEAD, not reasoned about. Where my measurement contradicts the briefing I inherited, I say so and rule
according to the measurement.

---

## 0. Measured facts this ruling rests on

| # | Measurement | Value |
|---|---|---|
| M1 | grid | 117 × 148 = **17,316** cells; `maxSingle` = 4,329; `maxCumulative` = 6,926 |
| M2 | app building mask `building_footprint_mask(ov)` (`rasterise_height` bbox raster) | **7,789** cells |
| M3 | polygon-exact building cells (test's `_building_cells`, same `buildings_hsk.json`) | **3,287** cells |
| M4 | `exact − approx` (cells the app's mask **leaks**) | **24** |
| M5 | `approx − exact` (cells the app's mask **over-covers**) | 4,526 |
| M6 | served `water_reservoir` polygon ∩ exact mask | **2** cells ← the red |
| M7 | served `water_reservoir` polygon ∩ approx mask | **0** cells |
| M8 | `contour_rings` `covered` vs `enclosed` for every reservoir/green pool component | **equal, all 6** |
| M9 | paving pool: components ≥ 150 cells, buildings excluded | **exactly 1**, size **6,821** |
| M10 | paving component bbox | 117 rows × 148 cols (full scene extent) |
| M11 | row-band greedy split at cap 4,329 | 2 bands: **4,314** + **2,507** cells |
| M12 | per-band 4-connected components | band0 `[3596, 649, 37, 14, 11, 3]`, band1 `[1836, 671]` |
| M13 | components ≥ `MIN_SITE_CELLS` (40) | band0 `[3596, 649]`, band1 `[1836, 671]` |
| M14 | sum of those four | **6,752** ≤ 6,926 cumulative cap ✔ |
| M15 | each of the four | ≤ 4,329 single cap ✔, all ≥ 40 ✔ |
| M16 | pairwise overlap among the four served polygons | **0** |
| M17 | with union mask (`approx ∪ exact` = 7,813) applied at pool build: `served_in_exact` / `served_in_approx` | **0 / 0** for all 6 reservoir+green pools |

---

## 1. Ruling — RED 1 `test_PENDING_proposals_avoid_existing_buildings`

### 1.1 The briefing's premise is wrong. Ring closure is NOT the cause.

I was told to choose between (a) testing with the contour's ring closure and (b) clipping the candidate to
non-building cells before contouring, on the theory that `contour_ring` "CLOSES the ring across a 1-cell
building gap, so re-rasterising picks enclosed building cells back up."

**Measurement M8 refutes this.** For every reservoir and green pool component on this HEAD,
`contour_rings(...)["covered"] == contour_rings(...)["enclosed"]`. There is no closure gap. The pools are
built with `connectivity=4` and `min_size` 40/120, and those components come back compact enough that the
contour encloses exactly its own cells. `covered − enclosed == ∅` everywhere.

So **neither (a) nor (b) is the fix.** Option (a) is additionally inadmissible: it would change the test to
match the implementation, which is the definition of a vacuous test. Option (b) is defensible as hardening
but does not address the actual cause — I verified that clipping to a *union* mask changes nothing for
reservoir/green, because the leak is in the mask, not in the contour.

### 1.2 The actual cause: two different building authorities (M2–M7)

| Side | How it decides "this cell is a building" | Cells |
|---|---|---|
| **App gate** | `proposal_constraints.building_mask` → `_overlay_common.building_footprint_mask` → `design_metrics.rasterise_height` → a **bounding-box rectangle per footprint** stamped from the footprint's centre cell | 7,789 |
| **Test** | `_polygon_cells(grids, feature["coordinates"])` — **point-in-polygon at cell centres** | 3,287 |

`rasterise_height` (`design_metrics.py:776-796`) is a *massing height raster*: it is honest about being a
crude envelope for 3D display, and its docstring says so. It is **not** a footprint occupancy test. Used as
one, it does two wrong things at once: it inflates (4,526 cells that are courtyards and gaps) and — the
dangerous direction — it **leaks 24 real building cells**. Two of those 24 fall inside the served
`water_reservoir` polygon (M6). The app sees 0 overlap against its own mask (M7), the test sees 2. Both are
correctly reporting their own authority.

**This is a D3 violation, not a D3 ambiguity.** D3 states the mask is "the one building authority" and that
"a cell either has a surveyed footprint in it or it does not." A bbox rectangle is neither — it is an
approximation that answers a different question. The fix makes the code *more* faithful to D3.

### 1.3 RULING

**R1 — Make the proposal gate's building authority polygon-exact, and make it the UNION of the polygon
set and the legacy bbox set. Do not touch the contour, the rasteriser, or `_overlay_common`.**

- **File:** `backend/app/services/proposal_constraints.py`
- **Function:** `building_mask` (lines 103-121)
- **Change:** stop delegating to `_overlay_common.building_footprint_mask`. Build the mask as
  `polygon_exact ∪ bbox_raster`, where `polygon_exact` is the union of
  `sandbox_model._polygon_cells(grids, coords)` over every `Polygon`/`MultiPolygon` in
  `settings.rasters_dir / "buildings_hsk.json"` — **the same rasteriser the ledger and the test already
  use** (D7: no new dependency, no new geometry, no second opinion about which cells a polygon covers).
  Keep the `None` return when the file is absent (D.2: "no data" ≠ "no buildings").

**Why the union and not the polygon set alone.** The gate's mask must be a **superset of every pool's
building filter**, so the gate can never be *looser* than the filter that produced the candidate it is
checking. `load_grounding` (`ai_plan.py:305`) filters pools by the **bbox** mask. If the gate used the
polygon set alone it would be 4,526 cells *looser* than the pool filter — the gate would be weaker than the
proposal. The union (7,813) dominates both. The direction of the union is the whole point: it is the
conservative one for a "do not touch buildings" contract.

**Why this makes M6 zero.** Served cells ⊆ pool cells ⊆ ~approx. Gate forbids `approx ∪ exact`. So
`served ∩ (approx ∪ exact) = (served ∩ approx) ∪ (served ∩ exact) = 0 ∪ 0 = 0` (M7, and exact by M17's
method). The test's own check is `served ∩ exact = 0`. Green.

**Blast radius: the proposal path only.** `building_mask` is called from exactly one place —
`ai_plan.generate_proposals` (`ai_plan.py:2167`). `siting.py` and `placement_footprints` keep the bbox mask
and are untouched, so their tests cannot move.

### 1.4 RULING (hardening, explicitly NOT the fix)

**R1b — Add a post-contour clip guard in `validate_proposal`, so the guarantee survives a future crenellated
pool.** At `proposal_constraints.py:768-790`, the non-coordinate branch computes
`covered = _polygon_cells(grids, coords)` and serves `geometry = dict(geom)` — the raw pool polygon. Add:
subtract `occupied` from `covered`; if anything was removed, re-contour the survivors through
`intervention_geometry.contour_ring` (**the** contour authority, D2) and serve that geometry instead;
`reasonCode = "BUILDING_OVERLAP"` if the survivors fall under `MIN_SITE_CELLS`. R1b is defence-in-depth for a
pool that does not exist today — M8 proves none does. It is cheap, it uses an existing `REASON_CODES`
member, and it makes the contract structural rather than incidental. Land it in T04, after T01 is green, so
if it perturbs anything the perturbation is attributable.

### 1.5 Explicitly rejected

- **Changing the test** to read `building_mask`. Rejected: the test is the acceptance criterion and its
  polygon test is the correct one. Adapting a test to the code it is meant to audit inverts the contract.
- **A tolerance/buffer.** Forbidden by D3, and unnecessary — the cause is a wrong rasteriser, not a
  boundary definition.
- **Changing `_overlay_common.building_footprint_mask`.** Rejected: it is shared with `siting.py`,
  `placement_footprints` and render-time roof suppression. Widening it to fix a proposal-path red would put
  three unrelated behaviours at risk for no gain.

---

## 2. RULING — RED 2 `test_every_proposal_kind_has_a_defined_ledger_effect`

### 2.1 RULING

**R2 — Give `drainage` and `road` the `{"to": None, "why": ...}` shape. Do NOT drop them from
`POOL_FOR_KIND`.**

- **File:** `backend/app/services/sandbox_model.py`
- **Insertion point:** `INTERVENTION_EFFECTS`, immediately after the `drainage_alignment` entry (line 1075-1078)
- **Effect on the code path:** `apply_interventions` (lines 1375-1398) currently hits
  `effect is None` and appends `skipped` with the literal string `"no land-cover effect is defined for this
  intervention type"` — which is exactly what the test greps for. With the entry present, control reaches
  line 1380; `to is None` and `"cn" not in effect` → line 1396-1397, `skipped` **with the `why`**. The
  string the test hunts for never appears.
- **`why` text (honest, D8):** `drainage` — *"a drainage network needs pipe and outfall capacity, which
  this screening model does not have; the alignment is drawn and disclosed but no grid effect is claimed."*
  `road` — *"a new road is a permeability and roughness question this screening model does not parameterise;
  the footprint is drawn and disclosed but no land-cover effect is claimed."*
  Note both say **no number is claimed** rather than borrowing `permeable_paving`'s CN 72. Borrowing a CN
  would be inventing a result (invariant 5).

### 2.2 Why not the alternative (dropping the kinds)

Dropping `drainage`/`road` from `POOL_FOR_KIND` (`ai_plan.py:918-924`) turns the test green and is a
one-line change, which is exactly why it should be checked rather than taken:

1. **It orphans two legend rows.** `STYLE_FOR_KIND` (`ai_plan.py:904-915`) keeps `drainage` (`#e3b341`) and
   `road` (`#6e7681`). The frontend `components/InterventionLegend.tsx` mirrors `STYLE_FOR_KIND` by hand, and
   the Phase-4 `test_legend_parity.py` will assert legend rows == `STYLE_FOR_KIND`. Dropping from
   `POOL_FOR_KIND` does **not** remove the style entries, so the legend would advertise two intervention
   types that the model can no longer select (the `CHOOSE_TOOL` enum is `sorted(set(POOL_FOR_KIND))`,
   `ai_plan.py:1253`). A legend row for an unselectable kind is a UI claim the backend cannot honour.
2. **It silently removes product capability.** The user's requirement is that all intervention types are
   generable. Deleting two kinds to satisfy a bookkeeping test inverts the priority.
3. It also shrinks `test_every_proposal_kind_round_trips_through_ai_plan`'s coverage, so Red 3's kind
   coverage would silently narrow.

### 2.3 Downstream test consequences — **no test edits required**

- `NO_GRID_EFFECT_KINDS` (`tests/test_proposal_contract.py:139`) stays `{"drainage_alignment",
  "massing_height"}`. Those two entries are untouched, so `test_no_grid_effect_kinds_are_reported_and_explained`
  (line 279-293) keeps passing **unchanged**. It asserts *skipped with a why* — precisely the shape R2 gives
  `drainage`/`road`.
- The every-kind sweep at line 266 gains `drainage`/`road`; both land in `skipped` with a non-empty `why`
  (line 273-274), so it passes.
- `REASON_CODES` needs **nothing**. `effect_for_kind` (`proposal_constraints.py:467`) returns
  `("none", <why>)` for both — previously `("none", None)`. That is a *disclosure improvement under D8*
  (the gate now tells the user the alignment changes no number) and it does not produce a `reasonCode`.
  `NO_EFFECT` is in the frozen set but is referenced nowhere in `proposal_constraints.py`; R2 does not
  introduce it.

---

## 3. RULING — RED 3 `test_every_proposal_kind_round_trips_through_ai_plan`

### 3.1 RULING

**R3 — Split the paving pool by row bands under the single cap, then keep every 4-connected component of a
band that is ≥ `MIN_SITE_CELLS`. Deterministic order; no fragment silently discarded.**

- **File:** `backend/app/services/ai_plan.py`
- **Function:** `load_grounding`, the paving block (lines 378-386)
- **New private helper** (module level, next to `_components_cells` at line 401):
  `def _split_under_cap(cells, cap, min_cells) -> list[list[tuple[int,int]]]`

**Algorithm, exactly:**

1. Group the component's cells by row into `dict[int, list[int]]`.
2. Walk rows in ascending `r`. Maintain `band` and `n`. If `band` is non-empty and `n + len(rows[r]) > cap`,
   close the band and start a new one; else extend. → deterministic, order-independent (M11: 4,314 + 2,507).
3. For each band, build its boolean mask and call
   `intervention_geometry.connected_components(mask, 1, connectivity=4)` — **the** component definition
   (D7, same alias `_components_cells` already uses).
4. Drop components `< min_cells` (M12: the 37/14/11/3/1-cell shards go).
5. Sort survivors by `(-size, min_r, min_c)` — a total order, so no tie depends on set or dict iteration.
6. Emit one site per surviving component, through the **existing** `site()` closure, so `geometry`,
   `centroid`, `areaHa`, `evidence` and the `contourCells`/`contourOfComponent` metrics are computed by the
   same code as every other pool. No new geometry path.

**Verified against the caps (M13–M16):** four sites of **3,596 / 649 / 1,836 / 671** cells. Every one is
≥ 40 (`MIN_SITE_CELLS`) and ≤ 4,329 (`maxSingle`). Sum 6,752 ≤ 6,926 (`maxCumulative`). Pairwise overlap
**0**, so `test_PENDING_proposals_do_not_overlap_each_other` and `validate_batch`'s `claimed |= cells` are
both safe — the bands are disjoint by row and the components are disjoint within a band, and `covered ==
enclosed` means no contour closure can reach a neighbour.

### 3.2 Why not the alternatives

- **BFS chunking at the cap** (fill each chunk to 4,329 by flood fill): measured, produces **13 parts**,
  sizes `[…, 1, 1, 220, 3, 4, 6, 3, 180]`. Six parts fall under `MIN_SITE_CELLS` and would be rejected
  `TOO_SMALL` at the gate — trading one red for a stream of rejections, and re-introducing the "silently
  shorter list" failure D5 exists to prevent. Rejected on measurement.
- **Row bands with no component filter**: band0 is 8 components (`[3596, 649, 37, 14, 11, 3]`), so
  `contour_rings(...)[0]` would silently serve only the largest and drop 1,318 cells with no record.
  Rejected — same D5 violation.
- **Keep only the largest component per band** (2 sites, 3,596 + 1,836): legal and green, but it discards
  1,320 real cells silently. Keeping all four costs nothing (6,752 ≤ 6,926) and reports more of the
  district. Rejected as unnecessary loss.
- **Raise `MAX_INTERVENTION_COVERAGE`**: forbidden. §2.1 fixes it at 0.25; 6,821/17,316 = 39.4 % would be a
  district-scale land-use change, which is the exact thing the cap was written to stop.

### 3.3 Metrics honesty — mandatory detail

**Every metric on a split sub-site must be recomputed from that sub-site's own cells.** `site()` already
does this (it contours the cells it is handed), so the rule is: pass the sub-site's `cells`, never the
parent component's, and never reuse `comp["size"]`, `comp["min_r"]` or the parent's depth `sub`. The
`evidence` string must change too — "largest contiguous impervious block" is now false for 3 of the 4
sites. Required wording: *"largest 4-connected impervious block within row band N of the largest paved
component (ESA WorldCover class 'paved'), contoured to its true cell mask"*, with `bandIndex` in `metrics`.
Provenance (`source`/`date`/`confidence`) rides on `evidence` + `source` as it does for every other pool.

### 3.4 Scope note

`load_grounding` currently truncates pools with `[:3]` **at the component stage**. R3 must apply the `[:3]`
truncation to the **emitted sub-sites**, not to the pre-split components, or the split buys nothing. The
paving pool goes from 1 site to 4; `propose()`'s `sites[:count]` with `count ≤ 3` (`parse_intent` clamps to
3) means at most 3 are ever proposed, which is the existing behaviour for every pool.

---

## 4. Task list (ordered by dependency)

Five tasks. T01–T03 are independent of each other and each turns exactly one red green; T04 hardens and
regresses. **Run the full backend suite after every task** — the tests are plain scripts, so:

```
cd T1/backend && ./.venv/bin/python tests/test_proposal_contract.py
```

### T01 — Polygon-exact gate building authority (Red 1)
- **Files:** `backend/app/services/proposal_constraints.py` (`building_mask`, lines 103-121)
- **Depends on:** —
- **Goes green:** `test_PENDING_proposals_avoid_existing_buildings`
- **Regression risk:** the mask grows 7,789 → 7,813 cells, so pools feeding the gate lose ≤ 24 candidate
  cells. A pool site could in principle drop below `MIN_SITE_CELLS` and flip `TOO_SMALL`. Measured: reservoir
  top component 1,878 → 1,876, green 252 → 252. **Not** at risk in practice.
  **Detect:** the per-kind accepted counts printed by the line-296 test; plus `test_PENDING_…do_not_overlap_each_other`
  and the coverage-cap test, both of which re-run the full proposal path.
- **Priority:** P0

### T02 — Ledger disclosure for `drainage` and `road` (Red 2)
- **Files:** `backend/app/services/sandbox_model.py` (`INTERVENTION_EFFECTS`, after line 1078)
- **Depends on:** —
- **Goes green:** `test_every_proposal_kind_has_a_defined_ledger_effect`
- **Regression risk:** `INTERVENTION_EFFECTS` gains two keys, so the sweep at test line 266 and any
  `for kind in INTERVENTION_EFFECTS` consumer now sees them. The effect check at
  `apply_interventions:1375-1398` runs **before** geometry is read, so no geometry path changes.
  **Detect:** the line-266 sweep, `test_no_grid_effect_kinds_are_reported_and_explained` (line 279), and a
  grep for other `INTERVENTION_EFFECTS` consumers to confirm none asserts a fixed key count.
- **Priority:** P0

### T03 — Row-band split of the paving pool (Red 3)
- **Files:** `backend/app/services/ai_plan.py` (`load_grounding` paving block 378-386; new helper beside
  `_components_cells` at 401)
- **Depends on:** —
- **Goes green:** `test_every_proposal_kind_round_trips_through_ai_plan`
- **Regression risk:** three, in order of seriousness.
  1. *Metric provenance.* Reusing the parent component's `size`/`bbox` on a sub-site publishes a number the
     served geometry does not support. **Detect:** assert `site["metrics"]["contourCells"] ==
     len(_polygon_cells(grids, site["geometry"]["coordinates"]))` for every emitted site.
  2. *Cumulative cap.* Four paving sites sum to 6,752; a batch that also claims reservoir or green ground can
     cross 6,926 and produce `OVER_CUMULATIVE_CAP`. That is **correct designed behaviour** (a rejection with
     a reason, not a crash), but it must be a deliberate acceptance, not a surprise.
     **Detect:** read the `rejected[]` array on the multi-kind round-trip instruction.
  3. *Site count.* `paving_sites` grows 1 → 4, which changes `candidateCounts` in the gate payload and the
     model catalogue size. **Detect:** any test asserting an exact pool length; the frontend
     `interventionLayer`/`InterventionLegend` do not read pool length, so vitest 98/98 should hold.
- **Priority:** P0

### T04 — Contour clip guard + full regression (R1b, hardening)
- **Files:** `backend/app/services/proposal_constraints.py` (`validate_proposal`, non-coordinate branch,
  lines 768-790; plus the `BUILDING_OVERLAP` reason already present in `_reject_warning`)
- **Depends on:** **T01** (land after T01 is green, so any perturbation is attributable)
- **Goes green:** nothing new — it must keep 18/18 green
- **Regression risk:** this is the only task that touches the serve path for *every* site-based proposal.
  Re-contouring survivors can shrink a served polygon and change `areaHa`/`centroid` and the coverage-cap
  arithmetic. It is a no-op on today's data (M8/M17: `covered == enclosed`, zero building cells), so if the
  suite moves at all, the guard is wrong, not the data.
  **Detect:** run the suite twice — once with T04, once with it stubbed out — and diff the accepted
  proposals' `areaHa` and cell counts. Any non-empty diff means the guard is firing on clean data.
- **Priority:** P1

### T05 — Docs + changelog
- **Files:** `docs/DESIGN_intervention_proposals.md` (record R1/R1b/R2/R3 and the D3 clarification),
  `STATUS.md`, `CHANGELOG.md`
- **Depends on:** T01, T02, T03, T04
- **Note:** the design doc currently claims `building_mask` is "the same mask `siting.py` and
  `placement_footprints` use". After R1 that sentence is **false** and must be corrected, together with the
  filed divergence: `siting.py`'s pool filter still uses the bbox raster and therefore still leaks the same
  24 cells. That is a **real, known, unfixed defect in a different code path** — file it as a Phase 2 item
  (`siting.py:120` pool construction) rather than silently widening the change. Do not let it be lost.
- **Priority:** P1

---

## 5. Frozen contract — changes proposed

**None.** Explicitly, per item:

| Frozen thing | Change |
|---|---|
| `validate_proposal` / `validate_batch` signatures (§4) | **none** |
| `REASON_CODES` (`proposal_constraints.py:69`) | **none** — R1b reuses `BUILDING_OVERLAP`, already in the set |
| `AIPlanResponse.rejected` | **none** — no new rejection shape |
| `INTERVENTION_EFFECTS` key set | **grows by 2** (`drainage`, `road`) — an internal table, not a wire contract; both entries produce the *existing* skipped-with-why shape, so no consumer signature moves |
| `_polygon_cells` / `contour_ring` / `contour_rings` signatures | **none** |
| New dependencies | **none** (D7). The fix reuses `sandbox_model._polygon_cells`, the rasteriser the ledger already trusts |

Nothing here requires a §2.2 amendment, and `reasonCode` never leaves the frozen set.

---

## 6. Honesty invariants — confirmation

| # | Invariant | Holds? | Note |
|---|---|---|---|
| 1 | No combined score across heat/wind/drainage | **YES** | Nothing touches scoring. R2 explicitly refuses to borrow `permeable_paving`'s CN 72 for drainage/road. |
| 2 | No pass/fail verdict | **YES** | All three fixes are accept/reject with a `reasonCode` from the frozen set; none ranks or scores a site. |
| 3 | `status` + non-empty `warning` on every AI output | **YES** | T03 adds sites through the existing `site()` + `_proposal_from` path, which always sets both. |
| 4 | Provenance on every number | **YES, at risk** | See below. |
| 5 | Model supplies assumption, never result | **YES** | R2's `why` claims no number rather than inventing one. R3's metrics are computed by code. |
| 6 | `reasonCode` within frozen `REASON_CODES` | **YES** | No new code. Verified against `proposal_constraints.py:69`. |
| 7 | Determinism (no `time`, no `random`, no set/dict-order ties) | **YES, at risk** | See below. |

### Most at risk: **#4 (provenance)**

T03 publishes numbers for a geometry that no longer corresponds to the component the old evidence string
described. If the engineer reuses `comp["size"]`, `min_r/max_r`, or the parent's depth `sub.max()`, a
`water_reservoir`-style metrics block ends up describing ground that is not in the served polygon, with a
`source` string that points at real code — the most dangerous failure mode in this project, because it looks
sourced. **Mitigation is mandatory, not optional:** metrics recomputed from the sub-site's own cells
(§3.3), the `evidence` string rewritten to name the band, and the T03 regression assertion comparing
`contourCells` against a fresh `_polygon_cells` rasterisation of the served geometry.

### Second most at risk: **#7 (determinism)**

`_split_under_cap` walks `dict` rows. Python dicts preserve insertion order and the helper inserts in
ascending `r`, so the walk is deterministic — **but only if the engineer does not iterate
`cells`/`set` directly** to build the row map, and only if the component sort key `(-size, min_r, min_c)`
is a genuine total order (it is: no two distinct components share all three). Ties must never fall back to
"whatever the set gave". Same care as `clip_window`'s existing `(row, col)` tie-break.

---

## 7. Residual risks I am not ruling away

1. **`siting.py:120` still leaks the same 24 building cells.** R1 fixes the proposal gate only. The catalog
   / suggester path keeps the bbox raster and can still site on 24 real building cells. **This is a genuine
   unfixed defect and must be filed, not forgotten** (T05).
2. **`rasterise_height` remains the render-time roof suppressor**, where a bbox envelope is the correct
   behaviour. Do not "fix" it.
3. **Four paving sites may trip `OVER_CUMULATIVE_CAP`** when batched with other kinds. Intended, but it will
   look like a bug the first time it appears in the gate. T03's task notes must say so.
4. **The 2-cell leak is data-dependent.** The 24 leaked cells come from `buildings_hsk.json` as committed.
   Re-running `fetch_*` scripts could change that set, and with it the exact overlap count. R1 is robust to
   this because it removes the class of error, not the instance.