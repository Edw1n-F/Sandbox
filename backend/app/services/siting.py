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

# The reservoir rule is the *preventive* screening form of flood risk, and it
# answers a sharper question than "which cells drain a lot of water". Under pure
# D8 the *total* accumulation is monotonically non-decreasing down every flow
# path — a cell's accumulation is simply the sum of its upstream cells, so the
# biggest values always sit at the bottom of the largest basins (the channel
# mouth, the boundary outlet). Those are the places the water already *leaves*,
# the opposite of where a detention basin helps.
#
# The right place to catch water is the *local* peak of risk along a path: the
# ground where a lot of water arrives while the terrain's own ability to pass it
# on is weakest. The score is the ratio the design brief's risk model suggests,
#
#     R = inflow / (S + epsilon)
#
# ``inflow`` is the water arriving at a place (the direct upstream runoff, CN
# weighted); ``S`` is the raw-DEM steepest-descent slope — the "conveyance".
# The numerator and the denominator are of *different geometric kinds*, and that
# is load-bearing. The slope is an *areal* signal: a gentle-slope region is a
# contiguous patch (the flat bed where a convergence sits). The inflow is a
# *point* signal: it spikes at the one cell where a branch meets the flat, and
# falls back to a single branch on either side. Intersecting the two cell-by-cell
# (``(slope < S_thr) & (inflow > I_thr)``) shatters the gentle-slope patch into
# hundreds of one-cell fragments that never clear the site floor. So the pool is
# built in two stages, not one:
#
#   1. the *candidate faces* are the contiguous gentle-slope patches
#      (``S < RESERVOIR_SLOPE_MAX``), which is what a detention basin is actually
#      sited on;
#   2. each face is *ranked* by the inflow it receives (the summed direct inflow
#      over its cells), so the flat patches that actually sit under a convergence
#      beat the flat patches that merely drain one branch.
#
# The slope is taken from the *raw* DEM, not the Priority-Flood-filled surface:
# the filler flattens every filled pit to its epsilon (1e-5) slope, so a
# filled-surface slope is a restatement of the fill amount and the two would
# multiply the same pit twice. The raw slope is the independent conveyance term,
# and it is what the real flooding agrees with: the cells that actually pond in
# the simulation (wet depth >= 0.3 m) have a median raw slope of 0.022 against a
# grid median of 0.097, and fill == 0 on 99.7 % of them.
#
# RESERVOIR_SLOPE_MAX: the raw-slope ceiling of a candidate face, in m/m. 0.03
# sits just above the median slope of the cells that really pond (0.022), so it
# covers the genuine flat beds while keeping the pool to a handful of patches.
RESERVOIR_SLOPE_MAX = 0.03

# RESERVOIR_MIN_INFLOW_M3: the minimum summed direct inflow a *face* must
# receive to be a real convergence rather than a flat that merely drains one
# branch. It is applied to the whole patch (not per cell), so it ranks faces
# without shattering them.
RESERVOIR_MIN_INFLOW_M3 = 300.0

# RESERVOIR_CHANNEL_QUANTILE: the *total* weighted-accumulation quantile above
# which a cell is "the channel" — the ultimate conveyor of water to the
# boundary. The reservoir must NOT be sited here: a channel is where the water
# already goes, so a basin there would block the very drain it is meant to
# protect. This is kept as a hard exclusion on the *monotonic* accumulation
# field, which is what actually delineates the channel; the risk ratio alone
# cannot, because it is local.
RESERVOIR_CHANNEL_QUANTILE = 0.99

# RESERVOIR_MIN_SITE_CELLS: the component floor for a reservoir candidate. A
# detention basin is a point/linear facility, not a big areal land-use change,
# so it is allowed to be smaller than the 40-cell (1,000 m2) floor the areal
# pools (paving, greening) use. 20 cells is 500 m2 — a half-football-pitch
# street-scale detention basin — which is a realistic lower bound for the
# neighbourhood-scale basins this 0.43 km2 site would actually host.
RESERVOIR_MIN_SITE_CELLS = 20

# Greening a cell only helps if it is currently hot (hard) AND the surrounding
# block is dense enough to be a heat island. GREEN_BLOCK_RADIUS_CELLS is the
# radius (in cells) over which building density is measured; a block is "dense"
# when at least GREEN_MIN_DENSE_FRACTION of that window is built on. The median
# building density on this study area is ~0.45, so the threshold is set above it
# to isolate the densest, hottest core rather than the whole city (85% of which
# is paved).
GREEN_BLOCK_RADIUS_CELLS = 5
GREEN_MIN_DENSE_FRACTION = 0.55

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

    ov, _buildings, _depth, _accum, _fill = _rasters()
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
def _rasters() -> tuple[Any, Any, Any, Any, Any]:
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

    # D8 flow accumulation (cells draining through each cell), written by the
    # offline conditioning pass. Loads lazily and tolerates absence exactly like
    # the depth raster: a pool that needs it returns empty rather than erroring.
    accum_path = settings.rasters_dir / "flow_accum_hsk.npy"
    accumulation = None
    if accum_path.exists():
        accumulation = np.load(accum_path).astype(np.float64)
        if accumulation.shape != (ov.rows, ov.cols):
            accumulation = None

    # Priority-Flood fill amount (filled - raw): how far the raw DEM had to be
    # raised so the cell gained a downhill path. This is the "poor drainage"
    # signal — a genuine depression, not a proxy like absolute elevation. Derived
    # from the two conditioning-pass rasters so it tolerates absence like the
    # accumulation field above.
    fill_path = settings.rasters_dir / "flow_filled_hsk.npy"
    raw_path = settings.rasters_dir / "flow_raw_elev_hsk.npy"
    fill = None
    if fill_path.exists() and raw_path.exists():
        filled = np.load(fill_path).astype(np.float64)
        raw = np.load(raw_path).astype(np.float64)
        if filled.shape == (ov.rows, ov.cols) and raw.shape == (ov.rows, ov.cols):
            fill = filled - raw
    return ov, buildings, depth, accumulation, fill


def _class_mask(name: str) -> np.ndarray:
    ov, _buildings, _depth, _accum, _fill = _rasters()
    from _overlay_common import LAND_CLASSES

    return ov.land_class == LAND_CLASSES.index(name)


def hard_standing() -> np.ndarray:
    """Paved or bare ground, surveyed building footprints removed.

    Buildings are removed because an intervention *on* a building is not a
    site: a permeable plaza is not the roof of the school standing on it. The
    footprints are the surveyed 396 (Open3dhk fused with Overture), so the
    exclusion is measured rather than assumed.
    """
    ov, buildings, _depth, _accum, _fill = _rasters()
    mask = _class_mask("paved") | _class_mask("bare")
    if buildings is not None:
        mask = mask & ~buildings
    return mask


def vegetated() -> np.ndarray:
    """Green ground, as the land class reports it."""
    return _class_mask("green")


def basin_depth() -> np.ndarray | None:
    """The 450 mm level-pool depth field, or None when it has not been run."""
    _ov, _buildings, depth, _accum, _fill = _rasters()
    return depth


def flow_accumulation() -> np.ndarray | None:
    """D8 flow accumulation (cells draining through each cell), or None when the
    conditioning pass has not been run."""
    _ov, _buildings, _depth, accumulation, _fill = _rasters()
    return accumulation


def waterway_mask() -> np.ndarray | None:
    """The surveyed watercourse (Tin Shui Wai Nullah) as a boolean mask.

    The channel exclusion. It is measured geometry, not a derived proxy: the
    study area is crossed by the Tin Shui Wai Nullah, an open drainage nullah
    (OSM ``waterway=drain``), rasterised by ``scripts/make_waterway_mask.py``.
    Where this exists it is the right authority for "this is the watercourse",
    because the D8 flow-accumulation top quantile only catches the downstream
    reach where accumulation actually peaks — the nullah is NOT uniformly
    deep-blue in the accumulation field, so the quantile cut misses most of it.
    """
    _ov, _buildings, _depth, _accum, _fill = _rasters()
    path = settings.rasters_dir / "waterway_hsk.npy"
    if not path.exists():
        return None
    mask = np.load(path).astype(bool)
    if mask.shape != (_ov.rows, _ov.cols):
        return None
    return mask


def boundary_outlets() -> np.ndarray | None:
    """The grid-edge cells water actually drains out of, as a boolean mask.

    A D8 ``receiver == -1`` marks an outlet, but that is not the same as "the
    boundary". After flat resolution an *interior* flat cell with no downhill
    neighbour is also an outlet (362 of them here) even though it does not sit
    on the edge — its water does not leave the scene, it pools there. The true
    discharge points are the ``receiver == -1`` cells on the outermost ring of
    the grid (82 here): every drop that leaves the study area crosses one of
    these.

    These are the *opposite* of flood risk — they are where the drainage
    succeeds — so a reservoir candidate must never be sited on them. A basin on
    the discharge ring would sit on the exit the water already has, blocking the
    relief it is meant to provide, not adding any.
    """
    _ov, _buildings, _depth, _accum, _fill = _rasters()
    from hydrology import condition

    ov = _overlay()
    receiver = condition(ov.elevation)["receiver"]
    outlet = receiver == -1
    rows, cols = ov.rows, ov.cols
    # Keep only the outlets on the outermost ring.
    on_edge = np.zeros_like(outlet)
    on_edge[0, :] = True
    on_edge[rows - 1, :] = True
    on_edge[:, 0] = True
    on_edge[:, cols - 1] = True
    return outlet & on_edge


def _raw_slope() -> np.ndarray | None:
    """Steepest-descent slope of the *raw* DEM, per cell, as a fraction (m/m).

    This is the independent "how fast can this cell shed water" signal. It is
    computed from the raw DEM, not the Priority-Flood-filled surface, because
    the filled surface flattens every filled pit to the epsilon slope (1e-5 m/m)
    — a filled-surface slope is then a restatement of the fill amount, and using
    both would multiply the same depression twice. The raw slope is the honest
    terrain-conveyance term.

    The slope is the maximum ``(elev - neighbour) / distance`` over the eight
    neighbours, the same steepest-descent rule D8 uses (so a cell's slope is its
    downhill drop toward its own D8 receiver). ``distance`` is cell-count based:
    1 for orthogonal, sqrt(2) for diagonal. Outlets (no downhill neighbour) get
    0, which the risk ratio's epsilon guards against.
    """
    _ov, _buildings, _depth, _accum, _fill = _rasters()
    ov = _overlay()
    raw_path = settings.rasters_dir / "flow_raw_elev_hsk.npy"
    if not raw_path.exists():
        return None
    raw = np.load(raw_path).astype(np.float64)
    rows, cols = ov.rows, ov.cols
    if raw.shape != (rows, cols):
        return None
    slope = np.zeros((rows, cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols):
            best = 0.0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < rows and 0 <= nc < cols):
                        continue
                    dist = 2.0 ** 0.5 if (dr and dc) else 1.0
                    s = (raw[r, c] - raw[nr, nc]) / dist
                    if s > best:
                        best = s
            slope[r, c] = best
    return slope


def _direct_inflow() -> np.ndarray | None:
    """The *direct* upstream inflow each cell receives, in m3.

    Each upstream cell sheds its SCS-CN ``unstored_m3`` into its D8 receiver;
    ``_direct_inflow[c]`` is the sum of that runoff over ``c``'s immediate
    upstream neighbours. Unlike the total D8 accumulation this is NOT monotonic
    down a path: it spikes where several branches converge and falls back to a
    single branch between junctions, so it is the local "water collects here"
    signal the risk ratio needs as its numerator.

    The weighting is by each cell's *runoff volume* (CN-weighted), not a bare
    count, so a paved roof contributes more than a lawn of equal area. On this
    85 %-paved grid the two nearly coincide, but the weighted field is the
    physically correct one and does not go stale when land cover changes.
    """
    _ov, _buildings, _depth, _accum, _fill = _rasters()
    ov = _overlay()
    from hydrology import condition

    receiver = condition(ov.elevation)["receiver"].ravel()
    from app.services.sandbox_model import _runoff_and_unstored

    _runoff_m, unstored_m = _runoff_and_unstored(ov.land_class, 450.0)
    cell_area = ov.cell_m * ov.cell_m
    unstored_m3 = (unstored_m * cell_area).ravel()
    inflow = np.zeros_like(unstored_m3)
    valid = receiver >= 0
    np.add.at(inflow, receiver[valid], unstored_m3[valid])
    return inflow.reshape(ov.rows, ov.cols)


def reservoir_candidates() -> list[dict[str, Any]]:
    """Gentle-slope patches that sit under a convergence: the preventive risk.

    A reservoir (detention basin) exists to catch excess inflow before it
    overwhelms the downstream, so its site is where flood risk *peaks locally*
    along a drainage path — the ground where a lot of water arrives while the
    terrain's own ability to pass it on is weakest.

    The risk model is ``R = inflow / slope``, but the numerator and denominator
    are of different geometric kinds, so they are applied in two stages rather
    than intersected per cell (which shatters the patches into one-cell
    fragments — see the module comment):

    1. the **faces** are the contiguous patches of raw slope under
       ``RESERVOIR_SLOPE_MAX`` (the "conveyance is weak here" areal signal);
    2. each face is kept only if its summed direct inflow clears
       ``RESERVOIR_MIN_INFLOW_M3`` — i.e. it actually sits under a convergence,
       not merely draining a single branch — and the faces are returned in
       descending ``totalInflowM3`` so the flat bed under the most water leads.

    The slope is deliberately *not* divided by when ranking: it has already done
    its filtering work as the admission gate, so re-amplifying it as a ranking
    denominator would only let a face's few steeper edge cells (which the
    4-connected labelling pulls in) skew the ordering. Inflow is the direct,
    stable source of flood risk, so it is the ranking key.

    The channel (the monotonic total-accumulation top
    ``RESERVOIR_CHANNEL_QUANTILE``) and the boundary outlets are excluded: both
    are where the water already drains away, the inverse of flood risk. Surveyed
    building footprints are excluded for the same reason as every other pool.

    The old rule (``depth >= 1.0 m`` static ponding) sited reservoirs on ground
    that *already* floods; the fill-intersection rule that followed sited them
    on a building-density proxy (fill >= 0.1 m cells are 87 % paved). This rule
    sites them where flooding would first *appear* — the flat bed under a
    convergence that the interception is actually useful at.
    """
    slope = _raw_slope()
    inflow = _direct_inflow()
    if slope is None or inflow is None:
        return []
    _ov, buildings, _depth, accum, _fill = _rasters()
    if buildings is None:
        return []

    # The areal signal: gentle-slope ground, cleared of everything that is not a
    # site (channel, outlet, building). This is the face a basin sits on.
    mask = slope < RESERVOIR_SLOPE_MAX
    # The surveyed watercourse is the channel exclusion. It is preferred over the
    # accumulation-quantile cut (kept as a fallback below) because the nullah is
    # measured geometry and is NOT uniformly deep-blue in the accumulation field,
    # so the quantile only catches its downstream reach.
    waterway = waterway_mask()
    if waterway is not None:
        mask = mask & ~waterway
    else:
        channel = None
        if accum is not None:
            channel_thr = float(np.quantile(accum, RESERVOIR_CHANNEL_QUANTILE))
            channel = accum >= channel_thr
            mask = mask & ~channel
    outlets = boundary_outlets()
    if outlets is not None:
        mask = mask & ~outlets
    mask = mask & ~buildings

    from app.services.intervention_geometry import (
        connected_components,
        contour_ring,
        polygon_area_ha,
        ring_centroid,
    )

    ov = _overlay()
    cols = ov.cols
    out: list[dict[str, Any]] = []
    for component in connected_components(mask, RESERVOIR_MIN_SITE_CELLS,
                                          connectivity=4):
        cells = np.asarray(component["cells"], dtype=np.int64)
        rs, cs = cells[:, 0], cells[:, 1]
        total_inflow = float(inflow[rs, cs].sum())
        if total_inflow < RESERVOIR_MIN_INFLOW_M3:
            continue
        contour = contour_ring(component["cells"], bounds=ov.bounds,
                               rows=ov.rows, cols=cols)
        if contour is None:  # pragma: no cover - components are non-empty
            continue
        geometry, enclosed, _of = contour
        out.append({
            "kind": "water_reservoir",
            "geometry": geometry,
            "centroid": ring_centroid(geometry["coordinates"][0]),
            "areaHa": polygon_area_ha(geometry["coordinates"],
                                      ov.bounds[1], ov.bounds[3]),
            "cells": component["cells"],
            "coveredCellCount": enclosed,
            # The flood-risk score's own part, published so the suggester can
            # rank the faces by inflow without re-deriving it. The slope was
            # already the admission gate (this function only keeps patches under
            # RESERVOIR_SLOPE_MAX), so it is not divided by again — the inflow
            # alone is the direct source of risk. totalInflowM3 is the summed
            # direct upstream runoff (the inflow term); see the module comment
            # for why the two signals are applied in stages, not multiplied.
            "totalInflowM3": total_inflow,
            "evidence": (f"gentle-slope bed (raw slope < {RESERVOIR_SLOPE_MAX}) "
                         f"receiving {total_inflow:.0f} m3 of direct inflow"),
        })
    out.sort(key=lambda s: -s["totalInflowM3"])
    return out


def _building_density(buildings: np.ndarray) -> np.ndarray:
    """Fraction of built-on cells within a radius window, per cell.

    A dense block is a heat island: paving it greener yields more cooling than
    greening an isolated hard patch. The density is a box-windowed mean of the
    building mask, computed with an integral image so it is O(rows*cols) rather
    than O(rows*cols*radius^2). The window is clipped at the grid edge, so an
    edge cell divides by its actual (smaller) window size rather than by the
    full window.
    """
    b = buildings.astype(np.float64)
    rows, cols = b.shape
    ii = np.zeros((rows + 1, cols + 1), dtype=np.float64)
    ii[1:, 1:] = b.cumsum(axis=0).cumsum(axis=1)
    radius = GREEN_BLOCK_RADIUS_CELLS
    density = np.empty_like(b)
    for r in range(rows):
        for c in range(cols):
            r0 = max(0, r - radius)
            r1 = min(rows, r + radius + 1)
            c0 = max(0, c - radius)
            c1 = min(cols, c + radius + 1)
            total = ii[r1, c1] - ii[r0, c1] - ii[r1, c0] + ii[r0, c0]
            density[r, c] = total / ((r1 - r0) * (c1 - c0))
    return density


def green_candidates() -> list[dict[str, Any]]:
    """Hot hard ground inside dense built blocks: the urban-heat-island core.

    A green candidate's value is cooling, so it is a cell that holds heat
    (``paved``/``bare``) inside a block dense enough to be a heat island (the
    building density over a local window reaches ``GREEN_MIN_DENSE_FRACTION``).
    Greening an isolated hard patch in a park-lined suburb cools little; greening
    the dense core cools the most. The old rule (hard standing in the halo of the
    largest vegetated block) dropped candidates into the river bed once the river
    was mis-classed as ``green`` by WorldCover, and was blind to heat entirely.
    """
    ov, buildings, _depth, _accum, _fill = _rasters()
    mask = _class_mask("paved") | _class_mask("bare")
    if buildings is not None:
        mask = mask & ~buildings
        mask = mask & (_building_density(buildings) >= GREEN_MIN_DENSE_FRACTION)
    return _sites(mask, kind="green_space",
                  why=f"paved or bare ground in dense built blocks (building "
                      f"density >= {GREEN_MIN_DENSE_FRACTION:.0%} over a "
                      f"{2 * GREEN_BLOCK_RADIUS_CELLS + 1}-cell window), "
                      f"surveyed building footprints excluded")


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
        settings.rasters_dir / "flow_accum_hsk.npy",
        settings.rasters_dir / "flow_raw_elev_hsk.npy",
        settings.rasters_dir / "flow_filled_hsk.npy",
        settings.rasters_dir / "flow_receiver_hsk.npy",
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
