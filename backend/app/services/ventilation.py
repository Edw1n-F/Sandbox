"""Air-ventilation screening by frontal area density, against published HSK data.

## Why this is not a wind tunnel

It is not, and it does not pretend to be. A wind tunnel or a CFD solve is the
published way to get a velocity ratio; this module does geometry.

What it computes is **frontal area density** (FAD) per wind direction: the total
building face presented perpendicular to the wind, over the site area. Low FAD
means the wind can penetrate; high FAD means the site is sheltered from that
direction. It needs nothing but the building footprints and heights, which the
project already has for all 2,639 envelopes.

**FAD is not a velocity ratio and the two are never interchanged.** VR is
Vp / V-inf and comes from a wind tunnel or CFD. The relationship between the two
depends on upstream wind profile, roughness and flow separation, none of which we
model. So this module reports FAD in its own units, and reports the published
HSK velocity ratios beside it purely as a *reference range* to read against.

## Where the wind directions come from

The one published fact this uses: the AVRG73 air ventilation assessment for
Hung Shui Kiu Area 13 states the dominant wind directions for the site are **NE,
ENE and E, together 40.8% of wind frequency**. No frequency is invented for any
other direction, and every direction is still computed and reported so the shape
of the site is visible even where the frequency is unknown.

## There is no threshold, and that is the finding

An earlier version of this note said the pass/fail bands "have not been
extracted". They were never there to extract. The policy document behind Hong
Kong's own guidance -- Ng & Cheng, *Policies and technical guidelines for urban
planning of high-density cities - air ventilation assessment (AVA) of Hong Kong*,
Building and Environment 44 (2009) 1478-1488 -- states:

    "A design having a higher velocity ratio would be considered as a better
    design than one having a lower velocity ratio. At this stage, only the better
    design option is known, but whether the better design option meets a standard
    cannot be known."

It does publish *empirical reference values*, which are not criteria: velocity
ratios of **0.3** near the waterfront and in open spaces, and **0.05-0.1** in
streets and congested urban spaces. Those live in
:mod:`app.services.design_metrics` as ``VR_OPEN_SPACE`` and
``VR_CONGESTED_STREET``.

**So this module still asserts no threshold, but now for the opposite reason:**
not because we could not find one, but because the guidance says none exists yet.
That is a stronger position than the old note claimed, and it is cited.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

# The geometry primitives are defined once, in design_metrics, and re-exported
# here so this module's callers and tests are unaffected. Two copies of
# projected_width_m existed between these modules, and two implementations of one
# formula is how a screening starts disagreeing with itself -- which is the one
# failure this module exists to rule out.
from app.services.design_metrics import (  # noqa: F401
    CARDINALS,
    VR_COMFORTABLE_PEDESTRIAN,
    VR_CONGESTED_STREET,
    VR_OPEN_SPACE,
    Footprint,
    _TRAVEL,
    cell_fad,
    directional_exposure,
    frontal_area_density as design_frontal_area_density,
    projected_width_m,
    rasterise_height,
)

# scipy is used for exactly one thing -- the least-cost-path corridors below. It
# is imported defensively so that a host where `scipy.sparse` will not load (the
# 1.15.3 `_propack`/dyld failure the requirements pin fixes) gets a loud
# RuntimeError rather than a silently degraded corridor. A degraded corridor is a
# wrong corridor, and this module would rather fail than draw one.
try:  # pragma: no cover - exercised by the import-failure branch, not the happy path
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
except Exception:  # noqa: BLE001
    csr_matrix = None  # type: ignore[assignment]
    dijkstra = None  # type: ignore[assignment]

REFERENCE_PATH = "reference/hsk_ava_reference.json"

# The eight principal wind directions the corridor endpoint defaults to.
PRINCIPAL_DIRECTIONS: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Which grid edge a wind FROM each direction enters through (Stage 5c's
# arrangement half). Meteorological naming: a north-easterly wind comes FROM the
# north-east, so it crosses the district's north-eastern edge moving
# south-westward. This table is presentation -- the entry edge is also derivable
# from the path's first cell -- but stating it per direction keeps a NE reading
# and a SW reading from being interchangeable in the response. ONE table, in the
# same convention as ``_TRAVEL``; a second travel table elsewhere is exactly the
# NE/SW-swap drift the canonical-table rule exists to prevent.
ENTRY_EDGES: dict[str, str] = {
    "N": "north", "NE": "north-east", "E": "east", "SE": "south-east",
    "S": "south", "SW": "south-west", "W": "west", "NW": "north-west",
}

# Meteorological convention: the direction a wind comes FROM. Named explicitly
# because "wind from the east" and "wind towards the east" differ by 180 degrees,
# and getting that backwards points every building at the wrong side.

# M_PER_DEG_LAT used to be defined here and is now unused in this module: the one
# function that needed it moved to design_metrics. Left behind as a second source
# of a physical constant is exactly the drift this consolidation removed.


def load_reference() -> dict[str, Any] | None:
    path: Path = settings.data_dir / REFERENCE_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def frontal_area_density(
    footprints: list[dict[str, Any]],
    site_area_m2: float,
    directions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """FAD per wind direction, from the canonical implementation.

    ``footprints`` items need ``widthM``, ``depthM`` and ``heightM``, and may
    carry ``bearingDeg``. This is now a **thin adapter** over
    :func:`app.services.design_metrics.frontal_area_density`, which is the
    canonical typed version.

    It used to be a second, independent implementation of the same formula. Two
    copies of one geometric calculation is how a screening starts disagreeing with
    itself, and it had already drifted once: when footprints gained an orientation
    the canonical version learned to use it and this one silently did not, so the
    two endpoints would have reported different frontal areas for the same scheme.
    One definition, one adapter.
    """
    typed = [
        Footprint(
            0.0, 0.0,
            float(fp["widthM"]), float(fp["depthM"]), float(fp["heightM"]),
            bearing_deg=float(fp.get("bearingDeg", fp.get("bearing_deg", 0.0)) or 0.0),
        )
        for fp in footprints
    ]
    return design_frontal_area_density(typed, site_area_m2,
                                       directions or CARDINALS)


def ventilation_screening(
    footprints: list[dict[str, Any]],
    site_area_m2: float,
    prevailing: list[str] | None = None,
    combined_frequency_percent: float | None = None,
) -> dict[str, Any]:
    """FAD screening, with the published prevailing directions weighted.

    ``prevailing`` defaults to the AVRG73 directions for Hung Shui Kiu. Only those
    directions carry a frequency; every other direction is computed but reported
    without one, because inventing a wind rose is how a screening turns into a
    fiction.
    """
    dirs = CARDINALS
    fad = frontal_area_density(footprints, site_area_m2, dirs)
    if "error" in fad:
        return fad

    wind = prevailing or ["NE", "ENE", "E"]
    known = [d for d in wind if d in fad["byDirection"]]
    prevailing_fad = (sum(fad["byDirection"][d] for d in known) / len(known)
                      if known else None)
    least = min(known, key=lambda d: fad["byDirection"][d]) if known else None

    return {
        "metric": "frontal area density",
        "units": "dimensionless (projected frontal area / site area)",
        "siteAreaM2": round(site_area_m2, 1),
        "buildingCount": len(footprints),
        "frontalAreaDensity": fad,
        "prevailingDirections": wind,
        "combinedFrequencyPercent": combined_frequency_percent,
        "prevailingMeanFad": (None if prevailing_fad is None
                               else round(prevailing_fad, 4)),
        "leastExposedPrevailingDirection": least,
        "limitation": (
            "Every footprint in this project is an axis-aligned rectangle, "
            "generated from the land-cover raster. For a box the projected width "
            "depends only on |sin| and |cos| of the bearing, so the result is "
            "symmetric about north-south and east-west: NE, SE, SW and NW all "
            "score identically. This screening therefore compares SCHEMES and site "
            "layouts, and compares axes (a north-south wind corridor against an "
            "east-west one), but it CANNOT distinguish a north-easterly from a "
            "south-westerly -- which matters, because the published prevailing "
            "wind here is from the north-east. Distinguishing them needs real "
            "building orientations, and our massing model has none."
        ),
        "note": (
            "Low frontal area density means the wind can penetrate the site; "
            "high means it is sheltered from that direction. This is geometry, "
            "not flow. It is NOT a velocity ratio and no pass/fail threshold is "
            "asserted -- the bands are in Technical Circular 1/06 and its Annex A "
            "Technical Guide, which we have not extracted. Read it as a shape "
            "indicator for comparing schemes, never as an assessment."
        ),
    }


# --------------------------------------------------------------------------- #
# Stage 5b -- least-cost-path wind corridors
# --------------------------------------------------------------------------- #


def _edge_arrays(grids: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The 8-neighbour graph as ``(src, dst, step_cells, indptr)``, built once.

    ``src``/``dst`` are flat cell indices, ``step_cells`` is the step length in
    CELLS (1 for a cardinal neighbour, sqrt(2) for a diagonal), and ``indptr`` is
    the CSR row pointer. The arrays are sorted by ``src`` so each direction's edge
    weights drop straight into a CSR without re-sorting -- the "build the
    structure once" the design asks for.
    """
    rows, cols = grids.rows, grids.cols
    rr, cc = np.meshgrid(np.arange(rows, dtype=np.int64),
                         np.arange(cols, dtype=np.int64), indexing="ij")
    srcs, dsts, steps = [], [], []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r2, c2 = rr + dr, cc + dc
            ok = (r2 >= 0) & (r2 < rows) & (c2 >= 0) & (c2 < cols)
            srcs.append((rr[ok] * cols + cc[ok]).astype(np.int64))
            dsts.append((r2[ok] * cols + c2[ok]).astype(np.int64))
            steps.append(np.full(int(ok.sum()), math.hypot(dr, dc), dtype=np.float64))
    src = np.concatenate(srcs)
    dst = np.concatenate(dsts)
    step_cells = np.concatenate(steps)
    order = np.lexsort((dst, src))
    src, dst, step_cells = src[order], dst[order], step_cells[order]
    counts = np.bincount(src, minlength=rows * cols)
    indptr = np.zeros(rows * cols + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    return src, dst, step_cells, indptr


def least_cost_corridors(
    footprints: list[Footprint],
    grids: Any,
    directions: dict[str, float] | None = None,
    *,
    step_m: float | None = None,
) -> dict[str, Any]:
    """Per-direction least-cost corridor across the frontal-area cost surface.

    The corridor is the cheapest 8-neighbour route from the edge the wind enters
    to the edge it leaves. ``cell_fad`` gives the per-cell blockage; the cost of
    standing in a cell is ``1 + cellFAD``, so the least-cost route runs through
    the OPENEST ground -- which is what a ventilation corridor physically is.

    **A note on the cost formula.** The design and PRD write the surface as
    ``1/(1+FAD)``. Taken literally that INVERTS the selection: a least-cost path
    minimises the sum of ``1/(1+FAD)``, which is smallest where FAD is largest, so
    the corridor would route through the most blocked cells and the design's own
    acceptance test -- "one low-FAD lane returns a corridor along the lane" --
    would fail. The reciprocal is the physically meaningful form and is what ships;
    it is recorded here because the difference is a sign, not a typo.

    The graph is built once; per direction the edge weights are recomputed and
    ``scipy.sparse.csgraph.dijkstra`` is run with every upwind-edge cell as a
    multi-source. Deterministic: no RNG, no wall-clock. Uses
    :func:`app.services.design_metrics.cell_fad` for the cost surface, so there is
    no second geometry.

    Raises ``RuntimeError('scipy.sparse.csgraph unavailable')`` if the import
    failed, rather than silently degrading -- a degraded corridor is a wrong one.
    """
    if dijkstra is None or csr_matrix is None:  # pragma: no cover
        raise RuntimeError("scipy.sparse.csgraph unavailable")

    dirs = directions or {name: CARDINALS[name] for name in PRINCIPAL_DIRECTIONS}
    for name in dirs:
        if name not in CARDINALS or name not in _TRAVEL:
            raise ValueError(
                f"unknown wind direction {name!r}; known directions are "
                f"{sorted(CARDINALS)}")

    rows, cols = grids.rows, grids.cols
    n = rows * cols
    if step_m is None:
        step_m = float(grids.cell_area_m2) ** 0.5
    step_m = float(step_m)

    src, dst, step_cells, indptr = _edge_arrays(grids)
    rr, cc = np.meshgrid(np.arange(rows, dtype=np.int64),
                         np.arange(cols, dtype=np.int64), indexing="ij")
    flat_rr = rr.reshape(-1)
    flat_cc = cc.reshape(-1)

    per_direction: dict[str, Any] = {}
    # The arrangement half of Stage 5c. `cell_fad` is 180-degree periodic, so the
    # NE and SW cost surfaces are bit-identical and their paths are the same
    # cells walked in opposite directions -- exact geometry, not a bug. What IS
    # direction-specific is the arrangement: which edge the wind enters from and
    # the height of the first obstruction it meets there. `directional_exposure`
    # (the canonical march, in design_metrics) already carries that, and its
    # per-direction entries are attached to each corridor so a NE reading and a
    # SW reading are no longer interchangeable. The path itself is NOT re-costed
    # with arrangement information: any such cost would be an invented weight.
    height = rasterise_height(footprints, grids)
    exposure = directional_exposure(height)
    for name, bearing in dirs.items():
        fad = cell_fad(footprints, grids, bearing).reshape(-1).astype(np.float64)
        cost = 1.0 + fad
        weights = 0.5 * (cost[src] + cost[dst]) * step_cells * step_m
        graph = csr_matrix((weights, dst, indptr), shape=(n, n))

        # The travel vector (wind FROM this direction) is the canonical table in
        # design_metrics -- not re-derived here, because a re-derived direction
        # table is exactly how NE and SW get swapped.
        dr, dc = _TRAVEL[name]
        # The upwind edge is the set of cells the wind reaches FIRST: those with
        # the minimum projection onto the travel vector. The downwind edge is the
        # maximum. For a cardinal wind that is a whole row or column (a band of
        # sources, exactly the "all upwind-edge cells as multi-source" the design
        # asks for); for a diagonal it is the single leading corner, which is what
        # forces the corridor to span the district instead of hopping from the top
        # edge to the adjacent left edge one step in -- the degenerate 2-cell NE
        # corridor the first version reported.
        projection = flat_rr * dr + flat_cc * dc
        up_idx = np.flatnonzero(projection == projection.min())
        down_idx = np.flatnonzero(projection == projection.max())

        dist, pred, _nearest = dijkstra(
            graph, directed=True, indices=up_idx,
            return_predecessors=True, min_only=True)

        exit_dist = dist[down_idx]
        finite = np.isfinite(exit_dist)
        if not finite.any():
            per_direction[name] = {
                "corridorCells": 0, "pathLengthM": 0.0, "meanCost": None,
                "blocked": True, "path": [],
                "entryEdge": ENTRY_EDGES[name],
                "firstObstructionMeanM": exposure.get(name, {}).get("meanFirstHitHeightM"),
            }
            continue

        node = int(down_idx[int(np.argmin(exit_dist))])
        chain: list[int] = []
        while node >= 0 and len(chain) <= n:
            chain.append(node)
            node = int(pred[node])
        chain.reverse()
        path = [[int(v // cols), int(v % cols)] for v in chain]

        length = 0.0
        for (r0, c0), (r1, c1) in zip(path, path[1:]):
            length += step_m * math.hypot(r1 - r0, c1 - c0)
        mean_cost = float(np.mean([cost[r * cols + c] for r, c in path]))

        per_direction[name] = {
            "corridorCells": len(path),
            "pathLengthM": round(length, 2),
            "meanCost": round(mean_cost, 4),
            "blocked": False,
            "path": path,
            # Stage 5c: the arrangement half. Same path cells for opposite
            # winds (exact geometry), but different entry edge and different
            # first-obstruction height -- the two readings differ here, and
            # the limitation below says exactly where the difference ends.
            "entryEdge": ENTRY_EDGES[name],
            "firstObstructionMeanM": exposure.get(name, {}).get("meanFirstHitHeightM"),
        }

    return {
        "metric": "least-cost-path ventilation corridors",
        "costSurface": ("1 + cellFAD (frontal-area resistance; the least-cost "
                        "path avoids blockage)"),
        "resolutionM": round(step_m, 4),
        "directions": per_direction,
        "status": "FAD_IS_NOT_A_VELOCITY_RATIO",
        "warning": ("FAD is not a velocity ratio. The corridor is geometry, not "
                    "flow: it shows the openest through-route, not a measured "
                    "wind speed."),
        "limitation": (
            "Geometry, not flow. No velocity, no turbulence, no pressure field. "
            "The least-cost path is the SAME cells for opposite winds: the "
            "frontal-area surface is 180-degree periodic, so the NE and SW "
            "corridors are one path walked in opposite directions -- exact "
            "geometry, not a bug. What IS direction-specific is now attached: "
            "entryEdge (which side the wind enters from) and "
            "firstObstructionMeanM (the arrangement, from the canonical "
            "directional_exposure march). The path cost itself still cannot "
            "tell a north-easterly from a south-westerly, and any cost that "
            "claimed to would be an invented weight."
        ),
    }


def fad_ratio(baseline_fad: float, scenario_fad: float,
              direction: str) -> dict[str, Any]:
    """``scenario / baseline`` frontal area density, for one direction.

    ``ratio`` is ``None`` with ``defined=False`` when the baseline is zero: a ratio
    with a zero denominator is undefined, not infinite. This is a comparison and
    never a velocity ratio.
    """
    baseline = float(baseline_fad)
    scenario = float(scenario_fad)
    defined = baseline > 0.0
    return {
        "direction": direction,
        "baselineFad": baseline,
        "scenarioFad": scenario,
        "ratio": (round(scenario / baseline, 6) if defined else None),
        "defined": defined,
        "note": ("scenario frontal area density / baseline, for one direction. A "
                 "ratio is a comparison, not an absolute velocity ratio."),
        "status": "RELATIVE_COMPARISON_NOT_A_VELOCITY_RATIO",
        "warning": "A ratio is a comparison, not an absolute VR.",
    }
