"""Candidate sites for interventions, from the study area's own rasters.

One definition of "where a project could go", shared by the things that need
it: ``app/services/suggest.py``, which ranks the candidate sites for the
intervention loop's suggest step, and ``app/services/proposal_constraints.py``,
which uses the size floor and the building exclusion.

A fixed catalog generated from these sites existed until 16 Oct 2026, when it
was retired once two declared projects (MTR station, HSK Park) made derived
placeholder parcels unnecessary. The siting rule it used is unchanged and is
what the suggester still applies.

If two callers had their own siting rules they would drift, and the first
symptom would be a suggestion that cannot be reproduced run to run.

## What a site is

A **true cell mask** and the evidence for it: never a bounding box. The
axis-aligned box of an irregular basin claims the empty corners as intervention
area, which overstates the site and trips the ledger's coverage guard on the
wrong evidence. Everything here produces geometry through
``app/services/intervention_geometry.py``, so the polygon is the mask.

## What a site is not

Not a ranked recommendation, and not a score. These are *candidates*: the
ranking is the suggester's job (per axis, with the arithmetic shown), and
whether any of them is a good idea is a run's job. This module answers "where
could it go and why", nothing else.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# A "component" smaller than this is noise rather than a project: 40 cells is
# 1,000 m2, roughly one small playground.
MIN_SITE_CELLS = 40

# The deep-basin threshold for a reservoir candidate. 1.0 m of computed depth is
# where a level-pool basin stops being a puddle: at 450 mm the study area's
# basin is 5,163 cells as a whole (29.8% of the site, which the ledger's 25%
# single-intervention cap refuses as a land-use change) and 2,469 cells at 1 m
# or more. The threshold is stated here so it can be argued with.
RESERVOIR_MIN_DEPTH_M = 1.0

# The halo, in 5 m cells, within which hard standing counts as "next to" a
# vegetated block. Three cells is 15 m: the width of a planted verge.
GREEN_HALO_CELLS = 3

# Working depth for an underground tank candidate. Assumption, not a
# measurement: see ``scripts/_overlay_common.STORAGE_DEPTH_M``.
TANK_DEPTH_M = 2.00

# Ponding depth for a floodable-ground candidate (a designed ponding depth for
# a typical floodable open space). Same source, same caveat.
FLOODABLE_DEPTH_M = 0.30

# Curve number for permeable paving: an assumed mid value for permeable
# pavement on an HSG D soil, not a TR-55 lookup. Same value the ledger applies.
PERMEABLE_CN = 72.0


def _overlay():
    """The 5 m working grid, loaded once.

    Imported lazily and cached so that importing this module never pulls the
    rasters in: the tests that only touch geometry should not need them, and a
    machine where the scripts have not been run should still import cleanly.
    """
    from _overlay_common import load_overlay

    return load_overlay()


def _halo(mask: np.ndarray, cells: int) -> np.ndarray:
    """Grow ``mask`` by ``cells`` in every direction (Chebyshev dilation).

    ``np.roll`` shifts plus a border restore: the radius is small and constant,
    so the obvious implementation is also the fast one, and there is no reason
    to import scipy for one morphological step.
    """
    out = mask.copy()
    for dr in range(-cells, cells + 1):
        for dc in range(-cells, cells + 1):
            out |= np.roll(np.roll(mask, dr, axis=0), dc, axis=1)
    out[:cells, :] |= mask[:cells, :]
    out[-cells:, :] |= mask[-cells:, :]
    out[:, :cells] |= mask[:, :cells]
    out[:, -cells:] |= mask[:, -cells:]
    return out


def _mask_of(cells: list[tuple[int, int]], shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape, dtype=bool)
    for r, c in cells:
        out[r, c] = True
    return out


def _sites(mask: np.ndarray, *, kind: str, why: str,
           min_size: int = MIN_SITE_CELLS) -> list[dict[str, Any]]:
    """4-connected components of ``mask`` as candidate sites, largest first.

    Four-connectivity, not eight: an 8-connected basin whose parts meet only
    diagonally traces as several small contours (the saddle case isolates the
    corners), so the largest *part* of a diagonal-only basin is a fraction of
    the basin. Four is also the conservative reading of "contiguous": diagonal
    contact is not a continuous water surface.
    """
    from app.services.intervention_geometry import (
        connected_components,
        contour_ring,
        polygon_area_ha,
        ring_centroid,
    )

    ov, _buildings, _depth = _rasters()
    out: list[dict[str, Any]] = []
    for component in connected_components(mask, min_size, connectivity=4):
        contour = contour_ring(component["cells"], bounds=ov.bounds,
                               rows=ov.rows, cols=ov.cols)
        if contour is None:  # pragma: no cover - components are non-empty
            continue
        geometry, enclosed, _of = contour
        out.append({
            "kind": kind,
            "geometry": geometry,
            "centroid": ring_centroid(geometry["coordinates"][0]),
            "areaHa": polygon_area_ha(geometry["coordinates"],
                                      ov.bounds[1], ov.bounds[3]),
            "cells": component["cells"],
            "coveredCellCount": enclosed,
            "evidence": why,
        })
    out.sort(key=lambda s: -len(s["cells"]))
    return out


@lru_cache(maxsize=1)
def _rasters() -> tuple[Any, Any, Any]:
    ov = _overlay()
    # The *gate's* building authority, not this module's own loose envelope.
    #
    # `building_footprint_mask` rasterises each footprint by whether a cell
    # centre falls inside it; the gate additionally unions a polygon-exact mask,
    # and the two differ by a couple of dozen cells in how a footprint edge
    # rasterises (7,789 vs 7,812 on this data). A pool filtered with the loose
    # mask can therefore offer a site the gate will then clip, so the site a
    # planner is shown is not the site that gets served -- and every cell of the
    # difference is a cell the two authorities disagree about.
    #
    # Measured before the swap: the largest basin falls 1,878 -> 1,876 cells and
    # the largest paving site 3,596 -> 3,580, both far inside the caps, and the
    # smallest site (83 cells) is unchanged and well above the 40-cell floor.
    # So no site vanishes and none crosses a cap; the pools simply stop
    # disagreeing with the gate.
    try:
        from app.services.proposal_constraints import building_mask

        buildings = building_mask(ov)
    except Exception:  # noqa: BLE001 - a missing city model is not an error here
        buildings = None
    if buildings is None:
        from _overlay_common import building_footprint_mask

        buildings = building_footprint_mask(ov)
    from _overlay_common import LAND_CLASSES

    depth_path = settings.rasters_dir / "wetgrid_hsk.npy"
    depth = None
    if depth_path.exists():
        depth = np.load(depth_path).astype(np.float64)
        if depth.shape != (ov.rows, ov.cols):
            depth = None
    return ov, buildings, depth


def _class_mask(name: str) -> np.ndarray:
    ov, _buildings, _depth = _rasters()
    from _overlay_common import LAND_CLASSES

    return ov.land_class == LAND_CLASSES.index(name)


def hard_standing() -> np.ndarray:
    """Paved or bare ground, surveyed building footprints removed.

    Buildings are removed because an intervention *on* a building is not a
    site: a permeable plaza is not the roof of the school standing on it. The
    footprints are the surveyed 396 (Open3dhk fused with Overture), so the
    exclusion is measured rather than assumed.
    """
    ov, buildings, _ = _rasters()
    mask = _class_mask("paved") | _class_mask("bare")
    if buildings is not None:
        mask = mask & ~buildings
    return mask


def vegetated() -> np.ndarray:
    """Green ground, as the land class reports it."""
    return _class_mask("green")


def basin_depth() -> np.ndarray | None:
    """The 450 mm level-pool depth field, or None when it has not been run."""
    _ov, _buildings, depth = _rasters()
    return depth


def reservoir_candidates() -> list[dict[str, Any]]:
    """Deep sub-basins of the computed wet grid.

    Deliberately the *deep* part, not the whole basin: the whole basin is 29.8%
    of this 741 m x 587 m study area and the ledger's 25% single-intervention
    cap refuses it as a land-use change. A reservoir belongs where the water
    already pools anyway.
    """
    depth = basin_depth()
    if depth is None:
        return []
    return _sites(depth >= RESERVOIR_MIN_DEPTH_M, kind="water_reservoir",
                  why=f"connected sub-basin of the 450 mm level-pool wet grid "
                      f"at {RESERVOIR_MIN_DEPTH_M} m depth or more")


def green_candidates() -> list[dict[str, Any]]:
    """Hard standing within the halo of the largest vegetated block.

    Greening already-green ground changes nothing, so a green candidate must be
    somewhere that is currently hard. The halo rule is stated, not derived: it
    is the planted-verge width around the study area's biggest park, and it can
    be argued with by changing ``GREEN_HALO_CELLS``.
    """
    green = vegetated()
    from app.services.intervention_geometry import connected_components

    blocks = connected_components(green, MIN_SITE_CELLS, connectivity=4)
    if not blocks:
        return []
    biggest = _mask_of(blocks[0]["cells"], green.shape)
    target = _halo(biggest, GREEN_HALO_CELLS) & ~biggest & hard_standing()
    return _sites(target, kind="green_space",
                  why=f"paved or bare ground within {GREEN_HALO_CELLS} cells "
                      f"({GREEN_HALO_CELLS * 5} m) of the study area's largest "
                      f"vegetated block, buildings excluded")


def paving_candidates() -> list[dict[str, Any]]:
    """Contiguous impervious blocks, buildings removed.

    A permeable-paving candidate is the *paved* extent, because that is what
    would be re-laid; the ledger lowers its curve number and changes nothing
    else, which is why this candidate pool and the tank pool below describe the
    same ground for two different services.
    """
    return _sites(hard_standing() & _class_mask("paved"),
                  kind="permeable_paving",
                  why="contiguous impervious block, surveyed building "
                      "footprints excluded")


def tank_candidates() -> list[dict[str, Any]]:
    """The same impervious ground, as a place to bury a tank.

    Same parcel as the paving candidate, deliberately: a district that wants
    drainage service on this ground chooses one or the other, and the loop
    should show the choice rather than hide it.
    """
    return _sites(hard_standing() & _class_mask("paved"),
                  kind="underground_tank",
                  why="contiguous impervious block, surveyed building "
                      "footprints excluded")


def park_candidates() -> list[dict[str, Any]]:
    """Vegetated blocks, as ground that could be re-graded to pond.

    A floodable park is landscaped ground that ponds: the heat value does not
    move (the ``floodable`` class carries the vegetated value, not the water
    one), so this candidate is a drainage service that costs no cooling.
    """
    return _sites(vegetated(), kind="flood_storage",
                  why="contiguous vegetated block, convertible to floodable "
                      "ground")


def _stamp() -> tuple[float, ...]:
    """The mtimes of the rasters the pools are built from.

    Contouring a 6,800-cell impervious block into a 500-vertex polygon costs
    real seconds, and the rasters do not change while the server runs. Caching
    on the mtimes is the same pattern the run service uses for its conditioned
    grid: a regenerated raster has a new mtime, so the cache is keyed on the
    thing that actually makes a pool stale.
    """
    paths = (
        settings.data_dir / "landclass_hsk.npy",
        settings.rasters_dir / "wetgrid_hsk.npy",
        BACKEND_DIR / "data" / "rasters" / "buildings_hsk.json",
    )
    return tuple(p.stat().st_mtime if p.exists() else -1.0 for p in paths)


@lru_cache(maxsize=4)
def _all_candidates(_stamp: tuple[float, ...]) -> dict[str, list[dict[str, Any]]]:
    return _build_all_candidates()


def all_candidates() -> dict[str, list[dict[str, Any]]]:
    """Every candidate pool, keyed by intervention kind.

    A pool that cannot be built (no depth raster yet, say) is an empty list, not
    an error: the suggester must be able to say "no reservoir candidate on this
    machine" without taking the whole endpoint down.
    """
    return _all_candidates(_stamp())


def _build_all_candidates() -> dict[str, list[dict[str, Any]]]:
    # The paving and tank pools describe the same parcel for two different
    # services, so the contour is computed once and the kind is attached
    # afterwards. Two identical 500-vertex polygons built twice would be two
    # chances to disagree about where the parcel is.
    paved_sites = _sites(hard_standing() & _class_mask("paved"),
                         kind="permeable_paving",
                         why="contiguous impervious block, surveyed building "
                             "footprints excluded")
    out: dict[str, list[dict[str, Any]]] = {
        "permeable_paving": [dict(s) for s in paved_sites],
        "underground_tank": [dict(s, kind="underground_tank") for s in paved_sites],
    }
    for name, builder in (
        ("water_reservoir", reservoir_candidates),
        ("green_space", green_candidates),
        ("flood_storage", park_candidates),
    ):
        try:
            out[name] = builder()
        except Exception:  # noqa: BLE001 - a pool that cannot be built is empty
            out[name] = []
    return out
