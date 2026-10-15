"""True-mask contours for intervention footprints (P1.1 of the loop plan).

The candidate sites first shipped as the **bounding box** of a connected
component: five points, always axis-aligned, and always claiming the empty
corners of an irregular basin as part of the intervention. A polygon that
claims a field nobody proposed is not a conservative approximation -- it
trips the single-intervention coverage guard on the wrong evidence (the
seeded reservoir basins are 45% of the study area *as boxes* and far less as
masks) and it draws a rectangle where the analysis found a shape.

This module contours the component's **true cell mask** instead:

- the samples are the cells themselves, one value per 5 m cell, so the 0.5
  level set passes through the midpoints of the edges between an inside cell
  and an outside cell;
- the trace is classic marching squares with the two saddle cases resolved
  by *isolating* the corners, which is the resolution that keeps centre
  membership exact: rasterising the produced ring with the project's own
  centre test (`sandbox_model._polygon_cells`) returns the component's own
  cells, and no others.

There are no new dependencies. The whole contour is a few dozen lines of
index arithmetic over a boolean mask, which is the point: a dependency here
would be a dependency on someone else's saddle resolution.

Everything is deterministic. The same cells produce the same ring, to the
last 1e-7 degree, forever.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

# --------------------------------------------------------------------------- #
# Marching squares, on cell-centre samples
# --------------------------------------------------------------------------- #
#
# A block of four cells with values TL, TR, BR, BL. The crossings live on the
# edges *between cell centres*, so in cell-index space a crossing is always at
# a half-integer coordinate -- exactly representable, which is what lets the
# segments be chained by exact key rather than by tolerance.
#
#   (r, c) ---- (r, c+1)          top    crossing: (r + 0.5, c + 1)
#     |  TL | TR  |                right  crossing: (r + 1,   c + 1.5)
#     | ----+---- |                bottom crossing: (r + 1.5, c + 1)
#     |  BL | BR  |                left   crossing: (r + 1,   c + 0.5)
#   (r+1,c) --- (r+1,c+1)
#
# Edge names, and the two segments each case emits, as pairs of edge indices
# (0=top, 1=right, 2=bottom, 3=left).

_TOP, _RIGHT, _BOTTOM, _LEFT = 0, 1, 2, 3

# The 16 cases. Bit order is TL=1, TR=2, BR=4, BL=8. The saddle cases (5 and
# 10) are resolved by isolating: two separate corner arcs rather than one
# connected pair. That is the resolution under which a cell centre is inside
# the contour if and only if its cell is in the mask -- which is the property
# the whole module exists to guarantee.
_MS_SEGMENTS: dict[int, tuple[tuple[int, int], ...]] = {
    0b0000: (),
    0b0001: ((_LEFT, _TOP),),                                   # TL
    0b0010: ((_TOP, _RIGHT),),                                  # TR
    0b0011: ((_LEFT, _RIGHT),),                                 # TL+TR
    0b0100: ((_RIGHT, _BOTTOM),),                               # BR
    0b0101: ((_LEFT, _TOP), (_RIGHT, _BOTTOM)),                 # TL+BR saddle
    0b0110: ((_TOP, _BOTTOM),),                                 # TR+BR
    0b0111: ((_LEFT, _BOTTOM),),                                # TL+TR+BR
    0b1000: ((_BOTTOM, _LEFT),),                                # BL
    0b1001: ((_TOP, _BOTTOM),),                                 # TL+BL
    0b1010: ((_TOP, _RIGHT), (_BOTTOM, _LEFT)),                 # TR+BL saddle
    0b1011: ((_BOTTOM, _RIGHT),),                               # TL+TR+BL
    0b1100: ((_LEFT, _RIGHT),),                                 # BR+BL
    0b1101: ((_TOP, _RIGHT),),                                  # TL+BR+BL
    0b1110: ((_TOP, _LEFT),),                                   # TR+BR+BL
    0b1111: (),
}


def _crossing_points(r: int, c: int) -> dict[int, tuple[float, float]]:
    """The four edge crossings of the 2x2 block whose top-left cell is (r, c)."""
    return {
        _TOP: (r + 0.5, c + 1.0),
        _RIGHT: (r + 1.0, c + 1.5),
        _BOTTOM: (r + 1.5, c + 1.0),
        _LEFT: (r + 1.0, c + 0.5),
    }


def _cell_mask(cells: Iterable[tuple[int, int]], rows: int,
               cols: int) -> np.ndarray:
    mask = np.zeros((rows, cols), dtype=bool)
    for r, c in cells:
        mask[r, c] = True
    return mask


def _ring_in_cell_space(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """Closed rings (cell-index space) around every 4-connected region.

    Returns a list of rings, largest first by the number of mask cells they
    enclose. Each ring is a list of ``(row, col)`` points, first == last.

    Only the blocks touching the mask are visited: a full-grid scan is 315k
    Python iterations for a handful of cells, and the project's budget for
    anything interactive is measured in hundreds of milliseconds.

    The mask is padded by one cell before the scan, and the offset is removed
    on the way out. A 2x2 block reads ``mask[r+1, c+1]``, so without the pad
    every block that touches the grid's last row or column is silently
    skipped -- and the largest basin in the study area does touch both. The
    symptom is not a crash but a contour that stops short and then fragments:
    the reservoir mask traced 165 vertices enclosing 409 of its 5,163 cells,
    which reads as a plausible polygon and is a lie about where the basin is.
    """
    padded = np.pad(mask, 1)
    rows, cols = mask.shape
    any_rows = np.nonzero(padded.any(axis=1))[0]
    any_cols = np.nonzero(padded.any(axis=0))[0]
    if any_rows.size == 0:
        return []
    r_lo, r_hi = max(0, int(any_rows[0]) - 1), min(rows, int(any_rows[-1]) + 1)
    c_lo, c_hi = max(0, int(any_cols[0]) - 1), min(cols, int(any_cols[-1]) + 1)
    # Collect directed segments, then walk them. A segment is keyed by its two
    # endpoints, each quantised to exact half-integers: multiply by 2 and the
    # float becomes an int, so matching is equality, not tolerance.
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def key(p: tuple[float, float]) -> tuple[int, int]:
        return (int(round(p[0] * 2)), int(round(p[1] * 2)))

    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            case = (
                (1 if padded[r, c] else 0)
                | (2 if padded[r, c + 1] else 0)
                | (4 if padded[r + 1, c + 1] else 0)
                | (8 if padded[r + 1, c] else 0)
            )
            segments = _MS_SEGMENTS.get(case, ())
            if not segments:
                continue
            points = _crossing_points(r, c)
            for a, b in segments:
                ka, kb = key(points[a]), key(points[b])
                adjacency.setdefault(ka, []).append(kb)
                adjacency.setdefault(kb, []).append(ka)

    rings: list[list[tuple[int, int]]] = []
    visited_edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    def walk(start: tuple[int, int], first: tuple[int, int]) -> None:
        ring = [start]
        prev, current = start, first
        while True:
            edge = (prev, current)
            rev = (current, prev)
            if edge in visited_edges or rev in visited_edges:
                # An already-walked edge means the ring has closed on itself.
                break
            visited_edges.add(edge)
            ring.append(current)
            nxt = [n for n in adjacency.get(current, []) if n != prev]
            if not nxt:
                break
            # Deterministic choice: smallest key first. At a pinch vertex the
            # walk closes a loop rather than crossing it, which keeps every
            # ring simple to rasterise.
            nxt.sort()
            prev, current = current, nxt[0]
            if current == start:
                break
        if len(ring) >= 4:
            rings.append(ring)

    for node in sorted(adjacency):
        for neighbour in sorted(adjacency[node]):
            if (node, neighbour) in visited_edges:
                continue
            if (neighbour, node) in visited_edges:
                continue
            walk(node, neighbour)

    # De-quantise back to float half-integers, remove the pad offset, and drop
    # the duplicated closure.
    out: list[list[tuple[float, float]]] = []
    for ring in rings:
        pts = [(a / 2.0 - 1.0, b / 2.0 - 1.0) for a, b in ring]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            out.append(pts)
    return out


def connected_components(mask: np.ndarray, min_size: int = 1,
                         connectivity: int = 8) -> list[dict[str, Any]]:
    """Label a boolean mask into components, largest first.

    Returns one dict per component with its bounding box (``min_r``/``min_c``/
    ``max_r``/``max_c``), its ``size`` in cells, and its ``cells`` as a list of
    ``(row, col)``. Keeping the cell list is what makes a *contour* possible:
    a bounding box has no shape to trace, and re-labelling to recover the cells
    would be a second chance to disagree about which cells are in the basin.

    ``connectivity`` is 4 or 8. The intervention pools use **4**: an
    8-connected basin whose parts meet only diagonally traces as several small
    contours (the saddle case isolates the corners), and the observed wet-grid
    basin is 5,163 cells as 8-connected but 409 as its largest 4-connected
    part -- a "reservoir site" covering a twelfth of its own basin. Four
    connectivity is also the conservative reading of "contiguous": diagonal
    contact is not a continuous water surface.

    Callers that only need boxes can ignore ``cells``; the fill is one pass
    either way, so this is the single definition of "component" for grounding,
    siting and the catalog generator alike.
    """
    # ``steps`` must be a *materialised* sequence, not a generator. An earlier
    # version built the 8-connectivity case as a generator expression, which the
    # flood fill below consumes once -- on the first cell it pops -- and then
    # finds exhausted, so every component stopped after a handful of cells: a
    # full 8x8 block of True cells labelled as [4, 1, 1, ...] instead of [64].
    # The 4-connectivity branch used a tuple and was correct, which is why only
    # the 8-connected callers broke -- most visibly ``ai_plan._components``,
    # where ``placement_footprints`` filters components by a 40 m block's cell
    # count, matched nothing, and the ``place`` op reported "placed 0" on the
    # real land cover. Tuple, so it is re-iterable for every cell in the fill.
    steps = ((-1, 0), (1, 0), (0, -1), (0, 1)) if connectivity == 4 else tuple(
        (dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0))
    seen = np.zeros_like(mask, dtype=bool)
    out: list[dict[str, Any]] = []
    rows, cols = mask.shape
    for r0 in range(rows):
        for c0 in range(cols):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            stack = [(r0, c0)]
            seen[r0, c0] = True
            cells: list[tuple[int, int]] = []
            while stack:
                r, c = stack.pop()
                cells.append((r, c))
                for dr, dc in steps:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
            if len(cells) >= min_size:
                arr = np.array(cells)
                out.append({
                    "min_r": int(arr[:, 0].min()), "min_c": int(arr[:, 1].min()),
                    "max_r": int(arr[:, 0].max()), "max_c": int(arr[:, 1].max()),
                    "size": len(cells), "cells": cells,
                })
    out.sort(key=lambda c: -c["size"])
    return out


def _signed_area(points: list[tuple[float, float]]) -> float:
    """Twice the signed area of a closed ring (shoelace)."""
    total = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _drop_collinear(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove vertices whose two edges continue in a straight line.

    A rectilinear boundary is mostly straight runs, and a ring with 400
    collinear vertices is 400 chances for a downstream consumer to disagree
    with itself about what the shape is. Only genuinely collinear midpoints
    go; a repeated pinch vertex is not collinear with anything and survives.
    """
    out: list[tuple[float, float]] = []
    n = len(points)
    for i in range(n):
        ax, ay = points[(i - 1) % n]
        bx, by = points[i]
        cx, cy = points[(i + 1) % n]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > 1e-9:
            out.append((bx, by))
    return out or list(points)


def _point_in_ring(lon: float, lat: float,
                   ring_rowcol: list[tuple[float, float]]) -> bool:
    """Even-odd ray casting in cell-index space.

    ``ring_rowcol`` points are ``(row, col)`` -- which is ``(y, x)``, the
    transpose of the ``(x, y)`` the arithmetic below is written for. Unpacking
    them as ``(x, y)`` without the swap is a silent mirror of every polygon,
    and it turned a diamond around its own centre.
    """
    hit = False
    n = len(ring_rowcol)
    j = n - 1
    for i in range(n):
        yi, xi = ring_rowcol[i]
        yj, xj = ring_rowcol[j]
        if (yi > lat) != (yj > lat):
            cross = xi + (lat - yi) * (xj - xi) / (yj - yi)
            if lon < cross:
                hit = not hit
        j = i
    return hit


def _cells_of_ring(ring_xy: list[tuple[float, float]], rows: int,
                   cols: int) -> set[tuple[int, int]]:
    """Cell centres inside a ring in cell-index space (the same centre test
    the ledger rasteriser uses, which is what makes the enclosure check
    meaningful rather than decorative).

    Scan the ring's own bounding box only: the mask is local by construction
    and a full-grid cast is the slowest possible way to agree with it.
    """
    out: set[tuple[int, int]] = set()
    pr = [p[0] for p in ring_xy]
    pc = [p[1] for p in ring_xy]
    r_lo = max(0, int(math.floor(min(pr))) - 1)
    r_hi = min(rows - 1, int(math.ceil(max(pr))) + 1)
    c_lo = max(0, int(math.floor(min(pc))) - 1)
    c_hi = min(cols - 1, int(math.ceil(max(pc))) + 1)
    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            if _point_in_ring(c + 0.5, r + 0.5, ring_xy):
                out.add((r, c))
    return out


def contour_rings(cells: Iterable[tuple[int, int]], *, bounds: tuple[float, float, float, float],
                  rows: int, cols: int) -> list[dict[str, Any]]:
    """Contour a set of grid cells into lon/lat polygons, holes included.

    ``bounds`` is ``(west, south, east, north)``; row 0 is the north edge,
    column 0 is the west edge -- the same convention as ``Overlay`` and
    ``Grids``. Returns one dict per **outer** ring, largest first:

    ``ring``     the outer ring as a closed GeoJSON coordinate list
    ``holes``    rings nested inside it (an island inside a basin is not part
                 of the basin, so it is not converted by the intervention)
    ``geometry`` a ready GeoJSON ``Polygon`` value: ``[outer, *holes]``
    ``enclosed`` the component's own cells whose centre falls inside the
                 polygon -- outer ring minus holes, intersected with the mask
    ``covered``  every cell whose centre falls inside the polygon, whether or
                 not it is one of the component's own
    ``ofComponent`` how many cells the component has in total

    The two cell counts are both reported because they answer different
    questions, and the gap between them is where a contour can mislead:
    ``enclosed`` says how much of *this site* the polygon is, ``covered`` says
    how much land a consumer will actually convert. They are equal for a
    compact basin and unequal for a crenellated one -- a paving band whose
    contour closes over a one-cell gap covers the gap too, and the ledger
    will paint it. ``covered`` is the number to quote; ``enclosed`` is the
    number to check the quote against.

    The enclosure count is measured, not asserted: it is what lets a caller
    (and a test) see the difference between a contour and a claim. When a
    component is only diagonally connected, its 4-connected parts come back
    as separate entries and the largest may enclose fewer than all of them;
    the two numbers say so instead of a coverage surprise saying it later.
    """
    cells = [(int(r), int(c)) for r, c in cells]
    if not cells:
        return []
    mask = _cell_mask(cells, rows, cols)
    wanted = {(r, c) for r, c in cells}

    west, south, east, north = bounds
    cw = (east - west) / cols
    ch = (north - south) / rows

    def to_lonlat(points: list[tuple[float, float]]) -> list[list[float]]:
        ring = [[round(west + pc * cw, 7), round(north - pr * ch, 7)]
                for pr, pc in points]
        # CCW outer, CW holes, per RFC 7946's recommendation. The sign
        # convention follows from (row, col) being (y, x) here.
        if _signed_area(list(points)) < 0:
            ring = list(reversed(ring))
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring

    raw = _ring_in_cell_space(mask)
    if not raw:
        return []

    simplified = [_drop_collinear(ring) for ring in raw]

    # A ring is an outer boundary if at least one mask cell centre falls
    # inside it. Everything else is a hole -- either a genuine island or the
    # inner boundary of a diagonal-only connection.
    outers: list[tuple[list[tuple[float, float]], set[tuple[int, int]]]] = []
    hole_candidates: list[list[tuple[float, float]]] = []
    for ring in simplified:
        enclosed = _cells_of_ring(ring, rows, cols)
        if enclosed & wanted:
            outers.append((ring, enclosed))
        else:
            hole_candidates.append(ring)

    # Attach each hole to the smallest outer ring that contains it. Nesting
    # is tested with the same even-odd cast, on the outer ring alone: a ring
    # inside a hole of a larger ring is a component of its own, and attaching
    # it to the wrong parent is exactly the silence this avoids.
    out: list[dict[str, Any]] = []
    for ring, enclosed in outers:
        holes: list[list[tuple[float, float]]] = []
        for candidate in hole_candidates:
            if any(_point_in_ring(pc + 0.5, pr + 0.5, ring)
                   for pr, pc in candidate):
                holes.append(candidate)
        kept = enclosed
        covered = _cells_of_ring(ring, rows, cols)
        for hole in holes:
            kept -= _cells_of_ring(hole, rows, cols)
            covered -= _cells_of_ring(hole, rows, cols) & covered
        hole_rings = [to_lonlat(h) for h in holes]
        # Holes must wind the other way from their outer ring.
        hole_rings = [list(reversed(h)) for h in hole_rings]
        outer_ring = to_lonlat(ring)
        out.append({
            "ring": outer_ring,
            "holes": hole_rings,
            "geometry": {"type": "Polygon",
                         "coordinates": [outer_ring, *hole_rings]},
            "enclosed": len(kept & wanted),
            "covered": len(covered),
            "ofComponent": len(wanted),
        })
    out.sort(key=lambda d: -d["enclosed"])
    return out


def ring_area_ha(ring: list[list[float]], lat_min: float,
                 lat_max: float) -> float:
    """Shoelace area of a lon/lat ring, in hectares.

    The projection is the one the grounding has always used -- 111,320 m per
    degree of longitude scaled by cos(latitude), 110,574 m per degree of
    latitude -- so a contour's area is directly comparable with the box
    areas it replaced. A shoelace on the ring replaces "width times height",
    which is what made the old figures claim the empty corners.
    """
    if len(ring) < 3:
        return 0.0
    mid_lat = math.radians((lat_min + lat_max) / 2)
    mx = 111_320.0 * math.cos(mid_lat)
    my = 110_574.0
    pts = [((p[0]) * mx, (p[1]) * my) for p in ring[:-1]] if ring[0] == ring[-1] \
        else [(p[0] * mx, p[1] * my) for p in ring]
    twice = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        twice += x0 * y1 - x1 * y0
    return abs(twice) / 2.0 / 10_000.0


def polygon_area_ha(coordinates: list[list[list[float]]], lat_min: float,
                    lat_max: float) -> float:
    """Shoelace area of a GeoJSON ``Polygon`` coordinate list, holes included.

    Outer ring minus every hole: a paved block with 178 single-cell gaps in it
    is 178 holes smaller than its outline, and quoting the outline would claim
    the gaps as permeable paving too. Same projection as ``ring_area_ha``, so
    the two are directly comparable.
    """
    if not coordinates:
        return 0.0
    total = ring_area_ha(coordinates[0], lat_min, lat_max)
    for hole in coordinates[1:]:
        total -= ring_area_ha(hole, lat_min, lat_max)
    return round(total, 1)


def ring_centroid(ring: list[list[float]]) -> list[float]:
    """The area centroid of a lon/lat ring, as ``[lon, lat]``.

    The polygon centroid, not the bounding-box centre: for an L-shaped basin
    the box centre can fall outside the basin entirely, and a "centroid" the
    intervention does not contain is worse than no centroid at all. Falls
    back to the mean of the vertices for a degenerate ring whose area is
    zero, so a caller never receives a point invented from nothing.
    """
    pts = ring[:-1] if ring and ring[0] == ring[-1] else list(ring)
    if len(pts) < 3:
        return [round(sum(p[0] for p in pts) / len(pts), 6),
                round(sum(p[1] for p in pts) / len(pts), 6)] if pts else [0.0, 0.0]
    twice = 0.0
    cx = 0.0
    cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        twice += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(twice) < 1e-12:
        return [round(sum(p[0] for p in pts) / n, 6),
                round(sum(p[1] for p in pts) / n, 6)]
    return [round(cx / (3 * twice), 6), round(cy / (3 * twice), 6)]


def contour_ring(cells: Iterable[tuple[int, int]], *,
                 bounds: tuple[float, float, float, float], rows: int,
                 cols: int) -> tuple[dict[str, Any], int, int] | None:
    """The largest true-mask polygon for ``cells``, or None when there is none.

    Returns ``(geometry, enclosed, of_component)`` where ``geometry`` is a
    ready GeoJSON ``Polygon`` value. The two counts let a caller see the
    difference between a contour and a claim: ``enclosed == of_component``
    means the polygon is the component, and less means the component is only
    diagonally connected and the polygon is its largest part.
    """
    rings = contour_rings(cells, bounds=bounds, rows=rows, cols=cols)
    if not rings:
        return None
    best = rings[0]
    return best["geometry"], best["enclosed"], best["ofComponent"]
