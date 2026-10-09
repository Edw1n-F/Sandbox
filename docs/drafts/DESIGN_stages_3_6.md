# Design — Stages 3–6 (comparison, heat SVF + anchoring, wind corridors, drainage routing)

**Owner:** 高见远 (Architect) · **Date:** 2026-10-09 · **Status:** design only, no code
**Inputs:** `docs/drafts/PRD_stages_3_6.md`; `STATUS.md`; `docs/HEAT_AND_WIND_PROGRESS.md`;
`Hack4SDG_Heat_Wind_Drainage_Research.md` Ch. 1–5; the eleven source files named in the brief.

**Scope.** Stages 3 (comparison), 4 (heat SVF + anchoring), 5b (wind corridors + FAD ratio),
6 (drainage fill-and-spill + mass balance + HAND). Stage 7 and all frontend work are out.

**The one rule that governs every boundary below.** *One definition of one geometry.*
`app/services/design_metrics.py` is canonical; `ventilation.py` delegates to it. Every new
geometric primitive lands in `design_metrics.py` **once**, and the adapter layer calls it. Where
this design says "delegates", it means: the second module imports the canonical function and
does no geometry of its own.

---

## 0. Environment findings that change the plan

Two facts were verified on the target host before writing this design. Both contradict the brief
and both are load-bearing.

### 0.1 scipy is installed but its binary is broken — fixed by pinning to 1.13.1

The brief states "scipy 1.15.3 … `scipy.sparse.csgraph.dijkstra` is available." It is **not**:

```
>>> import scipy.sparse
ImportError: dlopen(.../scipy/sparse/linalg/_propack/_spropack.cpython-310-darwin.so):
  section '__DATA/__thread_bss' has a zero-fill section type, but offset field is not zero
```

The whole `scipy.sparse` namespace is unimportable, because `scipy.sparse.__init__` imports
`scipy.sparse.linalg`, which imports `_svds`, which imports the `_propack` extension. That
extension is a **SciPy ≥ 1.14 addition** and its wheel is rejected by this host's dyld. A
`--force-reinstall` of the identical 1.15.3 wheel does not fix it (same broken section).

**Resolution (verified working):** pin `scipy<1.14`. `scipy==1.13.1` predates `_propack`, and

```
>>> from scipy.sparse.csgraph import dijkstra
>>> dijkstra(csr_matrix(...), indices=0, return_predecessors=True)   # OK
```

Measured on the real 664×474 grid: one Dijkstra source over 314,736 nodes = **0.05 s**; the CSR
structure builds in 0.06 s. Corridors are therefore affordable (see §4, T7).

> **Action for the engineer (T1):** change `requirements.txt` from `scipy>=1.11` to
> `scipy>=1.11,<1.14` and add a one-line comment naming the `_propack`/dyld incompatibility.
> The venv has already been repaired to 1.13.1 in this session. Do **not** use a fallback
> hand-rolled Dijkstra: two implementations of one shortest-path is the exact drift the project
> forbids, and scipy works once pinned.

### 0.2 The D8 receiver and the raw 5 m surface are computed but never saved

`scripts/hydrology.condition()` returns `filled`, `receiver` (reshaped to grid) and
`accumulation`. `make_wetgrid.compute()` keeps only `accumulation`; `receiver` is discarded, and
only `flow_accum_hsk.npy` is written. Separately, `Grids.elevation_5m` is the **Priority-Flood
filled** surface (make_wetgrid mutates `ov.elevation` to `filled` before saving), so the **raw**
5 m surface is not on disk either.

Both are required by the cascade:
- the **receiver** is the routing graph;
- the raw surface is required because a cell's depression capacity is `filled − raw`.

`compute()` already builds both (`result["raw_elevation"]`, and `condition()`'s `receiver`). The
fix is to **return and save them** (T11): `flow_receiver_hsk.npy` and `flow_raw_elev_hsk.npy`,
next to `flow_accum_hsk.npy`. This changes no depth field and no headline number (the level-pool
solve and the buffer deficit are untouched), so the existing `test_ledger`/`test_constants`
artefact pins still hold.

`Grids` gains `receiver` and `raw_elevation_5m`, both defaulted to `None` and appended **last** so
the frozen dataclass stays constructible and no existing call site changes. When the artifacts
are absent, the routing functions report `status="ROUTING_UNAVAILABLE"` with a reason rather than
crashing — the same "None rather than an exception" pattern `load_grids()` already uses.

---

## 1. Resolved decisions on the seven PRD open questions

Each decision is one paragraph with a one-line justification. Q3 and Q4 define correctness.

### Q1 — `compare()` output shape → **(c): per-axis ranking always; a weighted ordering only under stated weights, with the total named as the caller's opinion and the three axes visible on every row.**

`compare()` returns `byAxis` (an independent ordering on each of the three axes, no weights
needed) plus, **only when weights are supplied**, `ranked` — an ordering by a
`weightedFavourability` computed from each axis min–max normalised to a 0–1 favourability
*within the compared set* and oriented by a stated polarity table. The weighted total is never
called a score, is flagged `weightsAreCallersOpinion: true`, and `combinedScore` stays `None`.
*Justification:* the AVA framework authorises comparison and forbids grading, so an ordering is
allowed exactly when the caller states what they value, and the raw axes must survive beside it
so the weights' cost is visible.

### Q2 — Weight normalisation → **weights need not sum to 1; they are normalised by their sum, and both the supplied and the normalised vectors are echoed.**

Negative weights raise `ValueError`; unknown axis names raise `ValueError` (never widened —
consistent with the `_region_fraction` scope-refusal precedent); a **zero-weight axis is dropped
from the ordering but stays visible** on every row, and its per-axis rank is still computed; if
every weight is zero the call refuses to order. *Justification:* echoing both vectors is what
makes "the weights are the caller's" checkable, and dropping-but-showing a zero weight keeps the
axis visible without letting it influence the order.

### Q3 — Per-cell FAD for the corridor cost → **`cell_fad(θ)[cell] = frontal_raster(θ)[cell] / cell_area_m2`, where `frontal_raster` spreads each footprint's oriented frontal area `projected_width_m(θ, bearing) · height` evenly over the cells it covers, and `cell_area_m2` is the cell's own area (24.99 m²), not the site area.**

Spreading (not stamping) is the part that defines correctness: it makes the area-weighted mean of
`cell_fad` equal the site-level FAD **exactly** —
`Σ_cell cell_fad·A_cell / A_site = Σ_f projected_width_f·height_f / A_site = FAD(θ)` — which is
the identity the test asserts and the check that catches a wrong denominator. Overlapping
footprints are combined by `max`, matching `rasterise_height`'s rule, so the identity is exact on
a non-overlapping layout (the generator's case) and a stated lower bound otherwise.
*Justification:* frontal area density is frontal area per unit *ground* area, so the per-cell
analogue must divide the same cell's frontal area by the same cell's ground area for the two to
be the same quantity at two resolutions.

### Q4 — Mass-balance boundary condition → **the sink is the grid-edge D8 outlets (`receiver == -1`); there is no internal sink, and `runoff_in` is the ledger's per-cell unstored volume (generation minus local retention), which is exactly the quantity `storage_ledger` already sums to `unstoredM3`.**

The cascade routes each cell's `unstored_m` down its receiver chain; a cell that was raised by
Priority-Flood stores up to its depression capacity `(filled − raw)·A` and passes the excess
downstream; whatever reaches a `receiver == -1` cell is discharged. The identity asserted is
`runoff_in == stored + discharged` to relative tolerance, and a **second** identity,
`generation == local_retention + runoff_in`, is asserted separately so an ADR-015 double count
cannot hide inside a balanced cascade. *Justification:* a domain boundary is the only downstream
sink a D8 model has, and anchoring `runoff_in` to the already-published `unstoredM3` keeps the
headline number conserved instead of re-deriving it.

### Q5 — HAND "nearest drainage" → **flow accumulation above a stated threshold, not the water/wetland land class.**

`HAND_DRAINAGE_MIN_ACCUM_CELLS = 1000.0` (≈2.5 ha contributing area at 24.99 m²/cell); the
threshold is echoed as `drainageThresholdCells`. HAND for a cell is `filled[cell] −
filled[nearest downstream drainage cell]`, found by following the D8 receiver chain. *Justification:*
P1-5 requires HAND to be an **independent** cross-check of the routed depth; deriving it from the
water/wetland class would restate the land-cover grid and cross-check nothing, so the drainage
network is defined by the routing itself.

### Q6 — Anchoring reference → **a range, from a stored calibration file, whose schema names the physical quantity (Landsat LST *skin* or HKO air) and never conflates them.**

The mechanism ships now with `anchorSource: "none"`, `anchorRangeC: null`, `calibrationPath: null`
because no real data is on disk; the status stays `RELATIVE_INDEX_NOT_A_TEMPERATURE` and
`HEAT_INDEX` stays labelled hand-authored. A range (not a single value) is the reference because a
field needs a band to be read against. *Justification:* the PRD forbids claiming real anchoring
until real data is ingested, and a named quantity prevents the LST-vs-air conflation the PRD
warns about.

### Q7 — SVF resolution → **16 azimuth bins, 300 m (60-cell) horizon radius; acceptance: lone tower open-field mean SVF > 0.98, uniform block mean SVF < 0.05.**

16 bins reuses the AVA sixteen-direction convention the project already carries, and 300 m is
≈2.3× the tallest envelope (131.1 m), enough that the tallest building still occludes at the
horizon while keeping the fully vectorised search under the 1 s budget. *Justification:* reusing
the 16-direction convention is "one geometry, read again"; a larger radius buys no accuracy on
the open-field case and costs time.

---

## 2. Module boundaries and function signatures

`design_metrics.py` is canonical for **all geometry** (SVF, per-cell frontal area, blend). Every
other module either delegates to it or is non-geometric (routing, ranking).

### 2.1 `backend/app/services/design_metrics.py` — canonical geometry (extend)

New constants:

```python
SVF_AZIMUTH_BINS: int = 16
SVF_SEARCH_RADIUS_M: float = 300.0
HEAT_BLEND_WEIGHTS: dict[str, float] = {"land": 0.5, "svf": 0.3, "shadow": 0.2}
HEAT_STATUS: str = "RELATIVE_INDEX_NOT_A_TEMPERATURE"
HEAT_CALIBRATION_PATH: str = "reference/heat_anchor.json"   # under settings.data_dir
```

New functions:

```python
def sky_view_factor_raster(
    height: np.ndarray,           # [rows, cols] metres, from rasterise_height()
    cell_m: float,
    azimuth_bins: int = SVF_AZIMUTH_BINS,
    search_radius_m: float = SVF_SEARCH_RADIUS_M,
) -> np.ndarray:                  # float32 [rows, cols] in [0, 1]
    """Per-cell SVF = 1 - (1/N) Σ sin(γ_i), γ_i the horizon elevation angle in bin i.

    Observer at ground. A cell under massing is inside the building and gets 0.0.
    Fully vectorised: one shifted-array max per (bin, step); no Python per-cell loop.
    Deterministic; no RNG, no wall-clock.
    """
```

```python
def sky_view_factor(
    height: np.ndarray,
    cell_m: float,
    azimuth_bins: int = SVF_AZIMUTH_BINS,
    search_radius_m: float = SVF_SEARCH_RADIUS_M,
) -> dict[str, Any]:
    """Summary of sky_view_factor_raster(): meanSvf, meanOpenFieldSvf, minSvf,
    maxSvf, azimuthBins, searchRadiusM, searchRadiusCells, buildingCells,
    method, limitation, status, warning. No raster in the dict (JSON-safe)."""
```

```python
def shade_mask(
    footprints: list[Footprint],
    grids: Any,
    lat_deg: float, lon_deg: float, day_of_year: int, hour_local: float,
) -> np.ndarray:                   # bool [rows, cols]
    """The shaded-ground mask at one instant. Extracted verbatim from the body of
    shade_fraction(), which keeps its public return unchanged and now calls this.
    One shadow sweep, one definition; the blend needs the raster, the report needs
    the scalar, and both read this."""
```

```python
def blended_heat_field(
    land_index: np.ndarray,        # [rows, cols] 0..1, from HEAT_INDEX (caller maps it)
    svf: np.ndarray,               # [rows, cols] 0..1
    shaded: np.ndarray,            # [rows, cols] bool/0..1, from shade_mask()
    weights: dict[str, float] | None = None,
) -> np.ndarray:                   # float32 [rows, cols] in [0, 1]
    """w_land·land_index + w_svf·(1 - svf) + w_shadow·(1 - shaded), weights
    normalised to sum 1. Grid-agnostic (arrays only) so it serves both Grids and
    Overlay callers. The HEAT_INDEX table is NOT duplicated here: the caller maps
    the class grid through the one definition in _overlay_common.
    """
```

```python
def heat_field_report(
    land_index: np.ndarray,
    svf: np.ndarray,
    shaded: np.ndarray,
    calibration: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Assemble the served heat dict: mean/max index, weights, per-component means,
    the anchoring block, status and warning. Never converts to °C while
    anchorSource == 'none'."""
```

```python
def load_heat_calibration() -> dict[str, Any] | None:
    """Read data/reference/heat_anchor.json. Schema:
    {quantity: 'LST_skin'|'HKO_air', rangeC: [lo, hi], source, retrieved,
     method, mapsTo: 'range'}. Returns None when absent or unreadable — never a
     hard-coded range. Mirrors ventilation.load_reference()."""
```

```python
def frontal_area_raster(
    footprints: list[Footprint],
    grids: Any,
    bearing_deg: float,
) -> np.ndarray:                   # float32 [rows, cols] m² per cell
    """Rasterise Σ_f projected_width_m(w,d,θ,bearing)·h spread over each footprint's
    cells, combined by max. Canonical per-cell frontal area; the corridor cost and
    the per-cell FAD both come from here."""
```

```python
def cell_fad(
    footprints: list[Footprint],
    grids: Any,
    bearing_deg: float,
) -> np.ndarray:                   # float32 [rows, cols], dimensionless
    """frontal_area_raster(...) / grids.cell_area_m2. Per-cell FAD (Q3)."""
```

> **Do not duplicate `projected_width_m`.** `frontal_area_raster` calls the existing canonical
> `projected_width_m` (design_metrics.py:244), which already takes the footprint's
> `bearing_deg`. This is the single point that keeps the per-cell cost surface consistent with the
> site-level FAD.

### 2.2 `backend/app/services/ventilation.py` — delegates to design_metrics (extend)

No new geometry here. Both new functions consume `design_metrics.cell_fad` / `projected_width_m`.

```python
def least_cost_corridors(
    footprints: list[Footprint],          # TYPED and POSITIONED (lon/lat required)
    grids: Any,
    directions: dict[str, float] | None = None,   # default: the 8 principal directions
    *,
    step_m: float | None = None,          # default = sqrt(grids.cell_area_m2)
) -> dict[str, Any]:
    """Per-direction least-cost corridor on cost = 1/(1+cell_fad(θ)).

    Builds the 8-neighbour CSR structure once; per direction recomputes the edge
    weights and runs scipy.sparse.csgraph.dijkstra with all upwind-edge cells as
    multi-source. The corridor is the predecessor chain from the cheapest downwind
    exit back to the upwind edge. Deterministic. Returns a dict (shape in §3.4).

    Uses design_metrics.cell_fad for the cost surface — no second geometry.
    Raises RuntimeError('scipy.sparse.csgraph unavailable') if the import fails,
    rather than silently degrading: the corridor is the deliverable, and a
    degraded one is a wrong one.
    """
```

```python
def fad_ratio(
    baseline_fad: float,
    scenario_fad: float,
    direction: str,
) -> dict[str, Any]:
    """scenario/baseline for one direction. ratio is None with defined=False when
    baseline_fad <= 0 (a ratio with a zero denominator is undefined, not infinite).
    This is a comparison, never a velocity ratio."""
```

The existing `frontal_area_density` adapter and `ventilation_screening` are unchanged.

> **Note for the endpoint.** `least_cost_corridors` needs positioned footprints; the current
> `get_ventilation` builds positionless dicts (`widthM/depthM/heightM` only). The endpoint must
> build typed `Footprint(lon, lat, width, depth, height)` from the city-model rings — the rings
> already carry lon/lat. The dict-based `ventilation_screening` path is untouched.

### 2.3 `backend/app/services/scenario.py` — comparison (extend)

```python
AXIS_BETTER: dict[str, int] = {"shade": +1, "ventilation": -1, "drainage": -1}
# The direction of a higher FAVOURABILITY score, per axis. Shade is the heat axis's
# solar lever (more shade = less solar gain = the better direction for heat);
# ventilation FAD and drainage unstored volume are lower-is-better. Stated once,
# echoed in every compare() response, so a reader can disagree with the convention
# explicitly rather than inferring it.
```

```python
def compare(
    scenarios: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Rank scenarios against each other. Refuses to rank without weights.

    Each item of ``scenarios`` is an ``evaluate()`` result dict carrying an id
    (``scenarioId`` or ``id``) and ``axes`` with a per-axis ``candidate`` value.
    Ranking reads ``axes[axis]['candidate']``. All scenarios MUST share one
    baseline (the endpoint guarantees this by evaluating them all against the same
    city model); this is a stated precondition, echoed as ``sharedBaseline``.

    weights=None  -> {'refused': True, 'ranked': None, 'byAxis': None, 'reason': ...,
                      'scenarios': [{'scenarioId', 'axes'}...], 'combinedScore': None,
                      'verdict': None}
    weights given -> ordered ``ranked`` rows, each with the three raw axes, the
                      per-axis favourabilities, the contributions and the weighted
                      favourability, plus a full per-axis ``byAxis`` ordering.

    Raises ValueError on an unknown axis name, a negative weight, or an all-zero
    weight vector.
    """
```

```python
def _normalise_weights(weights: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """(as_supplied, normalised_by_sum). Validates axis names against AXIS_UNITS,
    rejects negatives and all-zero. Raises ValueError otherwise."""
```

`evaluate()` gains **no fourth axis and no combined score**. Its ventilation axis gains
`fadRatio` (baseline vs candidate, per prevailing direction) so a massing change is visible
directionally. `combinedScore` stays `None`.

> **Why no heat axis in `evaluate()`.** The blend (P1-1) is a *raster*, served by the heat
> endpoint and the heatmap overlay; it is not a fourth axis. The three axes stay
> shade/ventilation/drainage, `evaluate()` keeps its budget (it does not run SVF), and the
> "never a combined score" guard is untouched.

### 2.4 `backend/scripts/hydrology.py` — routing (extend)

```python
def label_depressions(
    filled: np.ndarray,
    raw: np.ndarray,
) -> dict[str, Any]:
    """8-connected components of (filled > raw + 1e-9). Returns labels (int32
    raster), count, spillElevationM {label: elev} where spill = the minimum filled
    value on the component's rim (its lowest escape), depthM {label: max(filled-raw)},
    depressionCells. Deterministic."""
```

```python
def mass_balance(
    runoff_in_m3: float,
    stored_m3: float,
    discharged_m3: float,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """The P0-4 runtime assertion. residual = runoff_in - (stored + discharged);
    balanced = |residual| <= tol * max(|runoff_in|, 1.0). Returns the residual
    block and RAISES AssertionError when not balanced, so a leak is loud."""
```

```python
def fill_and_spill(
    filled: np.ndarray,
    raw: np.ndarray,
    receiver: np.ndarray,             # flat index per cell, -1 at outlets
    runoff_in_m3: np.ndarray,         # per-cell unstored volume, m³
    cell_area_m2: float,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """Fill-and-spill cascade (Barnes et al. 2021). Process cells in decreasing
    ``filled`` elevation. cap[cell] = max(0, filled - raw) * A. stored = min(inflow,
    cap); route = inflow - stored, pushed to receiver[cell] (or discharged when -1).
    Calls mass_balance(...) and returns depthField (m), storedM3, dischargedM3,
    runoffInM3, depressions, wetCells, maxDepthM. Conserves mass by construction."""
```

```python
def hand(
    filled: np.ndarray,
    receiver: np.ndarray,
    accumulation: np.ndarray,
    drainage_min_accum_cells: float = 1000.0,
) -> dict[str, Any]:
    """Height Above Nearest Drainage. Drainage cells: accumulation >= threshold.
    HAND[cell] = filled[cell] - filled[nearest downstream drainage cell], found by
    following the receiver chain (propagated in decreasing elevation order). Returns
    handM (raster), drainageCells, drainageThresholdCells, min/max/meanHandM.
    Independent of the routed depth; the two are related, never merged."""
```

`condition()` is unchanged (it already returns `receiver`).

### 2.5 `backend/app/services/sandbox_model.py` — Grids + ledger (extend)

```python
@dataclass(frozen=True)
class Grids:
    ...                                # unchanged fields ...
    # appended LAST so existing construction is unaffected:
    receiver: np.ndarray | None = None          # D8 receiver, flat index, -1 at outlets
    raw_elevation_5m: np.ndarray | None = None  # pre-Priority-Flood 5 m surface
```

`load_grids()` loads `flow_receiver_hsk.npy` and `flow_raw_elev_hsk.npy` if present, else `None`.
New helper:

```python
def routed_ledger(grids: Grids, land_class: np.ndarray,
                  rainfall_mm: float = DEFAULT_RAINFALL_MM) -> dict[str, Any]:
    """Run the cascade for one land-class grid and return the routed mass-balance
    block. Returns {'status': 'ROUTING_UNAVAILABLE', 'reason': ...} when receiver or
    raw_elevation_5m is None, rather than raising."""
```

`storage_ledger` is unchanged (its numbers and the buffer deficit must not move).

### 2.6 `backend/scripts/make_wetgrid.py` — wire the cascade (extend)

- `compute()` also returns `receiver` (from `condition_dem`) alongside `accumulation` and
  `raw_elevation` (already returned).
- `main()` (only when `--out` is default) saves `flow_receiver_hsk.npy` and
  `flow_raw_elev_hsk.npy`, runs `fill_and_spill` on the unstored volume, saves
  `routed_depth_hsk.npy`, and writes the mass-balance block into the sidecar. The level-pool
  `depth_field`, the buffer deficit and every existing sidecar field are untouched.
- `to_rgba` and the sensitivity pass are unchanged.

### 2.7 `backend/app/routers/scenes.py` — endpoints (add)

| Method + path | Body / params | Calls | Returns |
|---|---|---|---|
| `POST /scenes/{id}/scenario/compare` | `{scenarios:[{scenarioId, massingOps, interventions}], weights:{...}}` | `evaluate()` per scenario, then `compare()` | compare dict (§3.3) |
| `GET /scenes/{id}/heat-field` | `?hours=9,14&dayOfYear=355` | `rasterise_height`, `sky_view_factor`, `shade_mask`, `blended_heat_field`, `heat_field_report` | heat dict (§3.2) |
| `GET /scenes/{id}/ventilation/corridors` | `?directions=NE,SW` (default 8 principal) | `least_cost_corridors` | corridor dict (§3.4) |
| `GET /scenes/{id}/drainage/routed` | `?rainfallMm=450` | `routed_ledger` | routed dict (§3.5) |

`GET /scenes/{id}/ventilation` keeps its current response and additionally includes `corridors`
and `fadRatio` **only when the city model is present**; the existing keys are unchanged.

---

## 3. Data structures — exact return keys the tests assert on

### 3.1 `sky_view_factor()` / `sky_view_factor_raster()`

`sky_view_factor` dict keys: `meanSvf`, `meanOpenFieldSvf`, `minSvf`, `maxSvf`, `azimuthBins`,
`searchRadiusM`, `searchRadiusCells`, `buildingCells`, `method`, `limitation`, `status`
(`"RELATIVE_INDEX_NOT_A_TEMPERATURE"`), `warning`.

### 3.2 `heat_field_report()` / `GET /heat-field`

```
{
  "meanHeatIndex": float, "maxHeatIndex": float,
  "weights": {"land": .., "svf": .., "shadow": ..},
  "components": {"land": float, "svf": float, "shadow": float},
  "svf": { ... sky_view_factor() dict ... },
  "anchoring": {
    "anchorSource": "none",                     # | "landsat_lst" | "hko_normals"
    "anchorRangeC": null,                       # [lo, hi] when present
    "calibrationPath": null,                    # "reference/heat_anchor.json" when present
    "quantity": null,                           # "LST_skin" | "HKO_air" when present
    "heatIndexSource": "Hand-authored screening index; awaiting verification"
  },
  "status": "RELATIVE_INDEX_NOT_A_TEMPERATURE",
  "warning": "Heat is not a temperature. ...",
  "note": "..."
}
```

### 3.3 `compare()`

No weights:
```
{ "refused": true, "ranked": null, "byAxis": null,
  "reason": "No weights supplied. ...",
  "scenarios": [{"scenarioId": str, "axes": {"shade":.., "ventilation":.., "drainage":..}}],
  "combinedScore": null, "verdict": null, "whyNoVerdict": "..." }
```
With weights:
```
{ "refused": false, "reason": "...",
  "weightsAsSupplied": {...}, "weightsUsed": {...},          # normalised, sums to 1
  "weightsNormalisedBySum": true, "weightsAreCallersOpinion": true,
  "polarity": {"shade": "higher is better", "ventilation": "lower is better",
               "drainage": "lower is better"},
  "normalisation": "min-max within the compared set, oriented by polarity; the set
                    is the caller's, so adding a scheme can move the normalisation",
  "sharedBaseline": true,
  "scenarioCount": int,
  "ranked": [ {"rank": 1, "scenarioId": str,
               "axes": {"shade":.., "ventilation":.., "drainage":..},
               "favourability": {"shade":.., "ventilation":.., "drainage":..},
               "contributions": {"shade":.., "ventilation":.., "drainage":..},
               "weightedFavourability": float,
               "perAxisRank": {"shade": int, "ventilation": int, "drainage": int}} ],
  "byAxis": {"shade": [ids best->worst], "ventilation": [...], "drainage": [...]},
  "combinedScore": null, "verdict": null, "whyNoVerdict": "..." }
```
Refusal cases (raise `ValueError`, surfaced as HTTP 422): unknown axis name; negative weight;
all-zero weights; a scenario missing an id or `axes`.

### 3.4 `least_cost_corridors()`

```
{
  "metric": "least-cost-path ventilation corridors",
  "costSurface": "1/(1+cellFAD)",
  "resolutionM": float,
  "directions": {
    "NE": {"corridorCells": int, "pathLengthM": float, "meanCost": float,
           "blocked": bool, "path": [[row, col], ...]},
    ... one entry per requested direction ...
  },
  "status": "FAD_IS_NOT_A_VELOCITY_RATIO",
  "warning": "FAD is not a velocity ratio. ...",
  "limitation": "Geometry, not flow. ..."
}
```

### 3.5 `routed_ledger()` / `GET /drainage/routed`

```
{
  "runoffInM3": float, "storedM3": float, "dischargedM3": float,
  "massBalance": {"residualM3": float, "relativeResidual": float,
                  "tol": float, "balanced": true},
  "wetCells": int, "maxDepthM": float, "meanDepthInWetAreaM": float,
  "depressions": {"count": int, "maxDepthM": float},
  "hand": {"drainageCells": int, "drainageThresholdCells": float, "meanHandM": float},
  "handVsRouted": {"agreementFraction": float, "note": "reported as a stated relation,
                    never merged into the routed depth"},
  "status": "SCREENING_ROUTED_NOT_A_FLOOD_PREDICTION",
  "warning": "Not a flood prediction. ...",
  "rainfallMm": float
}
```
Unavailable case: `{"status": "ROUTING_UNAVAILABLE", "reason": "flow_receiver_hsk.npy and
flow_raw_elev_hsk.npy not found; run scripts/make_wetgrid.py"}`.

### 3.6 `fad_ratio()` and `mass_balance()`

```
fad_ratio   -> {"direction": str, "baselineFad": float, "scenarioFad": float,
                "ratio": float | None, "defined": bool, "note": str}
mass_balance-> {"runoffInM3": float, "storedM3": float, "dischargedM3": float,
                "residualM3": float, "relativeResidual": float, "tol": float,
                "balanced": bool}          # raises AssertionError when not balanced
```

---

## 4. Ordered task list

Ordered by dependency. Each task names its files, what it depends on, and the acceptance check.
The engineer works top to bottom.

| ID | What | Target file(s) | Depends on | Acceptance check |
|---|---|---|---|---|
| **T1** | Pin `scipy>=1.11,<1.14`; note the `_propack`/dyld reason. | `backend/requirements.txt` | — | `python -c "from scipy.sparse.csgraph import dijkstra"` succeeds; a 3-node graph returns `[0,1,2]`. |
| **T2** | `sky_view_factor_raster` + `sky_view_factor` + constants. | `design_metrics.py` | — | `test_design_metrics`: lone tower open-field mean > 0.98; uniform block mean < 0.05; monotone non-increasing as surrounding height rises; < 1 s. |
| **T3** | `shade_mask` (extracted from `shade_fraction`, which then delegates) + `blended_heat_field` + `heat_field_report` + `load_heat_calibration` + `HEAT_STATUS`. | `design_metrics.py` | T2 | `test_design_metrics`: `shade_fraction` output byte-identical before/after the refactor; greening-only lowers the field; massing-only lowers it via SVF+shadow; field ∈ [0,1]; ramp/disclaimer unchanged; no calibration file → `anchorSource=="none"`, status `RELATIVE_INDEX_NOT_A_TEMPERATURE`. |
| **T4** | Render the blend in the overlay; add anchoring fields to the sidecar. | `make_heatmap.py` | T3 | Script runs; sidecar carries `anchorSource/anchorRangeC/calibrationPath/heatIndexSource/status/warning`; `heatIndexSource` still reads "Hand-authored screening index; awaiting verification". |
| **T5** | `AXIS_BETTER`, `compare()`, `_normalise_weights()`. | `scenario.py` | — | `test_scenario`: `compare(scens)` → `ranked is None, refused is True, reason`; with weights → ordering, weights echoed, list-order invariant, each row carries three axes; unknown axis/negative/all-zero raise. |
| **T6** | `frontal_area_raster` + `cell_fad`. | `design_metrics.py` | — | `test_design_metrics`: mean-preservation `Σ cell_fad·A_cell / A_site == FAD(θ)` on a non-overlapping synthetic layout; cost monotone in FAD. |
| **T7** | `least_cost_corridors` + `fad_ratio`. | `ventilation.py` | T1, T6 | `test_ventilation`: synthetic low-FAD lane → path along the lane; NE ≠ SW on the real layout; `fad_ratio == 1.0` identical, `> 1` where blockage rises; deterministic; < 1 s. |
| **T8** | `label_depressions`. | `hydrology.py` | — | `test_hydrology`: synthetic two-pit DEM → exactly 2 depressions with correct spill elevations; labels contiguous; deepest basin matches filled. |
| **T9** | `mass_balance` + `fill_and_spill`. | `hydrology.py` | T8 | `test_hydrology` + `test_ledger`: identity within tol on a synthetic flat basin and the real grid; leaked cell raises; more rainfall raises `runoffInM3`. |
| **T10** | `hand`. | `hydrology.py` | T9 | `test_hydrology`: HAND = 0 on drainage cells, > 0 elsewhere; monotone along a synthetic slope; HAND-vs-routed reported as a relation, not merged. |
| **T11** | `compute()` returns `receiver`; save `flow_receiver_hsk.npy`, `flow_raw_elev_hsk.npy`, `routed_depth_hsk.npy`; run the cascade; write the mass-balance block into the sidecar. | `make_wetgrid.py` | T9 | Script runs; new artefacts written; `wetgrid_hsk.npy` and the buffer deficit are byte-unchanged; sidecar gains the mass-balance block. |
| **T12** | `Grids.receiver`, `Grids.raw_elevation_5m`, `load_grids()` loads them, `routed_ledger()`. | `sandbox_model.py` | T11 | `test_ledger`: `load_grids()` still returns a `Grids`; `routed_ledger` returns the mass-balance block when the artefacts exist, else `ROUTING_UNAVAILABLE`. |
| **T13** | Four endpoints; extend `get_ventilation` with `corridors`/`fadRatio`. | `app/routers/scenes.py` | T3, T5, T7, T12 | `test_contract`: each new route returns 200 with the §3 keys; 422 on a bad weight/axis; no `verdict`/`pass`/`fail` field. |
| **T14** | Tests: extend `test_design_metrics`, `test_ventilation`, `test_ledger`, `test_scenario`, `test_contract`; add `test_hydrology.py`. | `backend/tests/*` | T2–T13 | All new checks pass; the existing 271 backend tests still pass. |
| **T15** | Update the two status docs with the new stages, the scipy pin, and the labels. | `STATUS.md`, `docs/HEAT_AND_WIND_PROGRESS.md` | T14 | Stages table reflects done/partial accurately; the honesty section gains the new labels. |

**Critical path:** T1 → T6 → T7; T8 → T9 → T11 → T12; T2 → T3 → T4. T5 is independent and cheap.

---

## 5. Test plan

New tests follow the existing convention exactly: plain `check(label, ok, detail)` counters,
`main() -> int`, run as `python tests/test_x.py`, importing via the same `BACKEND_DIR` /
`BACKEND_DIR/"scripts"` `sys.path` insert.

### 5.1 Extended suites

- **`tests/test_design_metrics.py`** (+ ~12 checks):
  - SVF: lone tower open-field mean > 0.98; uniform block mean < 0.05; `minSvf` at a tower cell
    ≈ 0; monotone non-increasing as surrounding height rises; `azimuthBins == 16`,
    `searchRadiusM == 300.0`; deterministic across two runs.
  - Blend: greening-only lowers the mean; massing-only lowers it (SVF + shadow); field ∈ [0,1];
    the existing ramp and disclaimer strings unchanged.
  - Anchoring: with no calibration file, `anchorSource == "none"`, `anchorRangeC is None`,
    `calibrationPath is None`, status `RELATIVE_INDEX_NOT_A_TEMPERATURE`, and
    `heatIndexSource` still "Hand-authored screening index; awaiting verification".
  - Per-cell FAD: mean-preservation identity on a non-overlapping synthetic layout (this is the
    Q3 correctness check); cost monotone in FAD.
- **`tests/test_ventilation.py`** (+ ~8 checks):
  - A synthetic district with one low-FAD lane returns a corridor along the lane.
  - NE corridor ≠ SW corridor on the real layout; corridor cost monotone in FAD.
  - `fad_ratio == 1.0` for identical schemes; `> 1` where blockage rises; `defined is False`
    when the baseline FAD is 0.
  - Deterministic; < 1 s.
- **`tests/test_ledger.py`** (+ ~6 checks):
  - `mass_balance` identity within relative tol on a synthetic flat basin (all stored,
    discharged ≈ 0) and on the real grid (skip with the existing "(skipped: run
    scripts/make_wetgrid.py first)" pattern when the artefacts are absent).
  - A deliberately leaked cell makes the assertion **raise**.
  - More rainfall raises `runoffInM3`; `generation == local_retention + runoff_in`.
  - The unmodified buffer deficit still equals the sidecar `unstoredVolumeM3` (regression pin).
- **`tests/test_scenario.py`** (+ ~8 checks):
  - `compare(scens)` refuses; `compare(scens, weights=...)` orders, echoes weights, is invariant
    to input list order, and every ranked row carries the three axes.
  - Polarity: a scenario strictly worse on ventilation and equal elsewhere ranks lower under a
    ventilation-only weight.
  - Refusals: unknown axis, negative weight, all-zero weights raise `ValueError`.
  - `evaluate()` still emits no scalar total and `combinedScore is None`.
- **`tests/test_contract.py`** (+ ~4 checks): the four new routes return 200 with the §3 keys;
  422 on a bad weight/axis; no truthy `verdict`/`pass`/`fail` anywhere in any new response.

### 5.2 New suite

- **`tests/test_hydrology.py`** (new, ~12 checks):
  - `label_depressions`: synthetic two-pit DEM → exactly 2 depressions with the correct spill
    elevations; labels contiguous; the deepest basin's depth matches `max(filled − raw)`.
  - `fill_and_spill`: on a synthetic flat basin water is stored and discharge ≈ 0; on a synthetic
    slope nearly all volume discharges; depth ≤ spill level; deterministic; `mass_balance` holds.
  - `mass_balance`: a leaked cell raises.
  - `hand`: HAND = 0 on drainage cells and > 0 elsewhere; monotone along a synthetic slope.
  - No `time`/`random` import in `hydrology.py` (determinism grep, mirroring P0-7).

### 5.3 Existing tests that must keep passing

All 271 backend tests. Two are load-bearing pins this design must not move:
`test_ledger` (buffer deficit == sidecar `unstoredVolumeM3`) and `test_constants`
(CN table, `254`-not-`2540`, ADR-015 retention classes). The cascade is additive; the level-pool
depth field and the ledger arithmetic are untouched.

**No existing call site changes.** `Grids` gains two defaulted fields (appended last); `compute()`
gains a return key; `evaluate()` gains a sub-key inside the ventilation axis. None of these is a
signature change, so no existing test call site must change.

---

## 6. Honesty labels — exact `status` and `warning` per output

Machine-readable `status` from a closed set; non-empty human `warning`. Existing statuses are
reused where the meaning matches.

| Output | `status` | `warning` (must contain) |
|---|---|---|
| SVF, blend, heat field | `RELATIVE_INDEX_NOT_A_TEMPERATURE` | "Heat is not a temperature." + "Screening-level; relative, not absolute." |
| Anchoring (no data) | `RELATIVE_INDEX_NOT_A_TEMPERATURE` (unchanged) | "No real LST/HKO calibration is present; the scale is not anchored to observed data." |
| Anchoring (data present) | `ANCHORED_TO_PUBLISHED_REFERENCE` | names the quantity ("Landsat LST (skin)" or "HKO normals (air)") and the source. |
| Corridors | `FAD_IS_NOT_A_VELOCITY_RATIO` | "FAD is not a velocity ratio." |
| `fad_ratio` | `RELATIVE_COMPARISON_NOT_A_VELOCITY_RATIO` | "A ratio is a comparison, not an absolute VR." |
| Routed depth / mass balance | `SCREENING_ROUTED_NOT_A_FLOOD_PREDICTION` | "Not a flood prediction." + "Not engineering-grade." |
| HAND | `SCREENING_HAND_CROSSCHECK_NOT_A_FLOOD_PREDICTION` | "Not a flood prediction." |
| `compare()` | `RELATIVE_RANKING_NOT_A_PASS_FAIL` | "The weights are the caller's, not the project's." |
| Routing unavailable | `ROUTING_UNAVAILABLE` | "Run scripts/make_wetgrid.py to generate the receiver and raw surface." |

Standard phrases from P1-6, used verbatim: "Screening-level; relative, not absolute",
"Not engineering-grade", "Not a flood prediction", "FAD is not a velocity ratio",
"Heat is not a temperature". No output carries a truthy `verdict`, `pass` or `fail`.

---

## 7. Risks — each is a plausible wrong number and the check that catches it

This project has a documented history of exactly these failures (a saturated shadow sweep, a
swapped direction table, a 2540-vs-254 CN factor). Each risk below names the specific check.

1. **scipy silently unavailable.** `import scipy.sparse.csgraph` fails on the shipped 1.15.3
   wheel; a fallback would be a second Dijkstra implementation. **Check:** T1's import test + the
   `<1.14` pin. `least_cost_corridors` raises `RuntimeError` rather than degrading.
2. **Per-cell FAD denominator wrong** (site area instead of cell area, or stamping instead of
   spreading). The cost surface stays plausible and only its scale is wrong. **Check:** the Q3
   mean-preservation identity `Σ cell_fad·A_cell / A_site == FAD(θ)` on a non-overlapping layout.
3. **Direction table swapped in the corridor cost.** NE ≡ SW again. **Check:** synthetic
   NE-oriented low-FAD lane vs SW; NE ≠ SW on the real layout; cost monotone in FAD.
4. **Mass-balance sink mis-defined** (internal sinks counted, so the identity holds trivially or
   never). **Check:** flat basin discharges ≈ 0 while a slope discharges ≈ all; a leaked cell
   raises; `runoffInM3` rises with rainfall.
5. **ADR-015 double count inside a balanced cascade.** A retention class generating runoff would
   inflate `runoff_in` while the identity still holds. **Check:** the second identity
   `generation == local_retention + runoff_in` plus the existing
   `test_no_class_is_counted_as_both_generation_and_retention`.
6. **Depression capacity sign/definition wrong** (`cap = filled − raw` inverted) → water never
   ponds or ponds everywhere. **Check:** synthetic pit stores > 0 with depth ≤ spill; flat slope
   stores 0.
7. **SVF horizon convention wrong** (observer at building top, or an azimuth sign error) → SVF
   inverted or trivially 1. **Check:** lone tower open-field ≈ 1 with `minSvf` at the tower ≈ 0;
   uniform block ≈ 0; monotone as surrounding height rises. (SVF is azimuth-symmetric, so a sign
   error shows up as the monotonicity check failing.)
8. **SVF resolution too coarse** so the lone tower is never sampled and the "≈ 1" test passes
   vacuously. **Check:** `azimuthBins`/`searchRadiusM` pinned to 16/300 and `minSvf` at a tower
   cell asserted ≈ 0.
9. **Anchoring claims real data when none is present.** **Check:** with no calibration file,
   `anchorSource == "none"`, status `RELATIVE_INDEX_NOT_A_TEMPERATURE`, `heatIndexSource` still
   hand-authored; the test must not require a real file.
10. **`compare()` polarity inverted** (higher FAD ranked better) → the ordering is backwards but
    plausible. **Check:** a scenario strictly worse on ventilation and equal elsewhere ranks
    lower under a ventilation-only weight.
11. **Weights echoed ≠ weights used.** **Check:** `weightsUsed` sums to 1 and is proportional to
    `weightsAsSupplied`; a zero-weight axis does not move the order.
12. **Shadow-sweep saturation resurfacing inside the blend.** **Check:** the existing
    saturation test in `test_design_metrics` (west vs all) still passes; the blend consumes the
    corrected `shade_fraction` output unchanged.
13. **Non-determinism** (wall-clock/RNG). **Check:** grep the new modules for `time`/`random`;
    a frozen scenario reproduces identical numbers across two runs.
14. **Seconds budget blown** (SVF + shadow + FAD + ledger inside one request). **Check:** T7 and
    T2 timing checks (< 1 s); `evaluate()` does **not** run SVF, so its budget is unaffected.

---

## 8. Where I disagree with the PRD

1. **P0-3 says "via `scipy.sparse.csgraph.dijkstra`" and the brief says scipy is available.** It
   is not, on the shipped wheel. I resolve it by **pinning `scipy<1.14`** (T1), not by adding a
   fallback Dijkstra — a second shortest-path implementation is the exact duplication the project
   forbids. This is a real change to `requirements.txt` that the PRD does not mention.
2. **P1-1/P1-2 name `make_heatmap.py` + `design_metrics.py` for the blend, but `make_heatmap`
   holds an `Overlay`, not a `Grids`.** `Overlay` lacks `cell_of`/`cell_area_m2` attribute, so a
   `Grids`-typed signature cannot take it. I resolve this by making the new geometry
   **grid-agnostic** (`height`/arrays + `cell_m`), which also keeps `design_metrics` free of the
   `HEAT_INDEX` table (it stays defined once in `_overlay_common`). This is a deliberate
   deviation from a `grids: Any` signature for the new functions.
3. **The PRD implies the heat blend becomes visible through the scenario evaluator, but adding a
   fourth axis would violate the three-axis guard and renaming `shade` would break existing
   tests.** I keep the three axes and serve the blend as a **raster** via a dedicated heat
   endpoint and the overlay. `evaluate()` is unchanged in axis count.
4. **P1-4 says the cascade targets `hydrology.py` + `make_wetgrid.py`, but the cascade needs the
   raw 5 m surface and the receiver, neither of which is saved.** I add two artefacts
   (`flow_receiver_hsk.npy`, `flow_raw_elev_hsk.npy`) — a small extension of P1-4's file list,
   flagged here rather than assumed.
5. **P0-1's `compare(scenarios, weights=None)` is under-specified about what a "scenario" is.** I
   fix it to: each item is an `evaluate()` result with an id and `axes`, and all must share one
   baseline. Without this, `compare()` could silently rank incomparable numbers.
6. **P1-5's HAND is described as "computed independently", but the PRD's Q5 offers the
   water/wetland class as an option.** I reject the class option outright: it would not be
   independent of the routing it cross-checks.

---

## 9. Appendix — why the three-axis guard survives every new surface

- `evaluate()`: still three axes, still `combinedScore: None`, still `verdict: None`. No SVF, no
  fourth axis.
- `compare()`: orders only under stated weights, echoes them, keeps the three axes and their
  per-axis ranks on every row, and names the total `weightedFavourability` (never `score`).
- Heat, corridors, routing and HAND each carry a `status` from §6 and a non-empty `warning`, and
  none emits a pass/fail.
- No new module imports `time` or `random`; every new number is a pure function of the committed
  rasters and the request.
