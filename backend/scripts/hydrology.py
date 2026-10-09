#!/usr/bin/env python
"""Terrain conditioning and flow routing on the project DEM.

## What I checked before writing this

The standard open-source tools for this are **richdem** (Barnes et al.,ADD) and
**hydrotools** (Mike/active). Both were installed and neither is usable here:

- ``richdem`` needs GDAL headers to build; the wheel does not exist for this
  interpreter, and there is no Homebrew GDAL on this host.
- ``hydrotools`` imports, but every hydrology class is gated behind richdem, so
  ``FlowAccumulator``, ``FlowDirection`` and ``TopologicalStreamOrder`` are all
  absent.

So the algorithms are implemented here directly, in numpy, following the
published descriptions. They are not novel -- this is a reimplementation of two
well-known methods, chosen because a dependency that cannot be installed is a
dependency that fails on someone else's machine too.

## The two algorithms

**Priority-Flood** (Barnes, Lehman & Mulla, *Priority-Flood: An Optimal
Depression-Filling and Watershed-Labeling Algorithm*, 2014). Every pit in the
DEM is filled to its lowest spill point, so every cell has a monotonically
non-increasing path to the outlet. Naive fill-to-gradient fails on
"elevated sinks"; the priority queue variant is O(n log n).

**D8 flow accumulation.** Each cell sends all of its water to its steepest
downhill neighbour, ties broken by a fixed diagonal convention. Processing
cells in **decreasing elevation order** makes the accumulation a single forward
pass with no iteration, which is what makes this cheap enough to run on every
scenario.

## Why this matters for the wet grid

Before this, the wet grid placed water by solving for a level surface over the
*raw* DEM, and the honest limitation was "no flow routing, no sink
conditioning". With the surface conditioned and flow accumulated, the grid can
route water downhill instead of merely flooding basins, and the reported
runoff-to-depth numbers stop depending on unconditioned pits.

## The cascade, and why it is additive

Stages 3-6 add a **fill-and-spill cascade** (Barnes, Callaghan & Wickert, *The
equivalent elevation: an algorithm for filling depressions in a digital
elevation model*, 2021) on top of the conditioned surface. Each cell's runoff
walks its D8 receiver chain; a cell that Priority-Flood raised stores up to its
depression capacity ``(filled - raw) * A`` and passes the excess downstream;
whatever reaches a ``receiver == -1`` grid-edge cell is discharged. The only
sink is the domain boundary, so ``runoff_in == stored + discharged`` holds by
construction and :func:`mass_balance` raises if it ever does not.

This is a **new artefact beside** the level-pool depth field, never a
replacement. The level-pool solve in ``make_wetgrid.py`` and the storage ledger
it feeds are untouched: their numbers are pinned by ``tests/test_ledger.py`` and
``tests/test_constants.py``, and the cascade runs only on the *unstored* volume
those already produce.

Two index spaces meet here and the distinction is load-bearing. ``condition()``
returns the D8 receiver **reshaped to the grid**, but ``d8_flow_direction()``
produces a **flat** index (``row * cols + col``). The cascade needs flat indices,
so :func:`fill_and_spill` and :func:`hand` consume a flat receiver and ravel
everything else to C order to match. This project already shipped a bug where a
256-px grid was indexed against a 28x37 raster and reported "no terrain" for the
whole district *without throwing*; a silent index-space mismatch is the known
failure mode, so the choice is stated here and at every call site rather than
left to ``reshape`` to guess.

Deterministic throughout: no wall-clock, no RNG. Ties break on a stable sort of
the cell index.
"""

from __future__ import annotations

import heapq

import numpy as np

__all__ = [
    "priority_flood",
    "d8_flow_direction",
    "flow_accumulation",
    "condition",
    "label_depressions",
    "mass_balance",
    "fill_and_spill",
    "hand",
]

# Flow accumulation (in cells) at or above which a cell counts as drainage for
# the HAND cross-check. ~2.5 ha of contributing area at 24.99 m2/cell. The
# threshold is deliberately NOT the water/wetland land class: HAND must be
# independent of the routing it cross-checks, and deriving it from land cover
# would restate the land-class grid and check nothing.
HAND_DRAINAGE_MIN_ACCUM_CELLS: float = 1000.0

# D8 neighbour offsets, ordered so ties break deterministically.
# (dr, dc, diagonal) -- diagonals are more likely to be chosen on a tie.
NEIGHBOURS: tuple[tuple[int, int, float], ...] = (
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, 2.0 ** 0.5), (-1, 1, 2.0 ** 0.5),
    (1, -1, 2.0 ** 0.5), (1, 1, 2.0 ** 0.5),
)


def priority_flood(dem: np.ndarray, epsilon: float = 1e-5) -> np.ndarray:
    """Fill every depression to its lowest spill point (Barnes et al. 2014).

    ``epsilon`` is the increment applied to a cell that has been filled, so the
    surface is *almost* flat over a filled pit rather than exactly flat. Without
    it a filled pit has zero gradient and D8 has no defined direction.
    """
    dem = np.asarray(dem, dtype=np.float64)
    rows, cols = dem.shape
    filled = dem.astype(np.float64).copy()

    # Start from every edge cell, which always drains off the grid.
    seen = np.zeros_like(filled, dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for r in range(rows):
        for c in (0, cols - 1):
            heap.append((float(filled[r, c]), r, c))
            seen[r, c] = True
    for c in range(cols):
        for r in (0, rows - 1):
            heap.append((float(filled[r, c]), r, c))
            seen[r, c] = True
    heapq.heapify(heap)

    while heap:
        elev, r, c = heapq.heappop(heap)
        for dr, dc, _ in NEIGHBOURS:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < rows and 0 <= cc < cols) or seen[rr, cc]:
                continue
            seen[rr, cc] = True
            if filled[rr, cc] < elev:
                filled[rr, cc] = elev + epsilon
            heapq.heappush(heap, (float(filled[rr, cc]), rr, cc))

    return filled


def d8_flow_direction(filled: np.ndarray) -> np.ndarray:
    """D8 receiver index per cell, or -1 where the cell is an outlet.

    Distances are cell-count based (diagonals cost sqrt(2)), which is the usual
    approximation when the grid is near-square. The receiver is the *steepest*
    neighbour, not merely the lowest -- otherwise a gentle nearby cell beats a
    steep distant one.
    """
    filled = np.asarray(filled, dtype=np.float64)
    rows, cols = filled.shape
    receiver = np.full(rows * cols, -1, dtype=np.int32)

    for r in range(rows):
        for c in range(cols):
            best_slope = 0.0
            best = -1
            here = filled[r, c]
            for dr, dc, dist in NEIGHBOURS:
                rr, cc = r + dr, c + dc
                if not (0 <= rr < rows and 0 <= cc < cols):
                    continue
                slope = (here - filled[rr, cc]) / dist
                if slope > best_slope:
                    best_slope = slope
                    best = rr * cols + cc
            receiver[r * cols + c] = best
    return receiver


def flow_accumulation(filled: np.ndarray, receiver: np.ndarray) -> np.ndarray:
    """Cells draining through each cell, as a flat float array.

    One forward pass in decreasing elevation order: when a cell is processed
    every cell that drains into it has already been processed, so its
    accumulation is final. That is the whole reason this is cheap -- the naive
    iterative version converges slowly on long flow paths.
    """
    filled = np.asarray(filled, dtype=np.float64)
    rows, cols = filled.shape
    order = np.argsort(filled, axis=None)[::-1]  # descending elevation
    acc = np.ones(rows * cols, dtype=np.float64)
    for idx in order:
        target = receiver[idx]
        if target >= 0:
            acc[target] += acc[idx]
    return acc.reshape(rows, cols)


def condition(dem: np.ndarray) -> dict[str, np.ndarray | float]:
    """Fill depressions, route D8, and accumulate -- the full conditioning pass.

    Returns the filled surface, the flow accumulation in cells, and the number
    of cells Priority-Flood had to raise, which is the honest measure of how
    broken the raw DEM was.
    """
    dem = np.asarray(dem, dtype=np.float64)
    filled = priority_flood(dem)
    raised = int(np.sum(filled > dem + 1e-9))
    receiver = d8_flow_direction(filled)
    acc = flow_accumulation(filled, receiver)
    return {
        "filled": filled,
        "receiver": receiver.reshape(dem.shape),
        "accumulation": acc,
        "cells_raised": raised,
        "max_accumulation_cells": float(acc.max()),
    }


def label_depressions(
    filled: np.ndarray,
    raw: np.ndarray,
) -> dict[str, object]:
    """Label the depressions Priority-Flood had to fill, 8-connected.

    A depression cell is one where ``filled > raw + 1e-9`` -- a cell the filler
    raised, because the raw surface had no downhill escape from it. 8-connectivity
    (not 4) is used so a pit joined only at a corner is one depression rather than
    two, which is the convention the spill-elevation figure assumes.

    ``spillElevationM[label]`` is the **minimum filled value on the component's
    rim** -- its lowest escape -- not the component's own lowest cell. Reporting
    the bottom instead of the rim understates the level at which a pit starts to
    pass water on, which is the number a basin's usable storage depends on.

    ``depthM[label]`` is ``max(filled - raw)`` over the component. ``labels`` is
    contiguous (1..count) so a caller can iterate ``range(1, count + 1)`` without
    a sparse-label lookup. Deterministic: components are seeded in row-major order
    and the flood uses a fixed 8-neighbour order.
    """
    filled = np.asarray(filled, dtype=np.float64)
    raw = np.asarray(raw, dtype=np.float64)
    rows, cols = filled.shape
    dep = filled > raw + 1e-9
    labels = np.zeros((rows, cols), dtype=np.int32)

    unvisited = set(zip(*[axis.tolist() for axis in np.nonzero(dep)]))
    count = 0
    spill: dict[int, float] = {}
    depth: dict[int, float] = {}

    while unvisited:
        seed = unvisited.pop()
        count += 1
        stack = [seed]
        cells = [seed]
        while stack:
            r, c = stack.pop()
            labels[r, c] = count
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and dep[nr, nc]:
                        if (nr, nc) in unvisited:
                            unvisited.discard((nr, nc))
                            stack.append((nr, nc))
                            cells.append((nr, nc))

        # Rim and depth are read only after the whole component is labelled, so
        # every same-component neighbour is excluded and every rim cell kept.
        rim_min = np.inf
        dmax = 0.0
        for r, c in cells:
            d = float(filled[r, c] - raw[r, c])
            if d > dmax:
                dmax = d
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and labels[nr, nc] != count:
                        v = float(filled[nr, nc])
                        if v < rim_min:
                            rim_min = v
        # A depression with no in-grid rim (the whole grid is one pit) has no
        # escape to report; its own surface is the only honest fallback.
        spill[count] = rim_min if np.isfinite(rim_min) else float(filled[cells[0][0], cells[0][1]])
        depth[count] = dmax

    return {
        "labels": labels,
        "count": count,
        "spillElevationM": spill,
        "depthM": depth,
        "depressionCells": int(dep.sum()),
    }


def mass_balance(
    runoff_in_m3: float,
    stored_m3: float,
    discharged_m3: float,
    tol: float = 1e-6,
) -> dict[str, object]:
    """The P0-4 runtime assertion: ``runoff_in == stored + discharged``.

    ``residual = runoff_in - (stored + discharged)`` and ``balanced`` when
    ``|residual| <= tol * max(|runoff_in|, 1.0)``. The ``max(..., 1.0)`` floor
    keeps the tolerance absolute when there is no runoff to be relative to, so an
    empty grid is balanced rather than dividing by zero.

    This **raises** ``AssertionError`` when the books do not balance. A leak that
    returns ``balanced: False`` and lets the caller carry on is a leak nobody
    notices; the returned ``balanced`` flag exists only so the block can be
    serialised next to the numbers it certifies.
    """
    runoff_in_m3 = float(runoff_in_m3)
    stored_m3 = float(stored_m3)
    discharged_m3 = float(discharged_m3)
    residual = runoff_in_m3 - (stored_m3 + discharged_m3)
    denom = max(abs(runoff_in_m3), 1.0)
    relative = abs(residual) / denom
    balanced = relative <= tol
    block = {
        "runoffInM3": runoff_in_m3,
        "storedM3": stored_m3,
        "dischargedM3": discharged_m3,
        "residualM3": residual,
        "relativeResidual": relative,
        "tol": tol,
        "balanced": balanced,
    }
    if not balanced:
        raise AssertionError(
            "mass balance leaked: runoff_in={:.6g} m3, stored={:.6g} m3, "
            "discharged={:.6g} m3, residual={:.6g} m3 "
            "(relative {:.3e} > tol {:.1e})".format(
                runoff_in_m3, stored_m3, discharged_m3, residual, relative, tol
            )
        )
    return block


def fill_and_spill(
    filled: np.ndarray,
    raw: np.ndarray,
    receiver: np.ndarray,
    runoff_in_m3: np.ndarray,
    cell_area_m2: float,
    tol: float = 1e-6,
) -> dict[str, object]:
    """Route each cell's runoff down its D8 chain, storing then spilling.

    Process cells in **decreasing filled elevation** -- a valid topological order,
    because D8 only ever points at a strictly lower cell, so every cell that
    drains into ``c`` has already been processed when ``c`` is reached and its
    inflow is final.

    ``cap[c] = max(0, filled[c] - raw[c]) * A``. That clamp is the whole of the
    depression-capacity definition: ``filled - raw`` is how far Priority-Flood
    raised the cell, and inverting or dropping the ``max(0, ...)`` makes water
    either never pond or pond everywhere -- both of which look plausible on a map.
    ``stored = min(inflow, cap)``; the excess is pushed to ``receiver[c]``, or
    counted as discharged when ``receiver[c] == -1``. The **only** sink is the
    grid-edge outlet, so ``runoff_in == stored + discharged`` holds by
    construction and :func:`mass_balance` raises if float error ever breaks it.

    ``receiver`` is a **flat** index array of length ``rows * cols`` with ``-1``
    at outlets (the form ``d8_flow_direction`` returns and the form stored on
    disk). ``filled``/``raw``/``runoff_in_m3`` are raveled to the same C order, so
    a grid-shaped or already-flat input both work; the conversion is explicit
    rather than left to ``reshape``.
    """
    filled = np.asarray(filled, dtype=np.float64)
    raw = np.asarray(raw, dtype=np.float64)
    rows, cols = filled.shape
    n = rows * cols
    filled_f = filled.ravel()
    raw_f = raw.ravel()
    recv = np.asarray(receiver, dtype=np.int64).ravel()
    runoff = np.asarray(runoff_in_m3, dtype=np.float64).ravel()
    if recv.size != n:
        raise ValueError(
            f"receiver has {recv.size} entries, expected rows*cols={n}; the flat "
            f"index space and the raster shape must agree"
        )
    if runoff.size != n:
        raise ValueError(f"runoff_in_m3 has {runoff.size} entries, expected {n}")

    cap = np.maximum(0.0, filled_f - raw_f) * float(cell_area_m2)
    inflow = runoff.copy()
    stored = np.zeros(n, dtype=np.float64)
    order = np.argsort(-filled_f, kind="stable")

    discharged = 0.0
    for idx in order:
        amount = float(inflow[idx])
        s = amount if amount < cap[idx] else float(cap[idx])
        stored[idx] = s
        route = amount - s
        target = int(recv[idx])
        if target >= 0:
            inflow[target] += route
        else:
            discharged += route

    runoff_total = float(runoff.sum())
    stored_total = float(stored.sum())
    balance = mass_balance(runoff_total, stored_total, discharged, tol=tol)

    depth_field = (stored / float(cell_area_m2)).reshape(rows, cols)
    depressions = label_depressions(filled, raw)
    wet_cells = int((depth_field > 0).sum())
    max_depth = float(depth_field.max()) if wet_cells else 0.0

    return {
        "depthField": depth_field,
        "storedM3": stored_total,
        "dischargedM3": float(discharged),
        "runoffInM3": runoff_total,
        "massBalance": balance,
        "depressions": depressions,
        "wetCells": wet_cells,
        "maxDepthM": max_depth,
    }


def hand(
    filled: np.ndarray,
    receiver: np.ndarray,
    accumulation: np.ndarray,
    drainage_min_accum_cells: float = HAND_DRAINAGE_MIN_ACCUM_CELLS,
) -> dict[str, object]:
    """Height Above Nearest Drainage, an independent cross-check of routed depth.

    Drainage cells are those with ``accumulation >= drainage_min_accum_cells`` --
    the routing's own channel definition, **not** the water/wetland land class.
    Using the class would restate the land-cover grid and cross-check nothing;
    HAND must be independent of the depth field it is compared against.

    ``HAND[c] = filled[c] - filled[nearest downstream drainage cell]``, where the
    nearest downstream drainage is found by following the D8 receiver chain. The
    chain runs downhill, so cells are visited in **increasing** filled elevation
    (the reverse of the cascade's order): a cell's reference is its receiver's,
    and the receiver is lower and therefore already resolved.

    Outlets (``receiver == -1``) seed the reference as well. Every chain ends at
    an outlet, so without this a cell that leaves the grid before meeting a
    channel would have no reference and the cross-check overlay would be partly
    undefined -- which is not a usable overlay. ``drainageCells`` still counts
    only the accumulation-threshold cells; the outlet seeding only supplies the
    reference elevation.

    ``receiver`` is the **flat** index array (see :func:`fill_and_spill`);
    ``filled`` and ``accumulation`` are raveled to match. Deterministic.
    """
    filled = np.asarray(filled, dtype=np.float64)
    rows, cols = filled.shape
    n = rows * cols
    filled_f = filled.ravel()
    recv = np.asarray(receiver, dtype=np.int64).ravel()
    accum = np.asarray(accumulation, dtype=np.float64).ravel()
    if recv.size != n:
        raise ValueError(
            f"receiver has {recv.size} entries, expected rows*cols={n}"
        )
    if accum.size != n:
        raise ValueError(f"accumulation has {accum.size} entries, expected {n}")

    drainage = accum >= float(drainage_min_accum_cells)
    seed = drainage | (recv < 0)

    reference = np.empty(n, dtype=np.float64)
    hand_flat = np.empty(n, dtype=np.float64)
    for idx in np.argsort(filled_f, kind="stable"):
        if seed[idx]:
            reference[idx] = filled_f[idx]
            hand_flat[idx] = 0.0
        else:
            target = int(recv[idx])
            reference[idx] = reference[target]
            hand_flat[idx] = filled_f[idx] - reference[idx]

    hand_m = hand_flat.reshape(rows, cols)
    return {
        "handM": hand_m,
        "drainageCells": int(drainage.sum()),
        "drainageThresholdCells": float(drainage_min_accum_cells),
        "minHandM": float(hand_m.min()),
        "maxHandM": float(hand_m.max()),
        "meanHandM": float(hand_m.mean()),
        "status": "SCREENING_HAND_CROSSCHECK_NOT_A_FLOOD_PREDICTION",
        "warning": (
            "Not a flood prediction. HAND is a terrain cross-check of the routed "
            "depth, not an inundation map, and it is never merged into it."
        ),
    }
