"""The three grounding pools must not offer a site on top of a building.

``load_grounding()`` builds candidate intervention sites from the wet grid, the
land-cover classes and the paved mask. Before this fix those masks were the raw
class masks: the surveyed building footprints were never subtracted, so a
reservoir or paving *site* could be offered on a roof. That is the defect gao
found, and it has to be fixed in the **pool**, not only guarded at the door: a
validator that rejects the very sites the grounding offers would leave the
proposal list always empty -- a worse failure than the one being removed.

What is asserted, and why at this level:

1. **Every site the model is offered contains zero cells under a building.** The
   check rasterises each site's own polygon with the project's one ring-to-cell
   test (``intervention_geometry._cells_of_ring``, the same cell-centre test the
   ledger uses), holes removed, and intersects it with
   ``scripts/_overlay_common.building_footprint_mask`` -- the one building
   authority, not a second read of the footprints GeoJSON.
2. **The pools are still non-empty.** A "fix" that emptied them would be a
   silent regression in the other direction: a planner offered nothing at all.

The ring is put back into cell space by inverting the projection
``intervention_geometry.contour_rings`` used to build it, rather than
``Overlay.cell_of``: a contour vertex sits exactly on a cell boundary, and
``cell_of``'s ``int()`` truncation maps a vertex at 11.999999 onto cell 11,
shifting the ring by one cell and reporting a neighbour as covered. Keeping the
exact float coordinate lets the project's own centre test decide, which is what
makes the enclosure check meaningful rather than decorative.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.ai_plan import load_grounding  # noqa: E402
from app.services.intervention_geometry import _cells_of_ring  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {label}{f' -- {detail}' if detail else ''}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} -- {detail}")


def _cells_under(ov, geometry: dict) -> set[tuple[int, int]]:
    """The grid cells a site's polygon covers, holes removed.

    The inverse of ``contour_rings.to_lonlat``: ``lon = west + col*cw``,
    ``lat = north - row*ch``, so ``col = (lon-west)/cw`` and
    ``row = (north-lat)/ch``, kept as floats (see the module docstring).
    """
    west, south, east, north = ov.bounds
    cw = (east - west) / ov.cols
    ch = (north - south) / ov.rows

    def to_cell_space(ring: list[list[float]]) -> list[tuple[float, float]]:
        return [((north - lat) / ch, (lon - west) / cw) for lon, lat in ring]

    rings = geometry.get("coordinates") or []
    if not rings:
        return set()
    cells = _cells_of_ring(to_cell_space(rings[0]), ov.rows, ov.cols)
    for hole in rings[1:]:
        cells -= _cells_of_ring(to_cell_space(hole), ov.rows, ov.cols)
    return cells


def main() -> int:
    from _overlay_common import building_footprint_mask

    grounding = load_grounding()
    check("the grounding loads on this study area",
          grounding.overlay is not None, "; ".join(grounding.notes))
    if grounding.overlay is None:
        print("\n0/1 checks passed")
        return 1
    ov = grounding.overlay

    occupied = building_footprint_mask(ov)
    check("a city model is present, so the subtraction is actually exercised",
          occupied is not None,
          "no buildings_hsk.json: the pools cannot be checked for roof sites")
    if occupied is None:
        print(f"\n{PASSED}/{PASSED + FAILED} checks passed")
        return 1 if FAILED else 0

    # The exclusion must be stated on the response, not implied by an absence.
    check("the grounding says the buildings were excluded",
          any("excluded from candidate sites" in note for note in grounding.notes),
          "; ".join(grounding.notes))

    for name, sites in (
        ("reservoir", grounding.reservoir_sites),
        ("green", grounding.green_sites),
        ("paving", grounding.paving_sites),
    ):
        check(f"the {name} pool is still non-empty", len(sites) > 0,
              f"{len(sites)} sites offered")
        for i, site in enumerate(sites):
            covered = _cells_under(ov, site["geometry"])
            under_building = sum(1 for r, c in covered if occupied[r, c])
            check(f"{name}[{i}] is offered on ground, not on a roof",
                  covered and under_building == 0,
                  f"{under_building} of {len(covered)} cells are under a building")

    print(f"\n{PASSED}/{PASSED + FAILED} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
