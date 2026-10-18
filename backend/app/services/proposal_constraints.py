"""The input constraints a proposal must satisfy before it is shown or scored.

Design: ``docs/DESIGN_intervention_proposals.md`` -- §2.1 (the exact
predicates), §2.2 (the wire shape), §4 / Part D.2 (the frozen signatures).

## What this module is

Three pure predicates over the loaded grid, plus the size algebra and the
centre+size -> geometry step. None of it calls a model, reads a clock or writes
anything: the model supplies an *assumption*, never a result, and every number
in a proposal is re-derived here from the rasters.

## One definition of each thing (design §9)

- geometry is **contoured** through ``intervention_geometry.contour_ring`` and
  **rasterised** through ``sandbox_model._polygon_cells``. There is no second
  point-in-polygon test in this file.
- the cell map is ``Grids.cell_of`` -- no lon/lat is rounded anywhere else.
- the building authority is :func:`building_mask` -- the UNION of the
  polygon-exact footprint raster and the legacy bounding-box one. See its
  docstring for why the union and not either side alone.
- the size floor is ``siting.MIN_SITE_CELLS`` (imported, never re-typed); the
  caps are ``sandbox_model.MAX_INTERVENTION_COVERAGE`` /
  ``MAX_TOTAL_COVERAGE`` (imported, never re-typed).

## Determinism

No ``time``, no ``random``. Iteration is over sorted cells or provider order;
ties never break on set/dict iteration order.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.intervention_geometry import (  # noqa: E402
    contour_ring,
    polygon_area_ha,
)
from app.services.sandbox_model import (  # noqa: E402
    INTERVENTION_EFFECTS,
    MAX_INTERVENTION_COVERAGE,
    MAX_TOTAL_COVERAGE,
    _polygon_cells,
)
from app.services.siting import (  # noqa: E402
    MIN_SITE_CELLS,
    RESERVOIR_MIN_SITE_CELLS,
)

# --------------------------------------------------------------------------- #
# The honesty labels (§2.2). UPPER_SNAKE statuses and a non-empty warning.
# --------------------------------------------------------------------------- #

CONSTRAINT_STATUS = "PROPOSAL_ACCEPTED"
REJECT_STATUS = "PROPOSAL_REJECTED"
CONSTRAINT_WARNING = (
    "Screening proposal: geometry is the contoured cell mask of the chosen site "
    "on the 5 m grid. Depth is a class assumption; a run will score it."
)

# The closed reasonCode set (§2.2). Copied from the design, not invented; a test
# can assert *which* predicate fired without parsing English.
REASON_CODES: frozenset[str] = frozenset({
    "BUILDING_OVERLAP",
    "OVERLAPS_STAGED",
    "OVER_SINGLE_CAP",
    "OVER_CUMULATIVE_CAP",
    "TOO_SMALL",
    "CLIP_TOO_LARGE",
    "NO_LEGAL_GROUND",
    "NO_EFFECT",
    "NOT_A_SITE",
})

# A coordinate-based window is refused when more than this share of it is
# clipped away (D4 / §2.1).
MAX_CLIP_FRACTION = 0.25

# The area of one 5 m cell, for the cell <-> area conversions (§2.1 grid
# constants). Kept here so ``size_to_cells`` / ``cells_to_area_ha`` need no grid.
_CELL_AREA_M2 = 25.12

#: Which storage class each kind stores in, so the parameter block quotes the
#: depth the ledger will actually read (``_overlay_common.STORAGE_DEPTH_M``).
_STORAGE_CLASS_FOR_KIND: dict[str, str] = {
    "water_reservoir": "water",
    "flood_storage": "floodable",
    "underground_tank": "tank",
}


# --------------------------------------------------------------------------- #
# The building authority (§2.1, D3)
# --------------------------------------------------------------------------- #


def _polygon_footprint_mask(ov: Any) -> np.ndarray | None:
    """The polygon-exact building raster: a cell is under a building iff a cell
    centre falls inside one of the surveyed footprint polygons.

    Rasterised through ``sandbox_model._polygon_cells`` -- the *same* function
    the ledger uses to decide which cells an intervention converts, and the one
    ``validate_proposal`` reads a served geometry back with. A second
    point-in-polygon here would be a second opinion about which cells a polygon
    covers, and two opinions is how a "no building overlap" guarantee stops
    meaning anything.

    ``design_metrics.outer_ring`` reads the ring so a ``Polygon`` and a
    ``MultiPolygon`` arrive as one ring rather than as two nesting depths; the
    city model genuinely contains one ``MultiPolygon`` (``bldg-0104``), and
    reading its coordinates blindly drops the building instead of excluding it.

    ``None`` when there is no city model, for the same reason
    :func:`building_mask` returns ``None``.
    """
    import json

    from app.config import settings
    from app.services.design_metrics import outer_ring

    path = settings.rasters_dir / "buildings_hsk.json"
    if not path.exists():
        return None
    try:
        features = json.loads(path.read_text(encoding="utf-8")).get("features") or []
    except (OSError, ValueError):
        return None
    if not features:
        return None

    mask = np.zeros((ov.rows, ov.cols), dtype=bool)
    for feature in features:
        ring = outer_ring((feature or {}).get("geometry"))
        if not ring:
            continue
        # `ov` is accepted anywhere `grids` is: both carry the same bounds,
        # rows/cols and `cell_of`, which is the whole of what `_polygon_cells`
        # reads. Passing the overlay keeps this function free of a second load.
        for r, c in _polygon_cells(ov, [ring]):
            mask[int(r), int(c)] = True
    return mask


def building_mask(ov: Any) -> np.ndarray | None:
    """The one building authority: ``True`` where a cell is under a footprint.

    The **union** of two rasters over the *same* ``buildings_hsk.json``:

    1. the polygon-exact mask (:func:`_polygon_footprint_mask`) -- cell centre
       inside a footprint polygon, and
    2. ``_overlay_common.building_footprint_mask`` -- the legacy bounding-box
       envelope, which ``design_metrics.rasterise_height`` stamps as one
       axis-aligned rectangle per footprint.

    **Why the union and not the polygon set alone.** The gate's mask must never
    be *looser* than the filter that produced the candidate it is checking.
    ``ai_plan.load_grounding`` builds every candidate pool by subtracting the
    bounding-box mask, so a polygon-only gate would be ~4,500 cells looser than
    the pool filter -- the gate would be weaker than the proposal it is
    auditing. The union dominates both, and the direction is the point: for a
    "do not build on a surveyed footprint" contract, over-exclusion is a
    smaller failure than under-exclusion.

    **Why the union and not the bounding box alone.** The bounding-box raster is
    a *massing-height* raster being used as an occupancy test, and its own
    docstring says it is a display envelope. As an occupancy test it is wrong in
    both directions: it claims courtyards and gaps (4,549 cells that are not
    buildings), and -- the dangerous direction -- it *misses* real building cells
    that fall outside every footprint's bounding box. The second failure is what
    a proposal sitting on two surveyed building cells looked like.

    ``siting.py`` and ``placement_footprints`` keep the bounding-box mask: it is
    the right raster for a massing envelope, and widening it there would put
    three unrelated behaviours at risk. That leaves ``siting.py``'s pool filter
    leaking the same cells -- a real, known defect on a different path, filed
    rather than silently fixed here.

    Returns ``None`` when no city model is present (``buildings_hsk.json``
    absent). The caller **must** handle that case: an all-False mask would read
    as "no buildings exist", which is a different claim from "no data", and the
    design forbids the silent substitution (§2.1, D.2).
    """
    from _overlay_common import building_footprint_mask

    envelope = building_footprint_mask(ov)
    if envelope is None:
        # No city model. One authority's "no data" is the whole answer; the
        # other cannot disagree about a file that is not there.
        return None
    exact = _polygon_footprint_mask(ov)
    if exact is None:
        return envelope
    return envelope | exact


# --------------------------------------------------------------------------- #
# The claimed set (§2.1, the overlap predicate's input)
# --------------------------------------------------------------------------- #


def _plan_doc(plan_id: str) -> dict[str, Any] | None:
    from app import db

    return db.read_doc(db.plan_path(plan_id))


def _feature_owner(feature: Mapping[str, Any], fallback: str) -> str:
    """A stable label for one feature, for ``overlapsWith`` provenance.

    A site id when the feature has one (massing features set ``params.siteId``),
    else the feature id, else the plan id -- never a set-iteration order.
    """
    params = feature.get("params") or {}
    return str(
        feature.get("site_id")
        or feature.get("siteId")
        or params.get("siteId")
        or params.get("site_id")
        or feature.get("id")
        or fallback
    )


def _owner_map(
    grids: Any,
    plan_ids: Iterable[str] | None,
    extra_features: Iterable[Mapping[str, Any]] | None,
) -> dict[tuple[int, int], str]:
    """``{cell: owner}`` for every feature of the named plans and extras.

    The cells come from ``sandbox_model._polygon_cells`` -- the ledger's own
    rasteriser, holes subtracted -- so "claimed" means exactly "the cells the
    run would rework". ``plan_ids`` empty/None means "no staged plan" (D.2): the
    staged-plan default is the caller's job, not this function's.
    """
    owners: dict[tuple[int, int], str] = {}

    for plan_id in (plan_ids or []):
        if not plan_id:
            continue
        doc = _plan_doc(str(plan_id))
        if not doc:
            continue
        for feature in (doc.get("interventions") or []):
            if not isinstance(feature, Mapping):
                continue
            # An unpinned row is off the sandbox: the run does not compose it,
            # so it must not block new ground either.
            if feature.get("pinned") is False:
                continue
            coords = (feature.get("geometry") or {}).get("coordinates")
            if not coords:
                continue
            owner = _feature_owner(feature, str(plan_id))
            for cell in _polygon_cells(grids, coords):
                owners.setdefault((int(cell[0]), int(cell[1])), owner)

    for feature in (extra_features or []):
        if not isinstance(feature, Mapping):
            continue
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        owner = _feature_owner(feature, "claimed")
        for cell in _polygon_cells(grids, coords):
            owners.setdefault((int(cell[0]), int(cell[1])), owner)

    return owners


def claimed_owners(
    grids: Any,
    plan_ids: Iterable[str] | None = None,
    extra_features: Iterable[Mapping[str, Any]] | None = None,
) -> dict[tuple[int, int], str]:
    """The claimed cells as ``{cell: owner}`` (provenance for ``overlapsWith``)."""
    return _owner_map(grids, plan_ids, extra_features)


def claimed_cells(
    grids: Any,
    plan_ids: Iterable[str] | None = None,
    extra_features: Iterable[Mapping[str, Any]] | None = None,
) -> set[tuple[int, int]]:
    """The union of every claimed cell (frozen signature, D.2).

    ``plan_ids`` empty/None means "no staged plan". Use :func:`claimed_owners`
    when ``overlapsWith`` needs to name who owns a conflicting cell.
    """
    return set(_owner_map(grids, plan_ids, extra_features))


def claimed_cells_for(
    scene_id: str,
    grids: Any | None = None,
    extra_features: Iterable[Mapping[str, Any]] | None = None,
    plan_ids: Iterable[str] | None = None,
) -> set[tuple[int, int]]:
    """Convenience: a scene's claimed set, its staged plan included (§2.1).

    Resolves the staged plan through ``pins.staged_plan_id`` -- the one place
    that knows which plan is staged -- then defers to :func:`claimed_cells`.

    ``plan_ids`` (Phase 4 T04a, additive) are the run's **toggled projects**.
    They are claimed ground for the proposer's purposes even though they are
    not staged rows: a site that overlaps a project the user has switched on
    is a site the run already covers, and proposing it would offer work that
    is already there. Added rather than replacing the staged plan, because
    both are prior work and the caller holds both.
    """
    from app.services import pins

    if grids is None:
        from app.services.sandbox_model import load_grids

        grids = load_grids()
    ids: list[str] = []
    staged = pins.staged_plan_id(scene_id)
    if staged:
        ids.append(staged)
    for plan_id in plan_ids or []:
        if plan_id and plan_id not in ids:
            ids.append(plan_id)
    return claimed_cells(grids, ids, extra_features)


# --------------------------------------------------------------------------- #
# The size algebra (D.2: area -> cells, cells -> area, in one place each)
# --------------------------------------------------------------------------- #


def size_to_cells(area_ha: float | None, cell_area_m2: float = _CELL_AREA_M2) -> int:
    """Cells for a requested area (D.2: area -> cells is the caps' direction)."""
    if area_ha is None:
        return 0
    return max(0, int(round(float(area_ha) * 10_000.0 / float(cell_area_m2))))


def cells_to_area_ha(cells: int, cell_area_m2: float = _CELL_AREA_M2) -> float:
    """The reverse of :func:`size_to_cells`, for a size block with no polygon."""
    return round(float(cells) * float(cell_area_m2) / 10_000.0, 2)


# --------------------------------------------------------------------------- #
# The three predicates, each a pure function (§2.1)
# --------------------------------------------------------------------------- #


def _occupied_cells(occupied: Any) -> set[tuple[int, int]] | None:
    """Normalise the building authority to a cell set, or ``None`` if absent."""
    if occupied is None:
        return None
    if isinstance(occupied, np.ndarray):
        return {(int(r), int(c)) for r, c in np.argwhere(occupied)}
    return {(int(cell[0]), int(cell[1])) for cell in occupied}


def _as_cell_set(claimed: Any) -> set[tuple[int, int]]:
    """Accept a plain cell set or a ``{cell: owner}`` mapping."""
    if claimed is None:
        return set()
    if isinstance(claimed, Mapping):
        return {(int(k[0]), int(k[1])) for k in claimed}
    return {(int(cell[0]), int(cell[1])) for cell in claimed}


def _as_owner_map(claimed: Any) -> dict[tuple[int, int], str]:
    """Accept a plain cell set (all labelled ``"claimed"``) or a mapping."""
    if claimed is None:
        return {}
    if isinstance(claimed, Mapping):
        return {(int(k[0]), int(k[1])): str(v) for k, v in claimed.items()}
    return {(int(cell[0]), int(cell[1])): "claimed" for cell in claimed}


def building_overlap_cells(covered: Iterable[tuple[int, int]],
                           occupied: Any) -> set[tuple[int, int]]:
    """The cells of ``covered`` that sit under a building (§2.1, 0-cell tolerance).

    Empty when ``occupied`` is ``None``: "no data" is not "no buildings", and
    the caller is told which case it is through the ``buildingsClear`` label.
    """
    occ = _occupied_cells(occupied)
    if occ is None:
        return set()
    return {cell for cell in covered if cell in occ}


def overlap_cells(covered: Iterable[tuple[int, int]],
                  claimed: Any) -> set[tuple[int, int]]:
    """The cells of ``covered`` already claimed (§2.1, 0-cell tolerance).

    Edge-adjacency is allowed; a 1-cell overlap is a violation. Never clipped --
    an overlap is another feature's ground.
    """
    return {cell for cell in covered if cell in _as_cell_set(claimed)}


def size_violations(covered: Iterable[tuple[int, int]], claimed: Any,
                    total_cells: int,
                    kind: str = "unknown") -> dict[str, Any]:
    """The cap checks (§2.1), stated as booleans plus the numbers behind them.

    ``min_cells <= |covered| <= floor(0.25 * total)`` and
    ``|claimed U covered| <= floor(0.40 * total)``, with every threshold
    imported, never re-typed. The floor is per-kind: point/linear facilities
    (reservoir, drainage) use ``RESERVOIR_MIN_SITE_CELLS``, the areal pools use
    ``MIN_SITE_CELLS`` -- see :func:`_min_cells`.
    """
    cells = len(list(covered))
    min_cells = _min_cells(kind)
    max_single = int(math.floor(MAX_INTERVENTION_COVERAGE * total_cells))
    max_cumulative = int(math.floor(MAX_TOTAL_COVERAGE * total_cells))
    union = len(_as_cell_set(claimed) | set(covered))
    return {
        "tooSmall": cells < min_cells,
        "overSingle": cells > max_single,
        "overCumulative": union > max_cumulative,
        "cells": cells,
        "maxSingle": max_single,
        "maxCumulative": max_cumulative,
        "union": union,
        "minCells": min_cells,
    }


# --------------------------------------------------------------------------- #
# The centre+size -> geometry step (D2: snap, clip, contour)
# --------------------------------------------------------------------------- #


def snap_centre(grids: Any, lon: float, lat: float) -> tuple[int, int]:
    """Snap a lon/lat to a cell through the one cell map (``Grids.cell_of``)."""
    return grids.cell_of(float(lon), float(lat))


def _centre_lonlat(centre: Any) -> tuple[float, float]:
    if not isinstance(centre, Mapping):
        raise ValueError("placement centre must be an object with lon/lat")
    lon = centre.get("lon", centre.get("lng", centre.get("longitude")))
    lat = centre.get("lat", centre.get("latitude"))
    if lon is None or lat is None:
        raise ValueError("placement centre needs lon and lat")
    return float(lon), float(lat)


def _window_cells(grids: Any, row: int, col: int,
                  size_cells: int) -> list[tuple[int, int]]:
    """**Exactly** ``size_cells`` cells: the nearest to the snapped centre.

    The window is built ring by ring in Chebyshev distance from ``(row, col)``
    (ring 0 is the centre itself); within each ring the cells are taken in
    row-major ``(row, col)`` order. The scan stops the instant ``size_cells``
    in-grid cells have been collected, so the window is **never larger than the
    request**.

    This is the whole point of the function. An earlier version built the
    ceiling square ``ceil(sqrt(size_cells))`` cells per side, which served 49
    cells for a request of 39 -- a 26 % silent enlargement that cleared the
    40-cell floor -- and rounded a request of exactly 4,329 (``floor(0.25 x
    17,316)``, the design's own single-intervention cap) up to 4,356, refusing
    the largest legal intervention. The design states the boundary on *covered*
    (39/40/4329/4330), so served must equal requested before clipping.

    Off-grid cells are skipped rather than counted, so a centre near the edge
    still yields a full-size window; the scan ends when a whole ring adds no new
    in-grid cell (the grid is exhausted).

    Deterministic: Chebyshev ring, then ``(row, col)`` -- never set/dict
    iteration order.
    """
    wanted = max(1, int(size_cells))
    cells: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    limit = max(grids.rows, grids.cols) + 1
    r = 0
    while len(cells) < wanted and r <= limit:
        if r == 0:
            perimeter: list[tuple[int, int]] = [(row, col)]
        else:
            perimeter = []
            for dc in range(-r, r + 1):
                perimeter.append((row - r, col + dc))
                perimeter.append((row + r, col + dc))
            for dr in range(-r + 1, r):
                perimeter.append((row + dr, col - r))
                perimeter.append((row + dr, col + r))
            perimeter.sort()
        before = len(cells)
        for rr, cc in perimeter:
            if len(cells) >= wanted:
                break
            if (0 <= rr < grids.rows and 0 <= cc < grids.cols
                    and (rr, cc) not in seen):
                seen.add((rr, cc))
                cells.append((rr, cc))
        if len(cells) == before:
            break  # grid exhausted: this ring added no new in-grid cell
        r += 1
    return cells


def clip_window(grids: Any, centre: Any, size_cells: int, occupied: Any,
                claimed: Any = None) -> dict[str, Any]:
    """Snap a requested centre+size to cells, clip to legal ground, contour it.

    The window is **exactly** ``size_cells`` cells -- the nearest to
    ``Grids.cell_of`` of the centre, taken ring by ring in Chebyshev distance and
    row-major within a ring (:func:`_window_cells`). So ``kept`` (and therefore
    ``size.cells``) equals the request before clipping: a request of 39 serves
    39, not 49, and 4,329 is requestable.

    "Legal ground" is every window cell that is **not** under a surveyed
    building; the clip is a cell-set subtraction, never a polygon operation
    (D7 -- no clipping library).

    ``claimed`` is accepted and reported (``overlapCells``) but **not** removed:
    an overlap is another feature's ground and is refused by the overlap
    predicate, not silently clipped away (§2.1).

    Returns the kept cells, the window/kept/clipped counts, the
    ``clippedFraction = 1 - kept/window``, the snapped centre cell, and the
    contoured geometry of the kept cells (``None`` when they are empty).
    """
    lon, lat = _centre_lonlat(centre)
    row, col = snap_centre(grids, lon, lat)

    window = _window_cells(grids, row, col, size_cells)

    occ = _occupied_cells(occupied)
    kept = {cell for cell in window if occ is None or cell not in occ}

    geometry = None
    served = 0
    if kept:
        contour = contour_ring(sorted(kept), bounds=grids.bounds,
                               rows=grids.rows, cols=grids.cols)
        if contour is not None:
            geometry, served, _of_component = contour

    # ``clippedCells`` is the whole difference between what was asked for and
    # what is served -- the building cells removed **plus** any fragment
    # `contour_ring` stranded by returning the largest ring only. Counting the
    # buildings alone under-reports: a bisecting building can leave survivors in
    # two pieces, and the smaller piece is silently not served. That number is
    # the one that makes the field auditable, so it has to be the total
    # shortfall rather than the part of it that is easy to see.
    #
    # When no contour came back at all, nothing is served and the whole window
    # is the shortfall -- which is also what ``kept`` being empty gives.
    clipped = len(window) - (served if geometry is not None else 0)
    fraction = (clipped / len(window)) if window else 0.0

    overlap = len(kept & _as_cell_set(claimed)) if claimed is not None else 0

    return {
        "cells": kept,
        "window": len(window),
        "kept": len(kept),
        "served": served,
        "clipped": clipped,
        "clippedFraction": round(fraction, 6),
        "centreCell": (row, col),
        "geometry": geometry,
        "overlapCells": overlap,
    }


# --------------------------------------------------------------------------- #
# Effect + parameters, so the gate can disclose what the run will read (§2.2)
# --------------------------------------------------------------------------- #


def effect_for_kind(kind: str) -> tuple[str, str | None]:
    """``(effect, effectNote)`` for a kind (§2.2).

    ``class_change`` when the kind has a ``to`` class, ``cn_only`` when it
    carries a curve number, else ``none`` -- and ``none`` carries
    ``INTERVENTION_EFFECTS[kind]['why']`` so the gate never promises a number
    the run will not produce (D8).
    """
    effect = INTERVENTION_EFFECTS.get(kind)
    if effect is None:
        return "none", None
    if effect.get("to") is not None:
        return "class_change", effect.get("why")
    if "cn" in effect:
        return "cn_only", effect.get("why")
    return "none", effect.get("why")


# --------------------------------------------------------------------------- #
# questions_for_kind -- the six topics, generated by code (Phase 2 R8-R11)
# --------------------------------------------------------------------------- #

# Which kinds make the site-level questions apply. Drainage/overflow is a
# question only where the model could not answer it itself: a pipe network's
# capacity is not in this dataset, so the `to=None` kinds carry a `why` that
# says so. Green space and paving have no pipe.
_DRAINAGE_KINDS: frozenset[str] = frozenset({
    "drainage", "drainage_alignment", "road", "flood_storage",
})

# The question a parameter's answer is *for*, in the unit the answer is given
# in. Kept beside the parameter name so a new parameter cannot be published
# without a question and a unit, and a question cannot name a parameter that
# is not read (R9's "exactly the parameters that were published").
_PARAMETER_QUESTIONS: dict[str, dict[str, str]] = {
    "location": {
        "prompt": ("Is this the right place for this intervention? It sits at "
                   "the centre the request named; if the ground should move, "
                   "say where."),
        "unit": "",
    },
    "footprint": {
        "prompt": ("Is this footprint the one you want? It is the size the "
                   "server could serve on the 5 m grid after removing "
                   "surveyed buildings."),
        "unit": "ha",
    },
    "surfaceReplaced": {
        "prompt": ("What surface does this replace on the ground? The ledger "
                   "will score it as the class named in the answer; it does "
                   "not survey the site."),
        "unit": "",
    },
    "usableStorageDepth": {
        "prompt": ("How deep is the usable storage here, in metres? The run "
                   "reads this number directly; the value shown is the class "
                   "default for this kind, which is an assumption, not a "
                   "survey of the site."),
        "unit": "m",
    },
    "drainageOverflow": {
        "prompt": ("Who does the overflow drain to, and at what capacity? "
                   "This dataset carries no pipe network, so the run cannot "
                   "sized it and will not guess."),
        "unit": "",
    },
    "treeCover": {
        "prompt": ("How much tree cover does this site have, in square "
                   "metres? The land-cover raster has a single `green` class "
                   "and does not separate trees, so the run counts green "
                   "ground, never trees."),
        "unit": "m2",
    },
}


def question_id(parameter: str, *, scope: str = "PROPOSAL") -> str:
    """The stable id for a question (R10).

    Derived from the parameter alone -- never an index, a counter, or a hash
    of anything ordered -- so the same parameter yields the same id on any
    machine and in any order, and an answer matches its question by string
    equality with no server state in between.
    """
    return parameter if scope == "PROPOSAL" else "site." + parameter


def _question(parameter: str, *, scope: str, status: str = "OPEN",
              value: Any = None, answered: Any = None) -> dict[str, Any]:
    spec = _PARAMETER_QUESTIONS[parameter]
    return {
        "id": question_id(parameter, scope=scope),
        "parameter": parameter,
        "scope": scope,
        "prompt": spec["prompt"],
        "unit": spec["unit"],
        "value": value,
        "answer": answered,
        "answeredValue": None,
        "status": status,
    }


def questions_for_kind(kind: str, params: Mapping[str, Any] | None = None,
                       *, answers: Mapping[str, Any] | None = None,
                       ) -> list[dict[str, Any]]:
    """The ``PROPOSAL``-scope questions for one proposal, generated from code (R9).

    A **pure function** of the kind and the params the ledger will read -- the
    model does not supply the question, only the answer it cannot know. Every
    question is bound to a parameter that :func:`parameters_for_kind`
    published, so a question can never exist for a parameter nothing reads,
    and a published parameter can never go unquestioned (R11).

    ``PROPOSAL`` scope only: location, footprint, surface replaced and usable
    storage depth are properties of *one* proposal, and a reservoir and a
    paving slab in the same batch have different footprints and depths. The
    two site-level topics live in :func:`site_questions`, asked once per batch
    (R8).

    ``answers`` maps question id -> the planner's typed answer. A matched
    question comes back ``ANSWERED`` with the server's parse of the answer in
    ``answeredValue`` (``None`` when the answer was not numeric), and the
    caller is responsible for *honouring* it (R13) -- the number the server
    parsed is the one that reaches ``params``, never the client's.
    """
    params = params if isinstance(params, Mapping) else {}
    answers = answers or {}
    out: list[dict[str, Any]] = []

    def emit(parameter: str, value: Any = None) -> None:
        question = _question(parameter, scope="PROPOSAL", value=value)
        _apply_answer(question, answers)
        out.append(question)

    emit("location", (params.get("requested") or {}).get("centre")
         if isinstance(params.get("requested"), Mapping) else None)
    emit("footprint")

    effect, _note = effect_for_kind(kind)
    if effect == "class_change":
        emit("surfaceReplaced", (INTERVENTION_EFFECTS.get(kind) or {}).get("to"))

    if kind in _STORAGE_CLASS_FOR_KIND:
        emit("usableStorageDepth", params.get("storageDepthM"))

    return out


def site_questions(batch: Iterable[Any],
                   answers: Mapping[str, Any] | None = None,
                   ) -> list[dict[str, Any]]:
    """The ``SITE``-scope questions for an accepted batch (R8).

    Generated once per batch rather than once per proposal: drainage and tree
    cover are properties of the site, and asking them six times because six
    proposals share it is the duplication the ``scope`` discriminator exists
    to prevent.
    """
    kinds = {_kind_of(c) for c in batch}
    answers = answers or {}
    out: list[dict[str, Any]] = []
    for parameter in ("drainageOverflow", "treeCover"):
        if parameter == "drainageOverflow" and not (kinds & _DRAINAGE_KINDS):
            continue
        question = _question(parameter, scope="SITE")
        _apply_answer(question, answers)
        out.append(question)
    return out


def _apply_answer(question: dict[str, Any],
                  answers: Mapping[str, Any]) -> None:
    """Record a planner's answer on its question, in place (R12).

    The parse is the **server's**, never the client's: the answer text is what
    the planner typed, ``answeredValue`` is what this side made of it, and a
    client that sends a mismatched pair gets the server's number back and can
    see the disagreement.
    """
    raw = answers.get(question["id"])
    if raw is None:
        return
    question["status"] = "ANSWERED"
    question["answer"] = str(raw)
    question["answeredValue"] = _parse_answer(raw)


def _parse_answer(raw: Any) -> float | None:
    """The server's parse of a typed answer; ``None`` when it is not numeric.

    Deliberately forgiving about presentation and strict about value: "0.8",
    "0.8 m" and "about 0.8" all yield ``0.8``, because a planner typing a
    depth should not have to spell it the way a JSON field would. Anything
    with no number in it yields ``None`` rather than a guess (R12) -- the
    server reports that it could not parse, rather than inventing a value.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return None if value != value else value
    text = str(raw).strip()
    if not text:
        return None
    # A depth may be written in metres or centimetres: "80 cm" is 0.80 m.
    number = _NUMBER_RE.search(text)
    if number is None:
        return None
    try:
        value = float(number.group(0).replace(",", "."))
    except ValueError:  # pragma: no cover - the regex already guarantees this
        return None
    if _CENTIMETRE_RE.search(text):
        value /= 100.0
    return value


_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_CENTIMETRE_RE = re.compile(r"\bcm\b|centimet", re.IGNORECASE)


def parameters_for_kind(kind: str,
                        override: float | None = None) -> dict[str, dict[str, Any]]:
    """The parameters ``apply_interventions`` actually reads for ``kind`` (§2.2).

    Only ``storageDepthM`` is read, for the three storage kinds; each entry
    carries its ``value``/``unit``/``status``/``source``. ``curveNumber`` is
    **not** read by the ledger (Q4), so it is reported as an unread assumption
    rather than silently offered as a lever.

    ``override`` is a depth the planner set by hand in the confirm gate (R17).
    It changes the entry's **shape**, not just its wording: ``status`` becomes
    ``USER_ASSUMPTION``, ``edited`` becomes true, and ``defaultValue`` carries
    the class default that was replaced -- mandatory, because without it the
    card cannot show what was overridden and the served value becomes
    unrecoverable from the record. The ``source`` string names the class
    default only in the clause saying what it is *not*; a reader must never be
    able to mistake it for where ``value`` came from.

    ``confidence`` stays ``ASSUMED`` on both paths on purpose: a planner's
    estimate is not more knowable than a class default, and labelling it
    better-known would launder a guess into a measurement.
    """
    from _overlay_common import STORAGE_DEPTH_M

    out: dict[str, dict[str, Any]] = {}

    storage_class = _STORAGE_CLASS_FOR_KIND.get(kind)
    if storage_class is not None and storage_class in STORAGE_DEPTH_M:
        default = float(STORAGE_DEPTH_M[storage_class])
        entry: dict[str, Any] = {
            "value": default,
            "unit": "m",
            "status": "ASSUMPTION",
            "source": (
                "scripts/_overlay_common.STORAGE_DEPTH_M['%s'] (class default), "
                "an assumption, not a survey" % storage_class
            ),
            "confidence": "ASSUMED",
            "edited": False,
        }
        if override is not None:
            entry.update({
                "value": float(override),
                "status": "USER_ASSUMPTION",
                "source": (
                    "a planner set this by hand in the confirm gate; the class "
                    "default scripts/_overlay_common.STORAGE_DEPTH_M['%s'] = %s "
                    "is what the run would otherwise have read, and it is NOT "
                    "what this run reads" % (storage_class, _fmt(default))
                ),
                "defaultValue": default,
                "edited": True,
            })
        out["storageDepthM"] = entry

    if kind == "permeable_paving":
        effect = INTERVENTION_EFFECTS.get("permeable_paving") or {}
        out["curveNumber"] = {
            "value": float(effect.get("cn", 72.0)),
            "unit": "",
            "status": "ASSUMPTION",
            "source": (
                "sandbox_model.INTERVENTION_EFFECTS['permeable_paving']['cn'] "
                "-- a fixed class effect; apply_interventions does not read a "
                "caller-supplied curveNumber"
            ),
            "confidence": "ASSUMED",
            "edited": False,
        }

    return out


def _fmt(value: float) -> str:
    """A number as the record spells it: ``1.5``, not ``1.5000000000000002``."""
    text = ("%.2f" % float(value)).rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------- #
# validate_proposal -- the frozen return contract (D.2)
# --------------------------------------------------------------------------- #


def _as_mapping(candidate: Any) -> dict[str, Any]:
    """Accept a feature dict or a Pydantic proposal; return snake_case keys."""
    if isinstance(candidate, Mapping):
        return dict(candidate)
    dump = getattr(candidate, "model_dump", None)
    if callable(dump):
        return dump(by_alias=False)
    return dict(candidate)


def _kind_of(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("intervention_type")
        or candidate.get("interventionType")
        or candidate.get("kind")
        or "unknown"
    )


def _site_id_of(candidate: Mapping[str, Any]) -> str | None:
    params = candidate.get("params") or {}
    value = (
        candidate.get("site_id")
        or candidate.get("siteId")
        or params.get("siteId")
        or params.get("site_id")
    )
    return str(value) if value is not None else None


# The intervention kinds that are *point/linear* facilities rather than areal
# land-use changes, and so carry the smaller ``RESERVOIR_MIN_SITE_CELLS`` floor
# instead of the areal ``MIN_SITE_CELLS``. ``drainage`` is the alias the
# proposal path maps onto the same reservoir pool (see ``ai_plan.POOL_FOR_KIND``),
# so it shares the reservoir floor rather than splitting a single facility
# concept across two sizes.
_RESERVOIR_KINDS = frozenset({"water_reservoir", "drainage"})


def _min_cells(kind: str) -> int:
    """The size floor for ``kind``: point/linear facilities are smaller.

    ``siting.reservoir_candidates`` admits 20-cell (500 m2) neighbourhood-scale
    detention basins, but the gate's global floor is 40 cells (1,000 m2) for the
    areal pools (paving, greening). A reservoir candidate of 20-39 cells would be
    refused ``TOO_SMALL`` the moment it reached the gate, so the gate reads the
    same per-kind floor the siting pool was built with, instead of a single
    number that can never match both.
    """
    if kind in _RESERVOIR_KINDS:
        return RESERVOIR_MIN_SITE_CELLS
    return MIN_SITE_CELLS


def _requested_of(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    """The raw centre+size ask, from ``requested`` or ``placement`` (§2.2)."""
    params = candidate.get("params") or {}
    for key in ("requested", "placement"):
        value = candidate.get(key)
        if isinstance(value, Mapping):
            return dict(value)
        value = params.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _constraints_block(
    *,
    buildings_clear: bool,
    building_overlap: int,
    overlap: int,
    overlaps_with: list[str],
    clipped: int,
    clipped_fraction: float,
    within_single: bool,
    within_cumulative: bool,
    effect: str,
    effect_note: str | None,
) -> dict[str, Any]:
    return {
        "buildingsClear": bool(buildings_clear),
        "buildingOverlapCells": int(building_overlap),
        "buildingIds": [],
        "overlapCells": int(overlap),
        "overlapsWith": list(overlaps_with),
        "clippedCells": int(clipped),
        "clippedFraction": float(clipped_fraction),
        "withinSingleCap": bool(within_single),
        "withinCumulativeCap": bool(within_cumulative),
        "effect": str(effect),
        "effectNote": str(effect_note or ""),
    }


def _size_block(grids: Any, geometry: dict[str, Any] | None,
                covered: set[tuple[int, int]]) -> dict[str, Any]:
    """``ProposalSize`` for the **served** geometry (§2.2, exact field rules)."""
    total = grids.rows * grids.cols
    if geometry is not None:
        coords = geometry.get("coordinates")
        cells = len(_polygon_cells(grids, coords))
        area = round(polygon_area_ha(coords, grids.bounds[1], grids.bounds[3]), 2)
    else:
        cells = len(covered)
        area = cells_to_area_ha(cells, grids.cell_area_m2)
    return {
        "cells": int(cells),
        "areaHa": float(area),
        "coverageFraction": round(cells / total, 5) if total else 0.0,
    }


def _owners_of(overlap: Iterable[tuple[int, int]],
               owner_map: Mapping[tuple[int, int], str],
               fallback: str = "claimed") -> list[str]:
    """Owner labels of the overlapping cells, first-seen in sorted cell order."""
    out: list[str] = []
    for cell in sorted(overlap):
        label = owner_map.get(cell, fallback)
        if label not in out:
            out.append(label)
    return out


def _accepted_warning(coordinate_based: bool, clipped: int,
                      building_data: bool, effect: str,
                      effect_note: str | None,
                      edited_depth: tuple[float, float] | None = None) -> str:
    """The accepted proposal's honesty label.

    ``edited_depth`` is ``(value, class_default)`` when a planner set the
    usable storage depth by hand (R18). It **replaces** the class-assumption
    sentence rather than joining it: "Depth is a class assumption" becomes
    false the moment the depth is not the class default, so emitting both
    would put a class-default claim over a hand-set number -- which is the
    exact disclosure failure this sentence exists to prevent.
    """
    if coordinate_based:
        parts = ["Screening proposal: geometry is the server-snapped, clipped "
                 "contour of the requested centre and size on the 5 m grid."]
        if clipped:
            parts.append("%d cell(s) under surveyed buildings were removed."
                         % clipped)
    else:
        parts = ["Screening proposal: geometry is the contoured cell mask of "
                 "the chosen site on the 5 m grid."]
        # A site-based proposal is clipped too -- the gate's building authority
        # is stricter than the pool filter that produced the site -- so the
        # shrink has to be stated here as well. PRD US1 promises a
        # "shrunk, N cells removed" annotation, and a user who was offered a
        # 3,596-cell paving site and is shown 3,579 needs to be told why, not
        # left to diff two numbers against a candidate they cannot see.
        if clipped:
            parts.append("The site was shrunk by %d cell(s) to exclude "
                         "surveyed buildings, and to serve only the part of "
                         "the remaining ground that is one contiguous polygon."
                         % clipped)
    if edited_depth is None:
        parts.append("Depth is a class assumption; a run will score it.")
    else:
        value, default = edited_depth
        parts.append(
            "usable storage depth %s m was set by hand in the confirm gate, "
            "not read from the class default (%s m); the run will use the "
            "hand-set value." % (_fmt(value), _fmt(default)))
    if not building_data:
        parts.append("BUILDING_CHECK_UNAVAILABLE: no surveyed building footprint "
                     "raster is present, so building exclusion could not be "
                     "verified.")
    if effect == "none":
        parts.append("No land-cover effect: %s."
                     % (effect_note or "this intervention type does not change "
                                       "the ledger"))
    return " ".join(parts)


def _reject_warning(reason: str, *, why: str, building_overlap: int,
                    overlap: int, overlaps_with: list[str],
                    viol: Mapping[str, Any],
                    clipped_fraction: float) -> str:
    if reason == "BUILDING_OVERLAP":
        return ("PROPOSAL_REJECTED: this placement contains %d cell(s) under "
                "surveyed buildings; it is not available as a site"
                % building_overlap)
    if reason == "OVERLAPS_STAGED":
        who = ", ".join(overlaps_with) if overlaps_with else "the staged scenario"
        return ("PROPOSAL_REJECTED: this placement overlaps %d cell(s) already "
                "claimed by %s; move it or remove the conflicting intervention "
                "first" % (overlap, who))
    if reason == "TOO_SMALL":
        return ("PROPOSAL_REJECTED: this placement covers only %d cell(s); the "
                "minimum site is %d cells"
                % (viol["cells"], viol["minCells"]))
    if reason == "OVER_SINGLE_CAP":
        return ("PROPOSAL_REJECTED: this placement covers %d cells, over the "
                "%d-cell single-intervention cap"
                % (viol["cells"], viol["maxSingle"]))
    if reason == "OVER_CUMULATIVE_CAP":
        return ("PROPOSAL_REJECTED: this placement would bring the scenario's "
                "total claimed land to %d cells, over the %d-cell cumulative cap"
                % (viol["union"], viol["maxCumulative"]))
    if reason == "CLIP_TOO_LARGE":
        return ("PROPOSAL_REJECTED: this placement loses %.0f%% of its window to "
                "surveyed buildings, over the %.0f%% the design accepts"
                % (clipped_fraction * 100.0, MAX_CLIP_FRACTION * 100.0))
    if reason == "NO_LEGAL_GROUND":
        return ("PROPOSAL_REJECTED: this placement leaves no buildable 5 m cell "
                "-- every cell in its window is under a building or off the "
                "study area")
    if reason == "NO_EFFECT":
        return ("PROPOSAL_REJECTED: this intervention type produces no "
                "land-cover effect in the ledger (%s), so it cannot be proposed "
                "as a scored intervention" % (why or "no defined effect"))
    if reason == "NOT_A_SITE":
        return ("PROPOSAL_REJECTED: this candidate is not a usable site (%s)"
                % (why or "no polygon geometry, or it covers no 5 m cell"))
    return "PROPOSAL_REJECTED: the placement is not available."


def validate_proposal(grids: Any, candidate: Any, occupied: Any,
                      claimed: Any) -> dict[str, Any]:
    """Check one candidate against the three predicates and freeze its geometry.

    ``candidate`` is the feature dict under test -- a ``geometry``, an optional
    ``requested``/``placement`` (centre + size), and an optional ``siteId``. It
    is *not yet* a proposal, which is why the input is not named ``proposal``.

    ``occupied`` is the building authority (a boolean raster from
    :func:`building_mask`, a cell set, or ``None`` when no city model exists).
    ``claimed`` is the already-claimed set -- a plain cell set or a
    ``{cell: owner}`` mapping (see :func:`claimed_owners`).

    Returns the frozen D.2 contract, exactly six keys:

    ``status`` / ``warning`` / ``reasonCode`` / ``constraints`` / ``size`` /
    ``geometry``. ``reasonCode`` is ``None`` iff accepted; ``geometry`` is the
    **served** polygon (the clipped contour for a placement, the candidate's own
    geometry for a site) and is ``None`` iff rejected.

    Provider order is preserved by the caller; this function is pure and
    deterministic.
    """
    cand = _as_mapping(candidate)
    kind = _kind_of(cand)
    requested = _requested_of(cand)
    owner_map = _as_owner_map(claimed)
    claimed_set = set(owner_map)
    occ = _occupied_cells(occupied)
    total = grids.rows * grids.cols
    effect, effect_note = effect_for_kind(kind)

    coordinate_based = requested is not None
    clipped = 0
    clipped_fraction = 0.0
    window_n = 0
    geometry: dict[str, Any] | None = None
    covered: set[tuple[int, int]] = set()

    if coordinate_based:
        centre = requested.get("centre") or requested.get("center")
        size_cells = requested.get("sizeCells", requested.get("size_cells"))
        if size_cells is None:
            area = requested.get("areaHa", requested.get("area_ha"))
            size_cells = size_to_cells(area) if area is not None else None
        if centre is None or not size_cells:
            return _finalize(
                grids, REJECT_STATUS, "NOT_A_SITE",
                why="the placement carries no usable centre and size",
                coordinate_based=True, geometry=None, covered=set(),
                clipped=0, clipped_fraction=0.0, occ=occ, claimed_set=claimed_set,
                owner_map=owner_map, effect=effect, effect_note=effect_note,
                kind=kind,
            )
        try:
            clip = clip_window(grids, centre, size_cells, occupied, claimed_set)
        except (TypeError, ValueError) as exc:
            return _finalize(
                grids, REJECT_STATUS, "NOT_A_SITE",
                why="the placement centre is malformed (%s)" % exc,
                coordinate_based=True, geometry=None, covered=set(),
                clipped=0, clipped_fraction=0.0, occ=occ, claimed_set=claimed_set,
                owner_map=owner_map, effect=effect, effect_note=effect_note,
                kind=kind,
            )
        covered = set(clip["cells"])
        clipped = int(clip["clipped"])
        clipped_fraction = float(clip["clippedFraction"])
        window_n = int(clip["window"])
        geometry = clip["geometry"]
    else:
        geom = cand.get("geometry")
        coords = (geom or {}).get("coordinates")
        if not coords:
            return _finalize(
                grids, REJECT_STATUS, "NOT_A_SITE",
                why="the candidate has no polygon geometry",
                coordinate_based=False, geometry=None, covered=set(),
                clipped=0, clipped_fraction=0.0, occ=occ, claimed_set=claimed_set,
                owner_map=owner_map, effect=effect, effect_note=effect_note,
                kind=kind,
            )
        covered = {(int(r), int(c)) for r, c in _polygon_cells(grids, coords)}
        if not covered:
            return _finalize(
                grids, REJECT_STATUS, "NOT_A_SITE",
                why="the polygon covers no 5 m cell",
                coordinate_based=False, geometry=None, covered=set(),
                clipped=0, clipped_fraction=0.0, occ=occ, claimed_set=claimed_set,
                owner_map=owner_map, effect=effect, effect_note=effect_note,
                kind=kind,
            )
        geometry = dict(geom)
        # --- the contour clip guard -----------------------------------------
        #
        # A candidate from a grounding pool arrives as a whole contoured
        # component. The pool filter that produced it subtracts one building
        # raster; this gate checks a stricter one, so a component can carry
        # building cells the pool never saw. Rejecting the whole component would
        # be correct on the predicate and useless in practice -- the largest
        # basin loses 2 cells and the user is offered nothing at all, which is
        # how a correctness fix turns into a dead feature.
        #
        # So the site path clips instead: subtract the building authority, then
        # re-contour the survivors through `contour_ring` -- the one contour
        # authority (D2) -- and serve *that*.
        #
        # This is LOAD-BEARING on the current data, not a structural guarantee
        # for a future pool: it fires on 2 of the 9 sites offered today
        # (the reservoir sheds 2 cells, the largest paving site 16). Both halves
        # of this change are load-bearing for the same reason -- removing the
        # polygon side of the building mask drops the suite to 17/18, and
        # removing this guard drops it to 16/18.
        #
        # `contour_ring` returns the LARGEST ring only, so a bisecting building
        # can leave survivors in two pieces and the smaller one is not served.
        # That is disclosed rather than hidden: `clipped` below counts every
        # candidate cell that is not in the served geometry -- buildings *and*
        # any stranded fragment -- so `clippedCells` is the whole difference
        # between what the pool offered and what the user is shown, which is
        # the number that makes the field auditable (design §2.2).
        if occ is not None:
            survivors = covered - occ
            if survivors != covered:
                dropped_buildings = len(covered) - len(survivors)
                if len(survivors) < _min_cells(kind):
                    return _finalize(
                        grids, REJECT_STATUS, "BUILDING_OVERLAP",
                        why=("only %d of the site's %d cells survive the "
                             "building exclusion, below the %d-cell minimum"
                             % (len(survivors), len(covered), _min_cells(kind))),
                        coordinate_based=False, geometry=None,
                        covered=covered, clipped=dropped_buildings,
                        clipped_fraction=0.0,
                        occ=occ, claimed_set=claimed_set, owner_map=owner_map,
                        effect=effect, effect_note=effect_note, kind=kind,
                        building_overlap=dropped_buildings,
                    )
                contour = contour_ring(
                    sorted(survivors), bounds=grids.bounds,
                    rows=grids.rows, cols=grids.cols)
                if contour is None:
                    # No ring at all: nothing legal left to serve. Refused
                    # rather than served un-clipped, which would put the
                    # building cells straight back on the map.
                    return _finalize(
                        grids, REJECT_STATUS, "BUILDING_OVERLAP",
                        why=("the site's legal ground does not contour to a "
                             "single polygon once buildings are excluded"),
                        coordinate_based=False, geometry=None,
                        covered=covered, clipped=dropped_buildings,
                        clipped_fraction=0.0,
                        occ=occ, claimed_set=claimed_set, owner_map=owner_map,
                        effect=effect, effect_note=effect_note, kind=kind,
                        building_overlap=dropped_buildings,
                    )
                geometry = contour[0]
                covered = {(int(r), int(c)) for r, c in _polygon_cells(
                    grids, geometry["coordinates"])}
                # Every candidate cell the user is NOT being shown: the ones
                # under buildings, plus any surviving piece the single served
                # ring could not include.
                clipped = len(survivors) - len(covered)
                clipped += dropped_buildings
                if len(covered) < _min_cells(kind):
                    return _finalize(
                        grids, REJECT_STATUS, "BUILDING_OVERLAP",
                        why=("the site's legal ground does not contour to a "
                             "single polygon of at least %d cells once "
                             "buildings are excluded" % _min_cells(kind)),
                        coordinate_based=False, geometry=None,
                        covered=covered, clipped=clipped, clipped_fraction=0.0,
                        occ=occ, claimed_set=claimed_set, owner_map=owner_map,
                        effect=effect, effect_note=effect_note, kind=kind,
                        building_overlap=dropped_buildings,
                    )

    # --- the three predicates, in §2.1's order -----------------------------
    building_overlap = building_overlap_cells(covered, occupied)
    overlap = overlap_cells(covered, claimed_set)
    overlaps_with = _owners_of(overlap, owner_map)
    viol = size_violations(covered, claimed_set, total, kind=kind)

    reason: str | None = None
    why = ""
    if coordinate_based:
        if window_n == 0 or not covered:
            reason, why = "NO_LEGAL_GROUND", "the window is empty"
        elif clipped_fraction > MAX_CLIP_FRACTION:
            reason = "CLIP_TOO_LARGE"
    if reason is None and not coordinate_based and building_overlap:
        reason = "BUILDING_OVERLAP"
    if reason is None and overlap:
        reason = "OVERLAPS_STAGED"
    if reason is None and viol["tooSmall"]:
        reason = "TOO_SMALL"
    if reason is None and viol["overSingle"]:
        reason = "OVER_SINGLE_CAP"
    if reason is None and viol["overCumulative"]:
        reason = "OVER_CUMULATIVE_CAP"

    return _finalize(
        grids,
        REJECT_STATUS if reason else CONSTRAINT_STATUS,
        reason,
        why=why,
        coordinate_based=coordinate_based,
        geometry=geometry,
        covered=covered,
        clipped=clipped,
        clipped_fraction=clipped_fraction,
        occ=occ,
        claimed_set=claimed_set,
        owner_map=owner_map,
        effect=effect,
        effect_note=effect_note,
        kind=kind,
        building_overlap=len(building_overlap),
        overlap=len(overlap),
        overlaps_with=overlaps_with,
        viol=viol,
        edited_depth=override_depth(kind, cand),
    )


def override_depth(kind: str, candidate: Mapping[str, Any] | None,
                   ) -> tuple[float, float] | None:
    """``(value, class_default)`` when the proposal's depth is a hand-set one.

    ``None`` when the depth is absent, or is the class default itself -- a
    planner who types back exactly the default number has not overridden
    anything, and labelling it ``USER_ASSUMPTION`` would claim an edit that
    did not happen. A depth the server issued for a suggestion is
    indistinguishable from the default here and is correctly unedited.

    Reads the depth from ``params``, which is the key ``apply_interventions``
    itself reads, so the label and the scored value cannot come from two
    different places.
    """
    from _overlay_common import STORAGE_DEPTH_M

    storage_class = _STORAGE_CLASS_FOR_KIND.get(kind)
    if storage_class is None or storage_class not in STORAGE_DEPTH_M:
        return None
    params = (candidate or {}).get("params") or {}
    raw = params.get("storageDepthM", params.get("storage_depth_m"))
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    default = float(STORAGE_DEPTH_M[storage_class])
    return None if value == default else (value, default)


def apply_answers(kind: str, candidate: Mapping[str, Any],
                  answers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Write a planner's answered depth into the candidate's ``params`` (R13).

    The load-bearing half of ``questions[]``: a question the model asks and the
    server then ignores is worse than no question at all, because it teaches
    the planner that answering changes nothing.

    Applied **before** validation, deliberately. ``params.storageDepthM`` is
    the one key ``apply_interventions`` reads, so writing the parsed answer
    there is what makes the answer reach the run -- and it means the served
    value, the ``parameters`` block and the proposal's ``warning`` all read one
    number from one place rather than three views of the answer that can
    disagree. It also keeps the frozen ``validate_proposal`` /
    ``validate_batch`` signatures untouched (§7): nothing new is threaded
    through them.

    The **server's** parse is what lands, never the client's ``answeredValue``
    (R12): "about 80 cm" becomes 0.80 m here, and an answer with no usable
    number, or one outside the accepted range, leaves the class default
    untouched. Either way the question still comes back ``ANSWERED`` carrying
    the parse, so a client can see that the answer was read and why it did not
    apply.

    The answer is not persisted server-side; the client carries it forward,
    exactly as it carries the proposal (the confirm gate is stateless).
    """
    if not answers:
        return dict(candidate)
    value = _answered_depth(kind, answers)
    if value is None:
        return dict(candidate)
    params = dict(candidate.get("params") or {})
    params["storageDepthM"] = value
    return {**dict(candidate), "params": params}


def _answered_depth(kind: str, answers: Mapping[str, Any]) -> float | None:
    """The depth an answered ``usableStorageDepth`` question implies, if usable."""
    from app.services.sandbox_model import depth_is_acceptable

    if kind not in _STORAGE_CLASS_FOR_KIND:
        return None
    raw = answers.get(question_id("usableStorageDepth"))
    if raw is None:
        return None
    value = _parse_answer(raw)
    if value is None or not depth_is_acceptable(value):
        return None
    return value


def _finalize(
    grids: Any,
    status: str,
    reason: str | None,
    *,
    why: str,
    coordinate_based: bool,
    geometry: dict[str, Any] | None,
    covered: set[tuple[int, int]],
    clipped: int,
    clipped_fraction: float,
    occ: set[tuple[int, int]] | None,
    claimed_set: set[tuple[int, int]],
    owner_map: Mapping[tuple[int, int], str],
    effect: str,
    effect_note: str | None,
    kind: str,
    building_overlap: int = 0,
    overlap: int = 0,
    overlaps_with: list[str] | None = None,
    viol: Mapping[str, Any] | None = None,
    edited_depth: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Assemble the frozen six-key contract from the computed predicate state."""
    accepted = reason is None
    if viol is None:
        viol = size_violations(covered, claimed_set,
                               grids.rows * grids.cols,
                               kind=kind) if covered else {
            "tooSmall": True, "overSingle": False, "overCumulative": False,
            "cells": 0, "maxSingle": 0, "maxCumulative": 0, "union": 0,
            "minCells": _min_cells(kind),
        }
    overlaps_with = overlaps_with or []

    constraints = _constraints_block(
        buildings_clear=(occ is not None) and building_overlap == 0,
        building_overlap=building_overlap,
        overlap=overlap,
        overlaps_with=overlaps_with,
        clipped=clipped,
        clipped_fraction=clipped_fraction,
        within_single=not viol["overSingle"],
        within_cumulative=not viol["overCumulative"],
        effect=effect,
        effect_note=effect_note,
    )
    size = _size_block(grids, geometry, covered)

    if accepted:
        warning = _accepted_warning(coordinate_based, clipped, occ is not None,
                                    effect, effect_note, edited_depth)
    else:
        warning = _reject_warning(
            reason, why=why, building_overlap=building_overlap, overlap=overlap,
            overlaps_with=overlaps_with, viol=viol,
            clipped_fraction=clipped_fraction,
        )

    return {
        "status": status,
        "warning": warning,
        "reasonCode": reason,
        "constraints": constraints,
        "size": size,
        "geometry": geometry if accepted else None,
    }


# --------------------------------------------------------------------------- #
# validate_batch -- the per-batch claimed-set loop (D.2)
# --------------------------------------------------------------------------- #


def _proposal_from(candidate: Mapping[str, Any], result: Mapping[str, Any],
                   kind: str,
                   answers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The accepted candidate as a proposal dict the wire schema can hold.

    The questions are generated here, immediately beside
    ``parameters_for_kind``, so a question exists for **exactly the parameters
    that were published** and the two cannot drift apart (R9). A planner's
    answered depth has already been written into ``params`` by
    :func:`apply_answers` (which the caller runs before validation), so the
    parameter block, the warning and the served value all read one number.

    ``answers`` is carried only so an *answered* question reports itself as
    ``ANSWERED`` with the server's parse; it never changes the geometry or the
    parameters here, because the value has already landed in ``params``.
    """
    params = dict(candidate.get("params") or {})
    site_id = _site_id_of(candidate)
    if site_id is not None:
        params.setdefault("siteId", site_id)
    edited = override_depth(kind, candidate)
    return {
        "geometry": result["geometry"],
        "intervention_type": kind,
        "source": candidate.get("source", "ai"),
        "params": params,
        "style": candidate.get("style", {}),
        "site_id": site_id,
        "rationale": candidate.get("rationale") or params.get("rationale"),
        "basis": candidate.get("basis") or params.get("basis"),
        "size": result["size"],
        "requested": _requested_of(candidate),
        "constraints": result["constraints"],
        "parameters": parameters_for_kind(
            kind, edited[0] if edited is not None else None),
        "questions": questions_for_kind(
            kind, {**params, "requested": _requested_of(candidate)},
            answers=answers),
        # The proposal honesty label (UPPER_SNAKE), distinct from the stored
        # feature's draft/validated status.
        "status": result["status"],
        "warning": result["warning"],
    }


def _rejection_from(candidate: Mapping[str, Any], result: Mapping[str, Any],
                    kind: str) -> dict[str, Any]:
    """A refused candidate as a ``ProposalRejection`` dict (§2.2)."""
    constraints = result["constraints"]
    return {
        "interventionType": kind,
        "siteId": _site_id_of(candidate),
        "requested": _requested_of(candidate),
        "reasonCode": result["reasonCode"],
        "buildingOverlapCells": constraints["buildingOverlapCells"],
        "overlapCells": constraints["overlapCells"],
        "status": REJECT_STATUS,
        "warning": result["warning"],
    }


def validate_batch(grids: Any, candidates: Iterable[Any], occupied: Any,
                   claimed: Any,
                   answers: Mapping[str, Any] | None = None,
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a batch in provider order; each accepted candidate claims its cells.

    Owns the per-batch ``claimed |= cells`` rule (§2.1): once a candidate is
    accepted, its served cells join the claimed set with the label
    ``proposal#<index>`` (the candidate's position in ``candidates``), so a later
    candidate that overlaps it is refused with ``OVERLAPS_STAGED`` naming
    ``proposal#<index>``.

    Returns ``(accepted, rejected)``: ``accepted`` are proposal dicts (served
    geometry + size + constraints + parameters + questions + status + warning)
    and ``rejected`` are ``ProposalRejection``-shaped dicts. Provider order is
    preserved; nothing is re-ranked.

    ``answers`` (additive, defaulted) maps question id -> the planner's typed
    answer. It is applied to each candidate **before** the predicates run, so a
    planner's answered depth is the depth the geometry is judged against -- a
    proposal cannot be validated at one depth and served at another.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    owner_map = _as_owner_map(claimed)

    for index, raw in enumerate(candidates):
        candidate = apply_answers(_kind_of(raw), _as_mapping(raw), answers)
        kind = _kind_of(candidate)
        result = validate_proposal(grids, candidate, occupied, owner_map)
        if result["status"] == CONSTRAINT_STATUS and result["geometry"]:
            served = _polygon_cells(grids, result["geometry"]["coordinates"])
            label = "proposal#%d" % index
            for cell in served:
                owner_map.setdefault((int(cell[0]), int(cell[1])), label)
            accepted.append(_proposal_from(candidate, result, kind, answers))
        else:
            rejected.append(_rejection_from(candidate, result, kind))

    return accepted, rejected
