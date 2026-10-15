"""Tests for the true-mask contour geometry (P1.1).

The load-bearing assertion is the round-trip: a contour ring fed to
``sandbox_model._polygon_cells`` -- the rasteriser the ledger itself uses --
returns exactly the component's own cells. Everything else (closed rings,
holes, diagonal behaviour, determinism) is in service of that identity.

Written as a plain script, like the rest of the suites: run
``cd backend && ./.venv/bin/python tests/test_intervention_geometry.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.services.intervention_geometry import contour_ring, contour_rings  # noqa: E402
from app.services.sandbox_model import _polygon_cells  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL  {label} - {detail}")


# A small window of the real grid's extent, so the lon/lat conversion is
# exercised at real scale (cell ~5 m) without the real 474x664 mask.
BOUNDS = (113.982, 22.425, 114.005, 22.455)
ROWS, COLS = 40, 50


def rasterise(geometry: dict) -> set[tuple[int, int]]:
    """What the ledger's own rasteriser makes of a contour polygon."""
    x0, y0, x1, y1 = BOUNDS

    def cell_of(lon: float, lat: float) -> tuple[int, int]:
        col = int((lon - x0) / (x1 - x0) * COLS)
        row = int((y1 - lat) / (y1 - y0) * ROWS)
        return (max(0, min(ROWS - 1, row)), max(0, min(COLS - 1, col)))

    grids = SimpleNamespace(bounds=BOUNDS, rows=ROWS, cols=COLS, cell_of=cell_of)
    # The full coordinate list, exactly as `apply_interventions` reads it.
    return set(_polygon_cells(grids, geometry["coordinates"]))


def rectangle(r0: int, c0: int, height: int, width: int) -> list[tuple[int, int]]:
    return [(r, c) for r in range(r0, r0 + height) for c in range(c0, c0 + width)]


# --------------------------------------------------------------------------- #
# One cell
# --------------------------------------------------------------------------- #

geometry, enclosed, of_component = contour_ring(
    [(12, 20)], bounds=BOUNDS, rows=ROWS, cols=COLS)
ring = geometry["coordinates"][0]
check("a single cell contours to a closed ring",
      geometry["type"] == "Polygon" and len(ring) >= 4 and ring[0] == ring[-1],
      f"{geometry['type']} with {len(ring)} points")
check("a single cell's diamond encloses exactly its own cell",
      enclosed == 1 and of_component == 1, f"{enclosed}/{of_component}")
check("a single cell round-trips through the ledger rasteriser",
      rasterise(geometry) == {(12, 20)}, f"{rasterise(geometry)}")


# --------------------------------------------------------------------------- #
# A solid block: the contour is the mask, not a bounding box
# --------------------------------------------------------------------------- #

cells = rectangle(6, 8, 12, 8)
geometry, enclosed, of_component = contour_ring(
    cells, bounds=BOUNDS, rows=ROWS, cols=COLS)
check("a solid block encloses every one of its cells",
      enclosed == of_component == len(cells), f"{enclosed}/{of_component}")
check("a solid block round-trips through the ledger rasteriser",
      rasterise(geometry) == set(cells),
      f"extra={rasterise(geometry) - set(cells)} missing={set(cells) - rasterise(geometry)}")


# --------------------------------------------------------------------------- #
# An irregular shape: the whole point of the contour
# --------------------------------------------------------------------------- #

# An L: a 14-wide bar with a 6-wide foot. Its bounding box claims 14x10 =
# 140 cells; the mask is 84. The old five-point box would have applied the
# intervention to 56 cells nobody proposed.
l_shape = rectangle(4, 4, 3, 14) + rectangle(7, 4, 8, 6)
geometry, enclosed, of_component = contour_ring(
    l_shape, bounds=BOUNDS, rows=ROWS, cols=COLS)
ring = geometry["coordinates"][0]
bbox_ring_cells = rectangle(4, 4, 11, 14)

check("an L encloses every one of its cells",
      enclosed == of_component == len(l_shape), f"{enclosed}/{of_component}")
check("an L round-trips through the ledger rasteriser",
      rasterise(geometry) == set(l_shape),
      f"extra={len(rasterise(geometry) - set(l_shape))} "
      f"missing={len(set(l_shape) - rasterise(geometry))}")
check("an L contour is not the bounding box it used to be",
      len(ring) > 5, f"{len(ring)} points -- that is a box, not a shape")
check("an L claims far less than its bbox did",
      len(rasterise(geometry)) < len(bbox_ring_cells),
      f"{len(rasterise(geometry))} vs {len(bbox_ring_cells)} bbox cells")


# A plus sign, as a second irregular case with four arms.
plus = sorted(set(
    rectangle(10, 8, 2, 12)
    + rectangle(8, 13, 10, 2)
    + rectangle(8, 16, 2, 2)
))
geometry, enclosed, of_component = contour_ring(
    plus, bounds=BOUNDS, rows=ROWS, cols=COLS)
check("a plus sign encloses every one of its cells",
      enclosed == of_component == len(plus), f"{enclosed}/{of_component}")
check("a plus sign round-trips through the ledger rasteriser",
      rasterise(geometry) == set(plus),
      f"extra={rasterise(geometry) - set(plus)} missing={set(plus) - rasterise(geometry)}")


# --------------------------------------------------------------------------- #
# Holes: an island in a basin is not part of the basin
# --------------------------------------------------------------------------- #

donut = rectangle(20, 10, 9, 9)
hole = rectangle(23, 13, 3, 3)
donut = [cell for cell in donut if cell not in hole]

rings = contour_rings(donut, bounds=BOUNDS, rows=ROWS, cols=COLS)
donut_geometry, enclosed, of_component = contour_ring(
    donut, bounds=BOUNDS, rows=ROWS, cols=COLS)
check("a donut comes back as a polygon with a hole",
      len(donut_geometry["coordinates"]) == 2 and len(rings) == 1,
      f"{len(donut_geometry['coordinates'])} rings, {len(rings)} entries")
check("a donut encloses exactly its own cells",
      enclosed == of_component == len(donut), f"{enclosed}/{of_component}")
check("a donut's hole is NOT converted by the ledger rasteriser",
      rasterise(donut_geometry) == set(donut),
      f"island cells wrongly enclosed: {rasterise(donut_geometry) & set(hole)}")


# --------------------------------------------------------------------------- #
# A diagonal-only connection: two parts, and the count says so
# --------------------------------------------------------------------------- #

diagonal = [(30, 30), (31, 31)]
rings = contour_rings(diagonal, bounds=BOUNDS, rows=ROWS, cols=COLS)
geometry, enclosed, of_component = contour_ring(
    diagonal, bounds=BOUNDS, rows=ROWS, cols=COLS)
check("a diagonal pair yields two parts",
      len(rings) == 2, f"{len(rings)} entries")
check("a diagonal pair's largest part encloses only itself",
      enclosed == 1 and of_component == 2, f"{enclosed}/{of_component}")
check("a diagonal pair round-trips through the ledger rasteriser",
      rasterise(geometry) == {(30, 30)}, f"{rasterise(geometry)}")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #

again, _, _ = contour_ring(donut, bounds=BOUNDS, rows=ROWS, cols=COLS)
check("the same cells contour to the same polygon, exactly",
      again == donut_geometry, "geometry differs between calls")

# --------------------------------------------------------------------------- #

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED:")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("all intervention-geometry checks passed")
