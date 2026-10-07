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
"""

from __future__ import annotations

import heapq

import numpy as np

__all__ = ["priority_flood", "d8_flow_direction", "flow_accumulation", "condition"]

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
