# 5. Way Forward: A Deterministic numpy/pillow/scipy Build Roadmap with Testable Acceptance Criteria and Honesty Labels

## 5.1 The organising principle

The research in Chapters 1–4 collapses three apparent features into one. Hong Kong's own air-ventilation guidance states that a designer controls only two variables — *"maximize wind speed and minimize solar radiation"* — and both are functions of the same geometry ([Ng & Cheng, 2009, *Building and Environment* 44, 1478–1488](https://www.sciencedirect.com/science/article/pii/S0360132308001455); the policy behind [Technical Circular 1/06](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf)). The roadmap therefore has a single organising rule: **one geometry, three evaluators; deterministic; seconds-on-a-laptop; every stage ships with an acceptance test and an honesty label.** Building heat, wind and drainage as three separate models would encode the same geometry three times and let them silently disagree. The existing code already honours this: the physics lives once in `_overlay_common` — the shared land-class grid, curve-number table and storage partition — which `sandbox_model.py`, `make_heatmap.py`, `make_wetgrid.py` and `make_citymodel.py` (among others) import rather than re-implement, and the storage ledger is shared rather than re-derived.

The ordering rule is dependency, not importance: **a stage may only begin when the raster and the interface it consumes already exist and are tested.** Stages 0–3 build the skeleton (metrics → one `evaluate` → comparison); Stages 4–6 fill the three axes behind that fixed interface; Stage 7 exposes the result to the language model. This matches, and extends, the six-stage plan already recorded in `docs/HEAT_AND_WIND_PROGRESS.md` — Stage 1 (`design_metrics.py`) is complete, Stage 2 (`evaluate`) is next.

## 5.2 The staged roadmap

| Stage | What (module / function) | Files | Acceptance test | Honesty label |
|---|---|---|---|---|
| **0** | Research, cited and recorded | `docs/HEAT_AND_WIND_PROGRESS.md` | Every rule carries a source | — |
| **1** | `design_report()` — geometry metrics each tied to a published rule (height dispersion, site coverage, ground-level void fraction, shade fraction) | `app/services/design_metrics.py` | `test_design_metrics.py` (37 checks) | "geometry, not flow; no verdict" |
| **2** | One `evaluate(scenario)` returning **three separate deltas** (heat, wind, drainage) | new `app/services/evaluate.py`; reuse `ledger_for_scenario` | Three keys present; never a single score | "three axes, not one number" |
| **3** | Scenario-vs-baseline comparison (the relative judgement the AVA document asks for) | `evaluate.py` + API | Δ sign correct; baseline reproducible | "relative, not a pass/fail" |
| **4** | Heat: rasterise massing → solar position → shadow mask → SVF → blend + anchor | extend `make_heatmap.py`, `design_metrics.solar_position` | SVF sanity; monotone in shade | "relative index, not °C" |
| **5** | Wind: oriented projected width (symmetry-breaking) → directional FAD → LCP corridors → FAD ratio | extend `ventilation.py`, `design_metrics.projected_width_m` | NE ≠ SW; ratio monotone | "FAD is not a velocity ratio" |
| **6** | Drainage: D8 fill-and-spill + mass balance + HAND cross-check | extend `hydrology.py`, `make_wetgrid.py` | Mass balance; HAND cross-check | "screening, not a flood prediction" |
| **7** | Expose to the LLM as an **objective/constraint**, never a number | `app/services/ai_plan.py`, `sandbox_model.py` | Model emits no arithmetic | "the code does the arithmetic" |

Stage 5 touches a live duplication the roadmap must resolve rather than extend: `projected_width_m` and `frontal_area_density` currently exist in **both** `ventilation.py` (dict-based footprints) and `design_metrics.py` (the typed `Footprint` dataclass), and `test_design_metrics.py` records the split in as many words — *"two conventions now exist for the same calculation; the typed one is the one new code should use."* The typed `design_metrics.py` version is therefore **canonical**; the `ventilation.py` copy is the legacy path, so the oriented-width work lands in one place (and `ventilation.py` should ultimately delegate to the canonical copy) rather than adding a third encoding of the same geometry.

Stage 4's SVF term follows the standard horizon-search definition of sky-view factor ([Zakšek et al., 2011](https://www.mdpi.com/2072-4292/3/2/398/notes)), the same coupling SOLWEIG uses ([SOLWEIG / UMEP](https://umep-dev.github.io/solweig/)) but without its radiation chain. Stage 6 routes on the D8 receiver ([O'Callaghan & Mark, 1984](https://www.sciencedirect.com/science/article/pii/S0734189X84800110)) that `hydrology.py` already computes, using the fill-and-spill cascade of ([Barnes et al., 2021](https://esurf.copernicus.org/articles/9/105/2021/)), and cross-checks with Height Above Nearest Drainage ([Nobre et al., 2011](https://www.sciencedirect.com/science/article/pii/S0022169411002599); [NOAA HAND factsheet](https://www.weather.gov/media/owp/operations/HAND_factsheet.pdf)).

## 5.3 Testable acceptance criteria

Each criterion is a runnable check, extending an existing suite:

- **Symmetry-broken (Stage 5).** A NE-oriented scheme must **not** score identical to a SW one. Today it does, because every footprint is axis-aligned and projected width reduces to `a·|sinφ| + b·|cosφ|` ([FAI review, 2022](https://www.sciencedirect.com/science/article/pii/S0360132322008186)); the fix is a true per-cell oriented width. Extends `test_ventilation.py`.
- **Monotonicity, per axis.** More pavement ⇒ more unstored runoff; more shade ⇒ lower heat index; lower FAD ⇒ better ventilation. Extends `test_ledger.py` and `test_design_metrics.py`.
- **Mass-balance assertion (Stage 6).** Runoff in = stored + discharged, to tolerance — a runtime check *and* a unit test, not a comment. Extends `test_ledger.py`.
- **SVF sanity (Stage 4).** A lone tower has SVF ≈ 1; a dense block ≈ 0. Extends `test_design_metrics.py`.
- **Determinism.** A frozen golden scenario reproduces byte-identical numbers across runs; no wall-clock or RNG dependence.
- **Seconds budget.** Each `evaluate()` completes under ~1 s on a laptop (the ledger already runs in 0.9 s per `docs/RUNG_AND_BUSINESS.md`), so the interactive sandbox stays interactive.
- **Constants pinned.** Every curve number and heat index value is pinned to its cited row, as `test_constants.py` already does.

## 5.4 Data and anchoring prerequisites

| Input | Status today | What stops it being a placeholder |
|---|---|---|
| Terrain | ~90 m, SRTM-derived / synthetic (`dem_hsk.npy`, 37×28) | A real bare-earth DTM — CEDD LiDAR or the [Lands Dept 5 m DTM](https://data.gov.hk/en-data/dataset/hk-landsd-openmap-5m-grid-dtm) |
| Massing | 2,639 **procedural** envelopes, not surveyed | Surveyed footprints; the phase signal exists in CSDI but needs an ID join, not a spatial coincidence |
| Land cover | [ESA WorldCover 10 m v200](https://esa-worldcover.org/en) — a real pretrained product | Already real; no upgrade needed |
| Heat index | Hand-authored, unverified | A real [Landsat 8/9 LST](https://science.nasa.gov/mission/landsat/tirs/) scene + [HKO 1991–2020 normals](https://www.hko.gov.hk/en/cis/normal.htm) for anchoring |
| Wind rose | One published frequency (AVRG73: NE/ENE/E = 40.8%) | The full [AVRG73](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf) rose |

Obtainable now: Landsat scenes, HKO normals, ESA WorldCover. **Request-gated:** the CEDD LiDAR and the 5 m DTM ([CEDD GeoOpenData](https://www.ginfo.cedd.gov.hk/GEOOpenData/tc/LIDAR.aspx)) — the rung-C substitution declared in `docs/RUNG_AND_BUSINESS.md`.

## 5.5 Risks and mitigations

- **False precision.** Mitigation: every number carries a label; nearest-neighbour, never interpolated.
- **The CN table is the largest lever.** Mitigation: publish the ±20% band, as `make_wetgrid.py --sensitivity` already does, rather than defend one value ([SCS-CN critique, 2024](https://link.springer.com/article/10.1007/s13157-024-01873-w)).
- **Procedural massing.** Mitigation: the `provisional` chip stays; no district claim without aggregation.
- **The ~90 m DEM.** Mitigation: label it a neighbourhood value, not a spot height.
- **The single-score trap.** Mitigation: three separate deltas; any ordering must state its weights.
- **Performance regressions.** Mitigation: the seconds budget is a test, not an aspiration — the shade sweep's 22 s → 0.01 s fix (grouping by footprint span) is the precedent.
- **A user mistaking a screening number for a statutory one.** Mitigation: the labelling is not optional; it is the last line of defence.

## 5.6 Honesty labels

Each output must carry a machine-readable `status` plus a human `warning`, following the pattern already in the PNG sidecars — `RELATIVE_INDEX_NOT_A_TEMPERATURE` in `make_heatmap.py`, `SCREENING_VIEW_SYNTHETIC_TERRAIN` in `make_wetgrid.py`. The wording pattern to standardise:

- **Screening-level; relative, not absolute.**
- **Not engineering-grade.**
- **Not a flood prediction.**
- **FAD is not a velocity ratio.**
- **Heat is not a temperature.**

## 5.7 If you only do three things

1. **Build `evaluate(scenario)` with three separate deltas** (Stage 2) — it is the interface everything else plugs into, and the one easiest to corrupt into a single score.
2. **Break the wind symmetry** (Stage 5) — without it the tool cannot tell the prevailing NE wind from its opposite, which is the axis that matters here.
3. **Add the drainage mass-balance assertion** (Stage 6) — a cheap, runnable guarantee that turns a level-pool solve into a defensible cascade.

### Summary

The way forward is a single deterministic geometry, read three ways, shipped in eight dependency-ordered stages. Each stage names its files, its acceptance test and its honesty label; the tests are runnable, not aspirational; the data upgrades are named with their honest status; and the honesty labels are standardised to match the sidecar pattern the code already uses. The three highest-leverage moves are the coupled `evaluate`, the wind symmetry-break, and the drainage mass balance — in that order.
