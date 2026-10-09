# PRD — Stages 3–6 (increment): comparison, heat anchoring, wind corridors, drainage routing

**Owner:** 许清楚 (PM) · **Date:** 2026-10-09 · **Type:** incremental PRD, not greenfield
**Source of truth:** `STATUS.md` (Stages table + "Gaps in the heat / wind / drainage work");
`Hack4SDG_Heat_Wind_Drainage_Research.md` Ch. 1–5; `docs/HEAT_AND_WIND_PROGRESS.md`.
**Scope:** Stages 3, 4, 5b, 6 only. Stage 7 (LLM objective) and any frontend work are out.

---

## 1. Goal & non-goals

**Goal** — make the three axes *comparable* and *physically grounded*: rank scenarios only
under **stated** weights; add a design-responsive SVF + shadow heat field with a stated,
cited anchoring layer; add least-cost-path wind corridors plus a per-direction FAD **ratio**;
and turn the already-computed D8 receiver into a fill-and-spill cascade guarded by a
mass-balance assertion.

**Non-goals** — no combined score across heat/wind/drainage; no pass/fail verdict; no CFD;
no temperature or comfort index (UTCI/PET/SET); no hydraulic model (pipes, culverts, outfalls);
no claim of real-data anchoring until a Landsat LST scene or HKO normals are actually ingested;
no changes to the city-model generator (`place`/`reset` stay unsupported in `scenario.py`).

---

## 2. User stories

- **As a planner**, I want to rank my design options under weights I choose and the tool echoes
  back, so the ordering reflects my priorities and not the project's — and I want the three axes
  still visible beside the ranking so I can see what the weights cost me.
- **As a planner**, I want a heat field that moves when I change massing *or* plant greenery,
  and that tells me what its 0–1 scale is anchored to, so I can trust a comparison without ever
  mistaking it for a temperature.
- **As a planner**, I want ventilation corridors drawn per wind direction, so I can see where the
  breeze reaches and what a scheme does to it, and a per-direction FAD ratio so I can compare two
  schemes without a velocity ratio I cannot compute.
- **As a planner**, I want the drainage view to route water downhill and to prove its books
  balance, so I see where water arrives rather than one flat water plane.
- **As a judge**, I want every number to carry a machine-readable status and a human warning, and
  no combined score or verdict anywhere, so I can see the tool never overclaims.
- **As a developer**, I want each stage to name its target file and a runnable acceptance test, so
  I can build in dependency order without re-encoding the same geometry.

---

## 3. Requirement pool

Prioritisation: **P0** = the Ch. 5 §5.7 "if you only do three things" that remain plus what
unblocks them, and the hard guards. **P1** = the rest of the Stage 4–6 scope. **P2** =
data-gated or lever work the model cannot yet see.

### P0 — capabilities

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P0-1** | `compare(scenarios, weights=None)` ranks scenarios **only** when weights are supplied, and echoes the weights used. With `weights=None` it **refuses** and states why. The three axes stay separately visible for every scenario. | New `tests/test_scenario.py`: `compare(scens)` (no weights) returns `{"ranked": None, "refused": True, "reason": ...}` and ranks nothing; `compare(scens, weights={"shade":.5,"ventilation":.3,"drainage":.2})` returns an ordering whose weights are echoed; ranking is invariant to input list order; each ranked row still carries its three axis values. | "relative, not a pass/fail"; "the weights are the caller's, not the project's" | `app/services/scenario.py` (add `compare()`; research doc calls the module `evaluate.py` — keep `scenario.py`) |
| **P0-2** | `sky_view_factor(height_raster)` — horizon search over the massing height raster (`rasterise_height()` already exists) across N azimuth bins; SVF ∈ [0,1]. | Extend `tests/test_design_metrics.py`: a lone tower in a flat field → mean open-field SVF ≈ 1 (> 0.98); a uniform tall block over the whole grid → SVF ≈ 0 (< 0.05); SVF is monotone non-increasing as surrounding height rises; deterministic; < 1 s. | "Heat is not a temperature"; "screening-level; relative, not absolute" | `app/services/design_metrics.py` |
| **P0-3** | Least-cost-path ventilation corridors on an inverse-FAD cost surface (`cost = 1/(1+FAD)`) via `scipy.sparse.csgraph.dijkstra`, per direction; plus `fad_ratio(baseline, scenario, direction) = scenario_FAD / baseline_FAD`. | Extend `tests/test_ventilation.py`: a synthetic district with one low-FAD lane returns a path along the lane; corridor cost is monotone in FAD; NE corridor ≠ SW corridor on the real layout; `fad_ratio == 1.0` for identical schemes, `> 1` where blockage rises; deterministic; < 1 s. | "FAD is not a velocity ratio"; "a ratio is a comparison, not an absolute VR" | `app/services/ventilation.py` (canonical geometry stays in `design_metrics.py`; `ventilation.py` delegates) |
| **P0-4** | `mass_balance(runoff_in, stored, discharged, tol)` as a **runtime assertion** and a unit test: `runoff in = stored + discharged`, to tolerance. | Extend `tests/test_ledger.py`: identity holds within relative tol on the real grid and on a synthetic flat basin; a deliberately leaked cell makes the assertion **raise**; more rainfall raises `runoff_in`. | "Not a flood prediction"; "screening, not a flood prediction" | `backend/scripts/hydrology.py` + `app/services/sandbox_model.py` |

### P0 — hard guards (must appear as requirements, not footnotes)

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P0-5** | **Never publish a combined score** across the three axes. `evaluate()` keeps `combinedScore: null`; `compare()` may order only with stated weights and must keep the three axes visible. | Test asserts no scalar total is emitted by `evaluate()`/`compare()`; `combinedScore is None`; every ranked row exposes the three axes. | "three axes, not one number" | `app/services/scenario.py` |
| **P0-6** | **Never print a pass/fail verdict.** TC 1/06 §6 gives no absolute benchmark; the AVA policy says whether the better option meets a standard "cannot be known". | Test asserts no truthy `verdict`/`pass`/`fail` field in any response; `verdict` is `None` with the reason present. | "relative, not a pass/fail" | `app/services/scenario.py`, `design_metrics.py` |
| **P0-7** | **Determinism** — no wall-clock, no RNG in any new path. | Frozen golden scenario reproduces byte-identical numbers across two runs; test greps the new modules for `time`/`random` imports. | "screening-level; relative, not absolute" | all new/extended modules |
| **P0-8** | **Seconds budget** — each `evaluate()` and `compare()` stays under ~ 1 s on a laptop. | Test times a full `evaluate()` + `compare()` and asserts the bound (ledger precedent: 0.9 s). | "screening-level; relative, not absolute" | all new/extended modules |

### P1 — rest of the Stage 4–6 scope

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P1-1** | Blend land-class index + SVF + shadow into **one relative field** so both greening and massing move it. | Extend `tests/test_design_metrics.py`: greening-only lowers the field; massing-only (taller/closer) lowers it via SVF + shadow; field stays 0–1; the ramp and disclaimer are unchanged. | "relative index, not a temperature"; "Heat is not a temperature" | `backend/scripts/make_heatmap.py` + `app/services/design_metrics.py` |
| **P1-2** | Anchoring **layer**: map the 0–1 relative scale to a stated, cited reference range; store the calibration machine-readably in the sidecar. `HEAT_INDEX` stays labelled hand-authored until real data is ingested. | New test: calibration is loaded from a stored file, not hard-coded; response echoes `anchorSource`, `anchorRangeC`, `calibrationPath`; with no real calibration present, `anchorSource: "none"` and status remains `RELATIVE_INDEX_NOT_A_TEMPERATURE`; `heatIndexSource` still reads "Hand-authored screening index; awaiting verification". | "screening-level; relative, not absolute"; do **not** claim real data if not ingested | `make_heatmap.py` sidecar + `design_metrics.py` |
| **P1-3** | Drainage **depression hierarchy**: label connected depressions on the Priority-Flood filled surface and record each one's spill elevation. | New `tests/test_hydrology.py`: a synthetic two-pit DEM yields exactly two depressions with the correct spill elevations; labels contiguous; deepest basin matches the filled surface. | "screening, not a flood prediction" | `backend/scripts/hydrology.py` |
| **P1-4** | **Fill-and-spill cascade** over the D8 receiver: walk cells in decreasing elevation, carry each cell's runoff to its receiver, fill a depression to its spill level, pass the excess downstream; emit a routed depth field. | New test: mass balance holds (P0-4); water arrives at a basin rather than one global plane; monotone (more pavement → deeper routed depth); deterministic; < 1 s. | "not a flood prediction" | `backend/scripts/hydrology.py` + `make_wetgrid.py` |
| **P1-5** | **HAND** (Height Above Nearest Drainage) computed **independently** as a visual cross-check overlay. | New test: HAND = 0 on drainage cells, positive elsewhere; monotone along a synthetic slope; the HAND-flagged inundated set vs the routed depth is reported as a stated relation, not silently merged. | "screening, not a flood prediction" | `backend/scripts/hydrology.py` |
| **P1-6** | **Honesty labels are deliverables.** Every new output carries a machine-readable `status` plus a human `warning`, matching the existing sidecar pattern (`RELATIVE_INDEX_NOT_A_TEMPERATURE`, `SCREENING_VIEW_SYNTHETIC_TERRAIN`). Standardise: "Screening-level; relative, not absolute", "Not engineering-grade", "Not a flood prediction", "FAD is not a velocity ratio", "Heat is not a temperature". | Test enumerates every new output and asserts a `status` string from a known set and a non-empty `warning`. | all of the above | sidecars in `make_heatmap.py` / `make_wetgrid.py`; responses in `scenario.py`, `design_metrics.py` |

### P2 — data-gated or lever work

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P2-1** | Ingest a real **Landsat 8/9 LST** scene over HSK or the **HKO 1991–2020 normals** and store the resulting calibration. **Blocked today** — no such data on disk. | Test flips from "anchorSource none" to the real source only when the data file exists; otherwise it is skipped with the reason. | "screening-level; relative, not absolute" | `make_heatmap.py` + `reference/` |
| **P2-2** | Greening **roughness penalty** on the corridor cost surface (research §2.8 item 7). | Corridor cost rises where green cover rises; ratio still monotone. | "FAD is not a velocity ratio" | `app/services/ventilation.py` |
| **P2-3** | **Podium / ground-gap** handling in the massing model, so the lever the AVA guidance ranks above height is visible (research §2.8 item 6). | A podium-covered scheme scores a higher ground-level FAD / lower void fraction than a tower-only one at equal height. | "FAD is not a velocity ratio" | massing generator (coordinate with the model owner) |

---

## 4. Data & provenance

| Input | Status today | What it means for this increment |
|---|---|---|
| Land cover | **Real** — ESA WorldCover 10 m v200 (`landclass_hsk.npy`); synthetic fallback is labelled | Blend input is real; provenance string is returned, never hidden |
| Terrain | **Assumed/coarse** — `dem_hsk.npy` ~ 90 m SRTM-derived, or the explicitly synthetic surface | Drainage cascade runs on it; "screening, not a flood prediction" |
| Massing | **Procedural** — 2,639 envelopes, not surveyed | SVF / corridors are computed on provisional massing; `provisional` chip stays |
| `HEAT_INDEX` | **Hand-authored** — paved 1.00, bare 0.85, green 0.40, wetland 0.20, water 0.05, no published provenance | Stays labelled `heatIndexSource: "Hand-authored screening index; awaiting verification"` until P2-1 lands |
| Curve numbers | **Cited** — TR-55 Table 2-2, HSG **D** assumed | Drainage generation is defensible; soil group is an assumption |
| Retention depths | **Assumed** — `STORAGE_DEPTH_M` (water 1.50, wetland 0.60), `DEPRESSION_STORAGE_M` 0.02 | The mass-balance assertion balances over these assumptions, not over measurements |
| Wind rose | **Real, partial** — AVRG73: NE/ENE/E = 40.8 % | Only those directions carry a frequency; others computed but unweighted |
| FAD | **Geometry, uncalibrated** | Reported as a **ratio** (comparison), never as a velocity ratio |
| Sea level | **Cited** — HKO 2024 projections | Unchanged by this increment |

**Anchoring prerequisite (explicit).** The heat field is **relative, not absolute** and its
0–1 scale is **not yet anchored to real data**. The anchoring *mechanism* (P1-2) and its
machine-readable calibration file are buildable now, but the **reference values themselves are
data-gated**: either a real Landsat 8/9 LST scene over HSK or the HKO 1991–2020 normals must be
ingested (P2-1). Until then the tool must not claim the anchoring is done with real data. Note
that LST is a *skin* temperature and the HKO normals are *air* temperature — the calibration must
state which one it uses and must not conflate them.

---

## 5. Open questions

1. **`compare()` output shape.** A weighted ordering is authorised by Stage 3, but a single
   weighted scalar re-introduces the combined score the project forbids. Does `compare()` return
   (a) a single weighted total with the weights echoed, (b) a partial order / per-axis ranking, or
   (c) both, with the total labelled as the caller's opinion? **Lean:** (c), never a bare total.
2. **Weight normalisation.** Must weights sum to 1, or are they normalised with the normalisation
   stated? What happens when one axis has a zero weight — is it dropped or shown?
3. **Per-cell FAD for the corridor cost.** FAD is site-level today; the corridor needs a per-cell
   cost. Is per-cell FAD `projected_width × height / cell_area`? This defines the whole cost surface.
4. **Mass-balance boundary condition.** Is "discharged" only the grid-edge outlet cells, or is
   there a downstream sink? The tolerance is meaningless until the sink is defined.
5. **HAND "nearest drainage" definition.** Flow accumulation above a threshold, or the existing
   water/wetland class? The two give different cross-checks.
6. **Anchoring reference choice.** LST (skin, 30–100 m) vs HKO normals (air, station) are different
   physical quantities. Which is the reference, and does the calibration map to a range or a value?
7. **SVF resolution.** Azimuth-bin count and horizon search radius — and the tolerance for
   "lone tower ≈ 1". These are the acceptance thresholds in P0-2.

---

*This is a screening-level increment. Every output carries a machine-readable status and a human
warning; nothing here is a temperature, a velocity ratio, or a flood prediction.*
