#!/usr/bin/env python
"""Wet-grid overlay (D2): CN runoff -> storage -> fill from the lowest cells up.

    python scripts/make_wetgrid.py --rainfall 450

Pipeline, in the order the baseline storage ledger requires:

1. **Generation.** SCS-CN runoff depth per cell from the rainfall depth and the
   class Curve Number (hydrologic soil group D).
2. **Retention.** Subtract what the standing water and wetland already hold,
   plus a depression allowance on the non-retention cells. This is the ADR-015
   partition: land cover sets how much *runs off*, explicit storage sets how much
   can be *held*, and no cell is counted in both.
3. **Allocation.** Distribute the leftover volume across cells starting from the
   lowest ground, giving each cell a depth until the volume is exhausted.

The result is a depth field, emitted as an RGBA PNG with a metadata sidecar
carrying every constant, so the number can be defended or dismissed later.

**The surface is synthetic** (see `_overlay_common`). This is a screening view of
where water would collect given a plausible topography -- it is not a flood
prediction and must not be presented as one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from hydrology import condition as condition_dem, fill_and_spill  # noqa: E402
from _overlay_common import (  # noqa: E402
    attach_to_scene,
    COUNTED_AS,
    CN_SOURCE,
    CURVE_NUMBER_HSG_D,
    DEPRESSION_STORAGE_M,
    LAND_CLASSES,
    STORAGE_DEPTH_M,
    load_overlay,
    runoff_depth,
)

# STORAGE_DEPTH_M and DEPRESSION_STORAGE_M used to be defined here. They moved to
# _overlay_common on 9 Oct 2026 so the API could re-run the identical retention
# arithmetic when scoring a candidate intervention; there is now one definition.

# Ramp for the PNG: transparent below this depth, opaque at MAX_VIS_DEPTH_M.
#
# MIN_VIS_DEPTH_M is set to DEPRESSION_STORAGE_M (0.02 m) on purpose: that is the
# routine on-site ponding every rainstorm produces before water runs off. Painting
# cells below that floor made the raster read as "the whole site floods" at any
# moderate rainfall, which is misleading — the model is not a flood prediction and
# blue is ponding depth, not inundation. Only water in excess of the routine
# depression storage is shown, so the picture answers "where does water actually
# accumulate" rather than "everything is wet".
MIN_VIS_DEPTH_M = 0.02
MAX_VIS_DEPTH_M = 0.30

# The flood-extent threshold: a cell with at least this much standing water after
# D8 routing is classed as "flooded". 0.30 m is a common screening proxy for
# ankle-depth — enough to matter to a pedestrian or a ground-floor entrance, and
# deep enough to be a defensible "flood" rather than a puddle. It is a JUDGEMENT,
# not a measurement, and is stated everywhere it is applied.
FLOOD_DEPTH_M = 0.30


def _fill_to_volume(
    elevation: np.ndarray, volume_m3: float, cell_area_m2: float
) -> tuple[np.ndarray, float]:
    """Solve for the water-surface elevation that holds ``volume_m3``.

    Depth in cell i is ``max(0, W - e_i)``, so volume is monotonic in W and a
    bisection converges unconditionally. Returns the depth field and the level.
    """
    flat = elevation.ravel().astype(np.float64)
    if volume_m3 <= 0.0:
        return np.zeros_like(elevation), float(flat.min())

    def volume_at(level: float) -> float:
        return float(np.maximum(0.0, level - flat).sum() * cell_area_m2)

    lo, hi = float(flat.min()), float(flat.max())
    # Grow the upper bound until it can hold the requested volume.
    span = max(hi - lo, 1.0)
    while volume_at(hi) < volume_m3 and (hi - lo) < span * 100.0:
        hi += span
    if volume_at(hi) < volume_m3:
        # More water than the basin can hold: everything is submerged.
        depth = np.maximum(0.0, hi - flat).reshape(elevation.shape)
        return depth, hi

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if volume_at(mid) < volume_m3:
            lo = mid
        else:
            hi = mid
    level = 0.5 * (lo + hi)
    depth = np.maximum(0.0, level - flat).reshape(elevation.shape)
    return depth, level


def compute(rainfall_mm: float, resolution_m: float, sink_fill: bool = True,
            cn_scale: float = 1.0) -> dict:
    """One wet-grid run.

    ``cn_scale`` multiplies every Curve Number. It exists for the sensitivity
    pass, and it is the honest answer to "how much does this number depend on the
    one assumption nobody has verified": rather than defending a single value,
    the model reports the range the assumption implies.
    """
    ov = load_overlay(resolution_m)
    cell_area = ov.cell_area_m2

    # --- terrain conditioning ---------------------------------------------
    # Priority-Flood before anything else touches the surface.
    #
    # A DEM straight from a 90 m global model has closed pits -- 11.5% of cells
    # in this extract -- and every one of them is a cell the level-pool solver
    # would happily flood to an arbitrary depth, because a pit has no downhill
    # neighbour to spill into. Filling first means the water level that comes
    # out corresponds to a real basin rather than an artefact.
    #
    # See scripts/hydrology.py for why this is implemented here rather than
    # pulled from richdem (which needs GDAL headers to build and cannot).
    raw_elevation = ov.elevation
    accumulation = None
    receiver = None
    cells_raised = 0
    if sink_fill:
        result = condition_dem(raw_elevation)
        ov.elevation = result["filled"]
        accumulation = result["accumulation"]
        # condition() reshapes the D8 receiver to the grid; the cascade needs the
        # flat index space d8_flow_direction produced. Keep the grid-shaped form
        # here and ravel it explicitly in main(), where it is written to disk and
        # handed to fill_and_spill -- a silent reshape is the failure mode this
        # project already hit once.
        receiver = result["receiver"]
        cells_raised = int(result["cells_raised"])
    else:
        accumulation = np.zeros_like(raw_elevation)

    # ---- 1. generation -----------------------------------------------------
    cn = np.zeros_like(ov.elevation)
    for name, value in CURVE_NUMBER_HSG_D.items():
        # clamp to 100: a scaled CN above the physical maximum would silently
        # imply more than 100% runoff, which is not a thing.
        cn[ov.class_mask(name)] = min(100.0, value * cn_scale)

    runoff_m = runoff_depth(rainfall_mm, cn)

    # ---- 2. retention ------------------------------------------------------
    storage_m = np.full_like(ov.elevation, DEPRESSION_STORAGE_M)
    for name, depth in STORAGE_DEPTH_M.items():
        storage_m[ov.class_mask(name)] = depth

    # A retention cell generates nothing: an existing pond's contribution is
    # already inside its storage term, so counting it again would double count.
    #
    # This used to zero only `water`, while `COUNTED_AS` declared *both* water and
    # wetland as retention. Wetland therefore generated runoff at CN 90 **and** drew
    # a 0.60 m storage allowance -- the double count ADR-015 exists to prevent, and
    # it was worth roughly 2x on the wetland contribution. Driving it off
    # `COUNTED_AS` rather than a literal list means the declaration and the
    # implementation cannot drift apart again.
    for _name, _side in COUNTED_AS.items():
        if _side == "retention":
            runoff_m[ov.class_mask(_name)] = 0.0

    unstored_m = np.maximum(runoff_m - storage_m, 0.0)

    volume_m3 = float(unstored_m.sum() * cell_area)

    # ---- 3. allocation: fill from the lowest ground up --------------------
    # Physically this is a level pool: find the water-surface elevation W such
    # that the volume of water below it equals the unstored volume. That is
    # exactly "allocate from the lowest cells upward", and unlike spreading a
    # uniform depth over N cells it produces a flat water surface, which is
    # what a real pond looks like and what makes the picture defensible.
    depth_field, water_level = _fill_to_volume(ov.elevation, volume_m3, cell_area)
    wet_cells = int((depth_field > 0).sum())

    return {
        "overlay": ov,
        "cn_scale": cn_scale,
        "raw_elevation": raw_elevation,
        "accumulation": accumulation,
        "receiver": receiver,
        "cells_raised": cells_raised,
        "rainfall_mm": rainfall_mm,
        "runoff_m": runoff_m,
        "unstored_m": unstored_m,
        "depth_field": depth_field,
        "volume_m3": volume_m3,
        "water_level_m": water_level,
        "wet_cells": wet_cells,
        "wet_area_ha": wet_cells * cell_area / 10_000.0,
        "total_area_ha": ov.rows * ov.cols * cell_area / 10_000.0,
    }


def _exact_building_mask(ov) -> "np.ndarray | None":
    """Exact building-outline mask for the wet-grid PNG, or ``None``.

    ``_overlay_common.building_footprint_mask`` returns the axis-aligned
    bounding-box envelope, which is the right raster for the proposal gate's
    "never looser than the pool filter" contract but the wrong one for painting:
    an AABB over-covers a rotated footprint's diagonals (swallowing street
    ponding) and under-covers its corners (leaving a sliver of the tower blue).
    This follows the real polygon.
    """
    import json as _json

    from app.services.design_metrics import building_mask_from_geojson

    path = settings.rasters_dir / "buildings_hsk.json"
    if not path.exists():
        return None
    try:
        features = _json.loads(path.read_text(encoding="utf-8")).get("features") or []
    except (OSError, ValueError):
        return None
    if not features:
        return None
    return building_mask_from_geojson(features, ov)


def to_rgba(depth_field: np.ndarray) -> np.ndarray:
    """Depth field -> RGBA, transparent where dry, deep blue where wet."""
    norm = np.clip(
        (depth_field - MIN_VIS_DEPTH_M) / (MAX_VIS_DEPTH_M - MIN_VIS_DEPTH_M), 0.0, 1.0
    )
    alpha = (norm * 235).astype(np.uint8)
    # Deep blue -> cyan as depth increases, so the gradient reads as depth.
    red = (30 * (1.0 - norm)).astype(np.uint8)
    green = (90 + 120 * (1.0 - norm)).astype(np.uint8)
    blue = np.full_like(alpha, 235)
    return np.dstack([red, green, blue, alpha])


def flood_rgba(depth_field: np.ndarray,
               threshold_m: float = FLOOD_DEPTH_M) -> np.ndarray:
    """Routed depth field -> RGBA flood map: transparent below the threshold.

    Unlike :func:`to_rgba` (a continuous ponding ramp), this renders a binary
    "flooded / not flooded" picture: a cell is opaque only where its standing
    depth after D8 routing reaches ``threshold_m``. Above the threshold the
    opacity still grades with depth so deeper flooding reads stronger, but the
    floor is a hard cut — below it the cell is invisible, which is what makes the
    overlay mean "flood extent" rather than "wet ground". The same ramp colours
    as :func:`to_rgba` keep the two overlays visually continuous.
    """
    norm = np.clip(
        (depth_field - threshold_m) / max(MAX_VIS_DEPTH_M - threshold_m, 1e-9),
        0.0, 1.0,
    )
    # Mask cells strictly below the threshold to fully transparent.
    below = depth_field < threshold_m
    norm[below] = 0.0
    alpha = (norm * 235).astype(np.uint8)
    red = (30 * (1.0 - norm)).astype(np.uint8)
    green = (90 + 120 * (1.0 - norm)).astype(np.uint8)
    blue = np.full_like(alpha, 235)
    return np.dstack([red, green, blue, alpha])


def flow_rgba(accumulation: np.ndarray) -> np.ndarray:
    """D8 flow accumulation -> RGBA precipitation-flow map.

    Renders *where rain goes*, not how deep the ponding is: every cell is
    coloured by the number of upstream cells draining through it (log-scaled,
    because D8 accumulation spans three orders of magnitude and a linear ramp
    would render the whole site as one flat colour with a single dark river).
    Low accumulation (ridges, flats) is a pale yellow-green wash that lets the
    terrain show through; high accumulation (the drainage network) deepens
    through teal into dark blue. Deeper blue == more water passing through.

    This replaces the old ponding-depth ramp as the flood-layer render: the
    depth field answers "how deep does it pond in the depressions", while the
    accumulation field answers "where does the rain actually flow" -- the
    question the siting axis is built on, and the one the picture should show.
    """
    acc = np.asarray(accumulation, dtype=np.float64)
    positive = acc[acc > 0]
    if positive.size == 0:
        return np.zeros((*acc.shape, 4), dtype=np.uint8)
    # Log scale with wide percentile anchors. The spread is what makes the
    # contrast legible: anchoring the low end at the 1st percentile and the high
    # end at the 99th spreads the bulk of cells across the whole ramp instead of
    # pinning them in a narrow band. log1p(1) == 0 keeps a single-cell trickle
    # at the very bottom of the scale.
    log_acc = np.log1p(acc)
    lo = np.log1p(max(float(np.percentile(positive, 1)), 0.0))
    hi = np.log1p(max(float(np.percentile(positive, 99)), 2.0))
    t = np.clip((log_acc - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    # A gamma lift spreads the mid-tones so small differences in the common
    # range (the 5-100 cell band most of the site lives in) become visible,
    # instead of all reading as near-identical light blue.
    t = t ** 0.75

    # Three-stop ramp, blue-led so "more flow" reads as "bluer", with a neutral
    # warm grey (not yellow) as the low-value colour:
    #   warm grey (t=0) -> mid blue (t=0.5) -> deep blue (t=1)
    stops = np.array(
        [[186.0, 184.0, 178.0], [96.0, 138.0, 196.0], [12.0, 42.0, 108.0]]
    )
    rgb = np.empty((*acc.shape, 3), dtype=np.float64)
    for i in range(2):
        lo_t, hi_t = i / 2.0, (i + 1) / 2.0
        seg = np.clip((t - lo_t) / (hi_t - lo_t), 0.0, 1.0)
        rgb = np.where((t >= lo_t)[..., None],
                       stops[i] + (stops[i + 1] - stops[i]) * seg[..., None], rgb)
    rgb[t >= 1.0] = stops[2]

    # Alpha now rises steeply and early: the drainage network must read as
    # solid deep blue, not a pale hint. Start at a firmer floor and reach
    # near-opaque sooner so the blue itself carries the "how much" signal.
    alpha = (150.0 + 105.0 * t).astype(np.uint8)
    # Cells with *zero* accumulation are transparent, not warm-grey. A masked
    # cell (a building footprint the caller has set to 0.0) must vanish, not
    # paint the roof's own wash of grey; and on this grid no natural cell is
    # ever zero (D8 accumulation is >= 1 everywhere), so this only ever fires
    # on the deliberately-masked cells -- it never erases a real low-flow cell
    # from the warm-grey end of the ramp.
    alpha[acc == 0] = 0
    return np.dstack(
        [rgb[..., 0].astype(np.uint8), rgb[..., 1].astype(np.uint8),
         rgb[..., 2].astype(np.uint8), alpha]
    )


def runoff_accumulation(
    filled: np.ndarray, receiver: np.ndarray, runoff_m3: np.ndarray
) -> np.ndarray:
    """CN-weighted runoff volume accumulated down the D8 cascade, in m3.

    This is :func:`hydrology.flow_accumulation` with the per-cell seed changed
    from a unit count to the cell's own ``runoff_m3`` (SCS-CN runoff less
    retained storage). The result at a cell is the total upstream volume
    passing through it -- the "how much water arrives here" picture the
    precipitation-flow overlay should show, and the field that actually
    responds to interventions: paving a slope raises its ``runoff_m3`` and thus
    every downstream cell's accumulation, where the bare cell-count field is
    scenario-independent.

    Same forward pass in decreasing elevation order, so a cell is final once
    processed. ``receiver`` and ``runoff_m3`` are flat, in the same
    ``row*cols+col`` index space.
    """
    filled = np.asarray(filled, dtype=np.float64)
    rows, cols = filled.shape
    order = np.argsort(filled, axis=None)[::-1]  # descending elevation
    acc = np.asarray(runoff_m3, dtype=np.float64).ravel().copy()
    receiver = np.asarray(receiver).ravel()
    for idx in order:
        target = receiver[idx]
        if target >= 0:
            acc[target] += acc[idx]
    return acc.reshape(rows, cols)


def routed_summary(cascade: dict | None) -> dict:
    """The mass-balance block written into the sidecar (or why it is absent).

    Reporting shape, not the enforcement: :func:`hydrology.mass_balance` has
    already raised inside the cascade if the books did not balance, so a block
    reaching here is a balanced one. ``None`` means conditioning was skipped, and
    that is said plainly rather than left as a missing key.
    """
    if cascade is None:
        return {
            "status": "ROUTING_UNAVAILABLE",
            "reason": "Priority-Flood conditioning was disabled (--no-sink-fill)",
            "warning": (
                "Run scripts/make_wetgrid.py without --no-sink-fill to generate "
                "the receiver and raw surface. Not a flood prediction."
            ),
        }
    dep = cascade["depressions"]
    dep_max = max(dep["depthM"].values()) if dep["depthM"] else 0.0
    return {
        "status": "SCREENING_ROUTED_NOT_A_FLOOD_PREDICTION",
        "warning": (
            "Not a flood prediction. Not engineering-grade. Screening-level D8 "
            "fill-and-spill on a Priority-Flood conditioned 5 m surface."
        ),
        "runoffInM3": round(float(cascade["runoffInM3"]), 1),
        "storedM3": round(float(cascade["storedM3"]), 1),
        "dischargedM3": round(float(cascade["dischargedM3"]), 1),
        "massBalance": cascade["massBalance"],
        "wetCells": int(cascade["wetCells"]),
        "maxDepthM": round(float(cascade["maxDepthM"]), 4),
        "depressions": {
            "count": int(dep["count"]),
            "maxDepthM": round(float(dep_max), 4),
        },
    }


def _sensitivity(args) -> int:
    """How far does the answer move if the Curve Number table is wrong?

    The CN table is the single unverified assumption in the model, so the useful
    output is not one number but the spread around it. Each row is a full
    recomputation through the same `compute()` the real run uses, so this
    measures the model and not a simplified proxy of it.
    """
    print(f"[wetgrid] CN sensitivity at {args.rainfall} mm rainfall")
    print(f"[wetgrid] CN source: {CN_SOURCE}")
    print()
    print(f"  {'CN scale':>9} {'runoff m3':>14} {'unstored m3':>14} "
          f"{'wet ha':>9} {'wet %':>7}")
    rows = []
    for scale in (0.8, 0.9, 1.0, 1.1, 1.2):
        r = compute(args.rainfall, args.resolution,
                    sink_fill=not args.no_sink_fill, cn_scale=scale)
        ov = r["overlay"]
        runoff = float(r["runoff_m"].sum() * ov.cell_area_m2)
        rows.append((scale, runoff, r["volume_m3"], r["wet_area_ha"]))
        print(f"  {scale:>8.0%} {runoff:>14,.0f} {r['volume_m3']:>14,.0f} "
              f"{r['wet_area_ha']:>9,.1f} {100*r['wet_area_ha']/r['total_area_ha']:>6.1f}%")

    low = min(r[1] for r in rows)
    high = max(r[1] for r in rows)
    base = next(r[1] for r in rows if r[0] == 1.0)
    print()
    print(f"[wetgrid] runoff range over CN +/-20%: {low:,.0f} .. {high:,.0f} m3")
    print(f"[wetgrid] spread is {100*(high-low)/base:.1f}% of the central value")
    print("[wetgrid] This range, not the single figure, is the honest answer to")
    print("           'what if the Curve Number table is wrong?'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rainfall", type=float, default=450.0, help="rainfall depth, mm")
    parser.add_argument("--resolution", type=float, default=5.0, help="cell size, m")
    parser.add_argument(
        "--out", default=None, help="output PNG (default data/rasters/wetgrid_hsk.png)"
    )
    parser.add_argument(
        "--sensitivity", action="store_true",
        help="report how the result moves across a CN range, then exit",
    )
    parser.add_argument(
        "--no-sink-fill", action="store_true",
        help="skip Priority-Flood conditioning (shows how much it matters)",
    )
    args = parser.parse_args()

    if args.sensitivity:
        return _sensitivity(args)

    result = compute(args.rainfall, args.resolution,
                     sink_fill=not args.no_sink_fill)
    ov = result["overlay"]

    out = Path(args.out) if args.out else settings.rasters_dir / "wetgrid_hsk.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Render-time building mask: the depth field is solved on bare earth, so a
    # wet cell can fall under a tower; painted onto 3D Tiles that would show
    # water on a roof. The data (wetgrid_hsk.npy below) keeps every cell.
    #
    # The mask follows the *real* building outlines (point-in-polygon), not the
    # axis-aligned envelopes `building_footprint_mask` rasterises for the
    # proposal gate. An AABB mask over-covers a rotated footprint's diagonals
    # and under-covers its corners, so a tower's own edge would leak blue.
    render_accum = result["accumulation"]
    footprint = _exact_building_mask(ov)
    if footprint is not None:
        render_accum = np.where(footprint, 0.0, render_accum)
    # The static overlay is the precipitation-flow picture (D8 accumulation,
    # deeper blue == more upstream drainage), not the ponding-depth field. The
    # depth field stays in wetgrid_hsk.npy for anything that needs the numbers.
    Image.fromarray(flow_rgba(render_accum), mode="RGBA").save(out)

    # Also write the depth field as float32 metres.
    #
    # The PNG is a *render*, and its alpha channel saturates at
    # MAX_VIS_DEPTH_M (0.30 m) so the image stays readable. That makes it lossy
    # for analysis: in the 450 mm run the mean depth in the wet area is ~1.5 m,
    # so nearly every cell clips and the true depth is unrecoverable from the
    # image. Anything that needs to re-read the numbers -- siting interventions,
    # comparing scenarios, computing statistics -- must use this file instead.
    if args.out is None:
        np.save(settings.rasters_dir / "wetgrid_hsk.npy",
                result["depth_field"].astype(np.float32))
        # Flow accumulation is the standard hydrology product and is useful on
        # its own -- it says where water concentrates, independently of any
        # volume assumption. Kept as data because, like the depth field, the
        # PNG cannot represent it.
        np.save(settings.rasters_dir / "flow_accum_hsk.npy",
                result["accumulation"].astype(np.float32))

    # ---- fill-and-spill cascade (Stages 3-6, additive) ---------------------
    # The D8 receiver and the *raw* 5 m surface were computed but never saved:
    # `compute()` kept only the accumulation, and Grids.elevation_5m is the
    # Priority-Flood *filled* surface. Both are needed downstream -- the receiver
    # is the routing graph and a cell's depression capacity is `filled - raw` --
    # so they are written here, beside the accumulation. The level-pool depth
    # field and every headline number above are untouched: the cascade runs on
    # the unstored volume the ledger already produced, and mass_balance() raises
    # if its books do not balance.
    cascade = None
    if args.out is None:
        receiver = result["receiver"]
        if receiver is not None:
            # Flat, not grid-shaped: the cascade indexes cells by `row*cols+col`.
            # Stored flat so the on-disk artefact and Grids.receiver share the
            # same index space with no reshape in between.
            receiver_flat = np.asarray(receiver).ravel().astype(np.int32)
            np.save(settings.rasters_dir / "flow_receiver_hsk.npy", receiver_flat)
            # The *filled* surface is persisted too, and it has to be.
            #
            # The design assumed Grids.elevation_5m already held the
            # Priority-Flood filled surface because make_wetgrid mutates
            # ov.elevation -- but that mutation is local, and load_grids() builds
            # a fresh Overlay from the raw DEM, so elevation_5m was in fact the
            # *raw* surface (verified: only 94.8% of D8 receivers point downhill
            # on it). The cascade must process cells in the same order the
            # receiver was built on, or water routed to an already-processed cell
            # is lost -- the mass balance then fails loudly, which is how this was
            # found. Saving the exact float64 surface the receiver was derived
            # from removes the ambiguity; float64 (not float32) so the reload is
            # bit-exact and the order cannot drift by a tie-break.
            np.save(settings.rasters_dir / "flow_filled_hsk.npy",
                    np.asarray(ov.elevation, dtype=np.float64))
            # float64, not float32: the depression mask is `filled > raw + 1e-9`,
            # and at these magnitudes float32 rounds by ~1e-5 m. A float32 raw
            # surface turns thousands of cells that were never raised into
            # apparent depressions (1748 components against 37 on the real grid)
            # and inflates the capacity that is meant to be `filled - raw` for
            # genuinely filled cells only.
            np.save(settings.rasters_dir / "flow_raw_elev_hsk.npy",
                    np.asarray(result["raw_elevation"], dtype=np.float64))
            cascade = fill_and_spill(
                filled=ov.elevation,
                raw=result["raw_elevation"],
                receiver=receiver_flat,
                runoff_in_m3=result["unstored_m"] * ov.cell_area_m2,
                cell_area_m2=ov.cell_area_m2,
            )
            np.save(settings.rasters_dir / "routed_depth_hsk.npy",
                    cascade["depthField"].astype(np.float32))

    meta = {
        "artefact": "wetgrid",
        "generated": "2026-10-06",
        "status": (
            "REAL_TERRAIN_SATISFACTORY"
            if ov.terrain_source == "open3dhk-tiles"
            else "SCREENING_VIEW_SYNTHETIC_TERRAIN"
        ),
        "terrainSource": ov.terrain_source,
        "conditioning": {
            "method": "Priority-Flood (Barnes et al. 2014)",
            "enabled": not args.no_sink_fill,
            "cellsRaised": result["cells_raised"],
            "cellsTotal": int(ov.rows * ov.cols),
            "raisedFraction": round(result["cells_raised"] / (ov.rows * ov.cols), 4),
            "maxFlowAccumulationCells": round(float(result["accumulation"].max()), 1),
            "note": (
                "Implemented in scripts/hydrology.py. richdem and hydrotools are "
                "the standard open-source tools for this; both were installed and "
                "neither is usable on this host because richdem needs GDAL headers "
                "to build."
            ),
        },
        "landCoverSource": ov.landcover_source,
        "warning": (
            "Real elevation and real ESA WorldCover land cover. The surface is "
            "Priority-Flood conditioned and D8 flow accumulation is computed and "
            "reported, but the runoff VOLUME is still allocated by solving for a "
            "water level rather than routed downhill cell by cell. It is a "
            "screening view, not a flood prediction."
            if ov.terrain_source == "open3dhk-tiles"
            else "Terrain is SYNTHETIC (see _overlay_common). Run "
                 "scripts/fetch_dem.py to replace it with real elevation."
        ),
        "inputs": {
            "rainfall_mm": args.rainfall,
            "resolution_m": args.resolution,
            "rows": ov.rows,
            "cols": ov.cols,
            "bounds": list(ov.bounds),
            "cell_area_m2": ov.cell_area_m2,
        },
        "assumptions": {
            "hydrologicSoilGroup": "D",
            "curveNumberByClass": CURVE_NUMBER_HSG_D,
            "curveNumberSource": "USDA NRCS TR-55; awaiting independent verification",
            "storageDepthM": STORAGE_DEPTH_M,
            "depressionStorageM": DEPRESSION_STORAGE_M,
            "countedAs": COUNTED_AS,
            "doubleCountingRule": (
                "Land cover sets the generation term; explicit storage sets the "
                "retention term. Open water generates nothing so a pond is never "
                "counted in both (ADR-015)."
            ),
            "verticalDatum": "mPD",
        },
        "results": {
            "runoffVolumeM3": round(
                float(result["runoff_m"].sum() * ov.cell_area_m2), 1
            ),
            "unstoredVolumeM3": round(result["volume_m3"], 1),
            "wetCells": result["wet_cells"],
            "wetAreaHa": round(result["wet_area_ha"], 2),
            "totalAreaHa": round(result["total_area_ha"], 2),
            "wetAreaFraction": round(
                result["wet_area_ha"] / max(result["total_area_ha"], 1e-9), 4
            ),
            "meanDepthInWetAreaM": round(
                result["volume_m3"]
                / max(result["wet_cells"] * ov.cell_area_m2, 1e-9),
                4,
            ),
        },
        "render": {
            "minVisibleDepthM": MIN_VIS_DEPTH_M,
            "maxVisibleDepthM": MAX_VIS_DEPTH_M,
            "note": "Depth beyond maxVisibleDepthM is visually saturated, not capped in the data.",
            "buildingsMaskedFromRender": footprint is not None,
            "buildingsMaskNote": (
                "Cells under a building footprint render transparent. The "
                "surface is bare-earth, so the solver can place water where a "
                "building stands; painting that onto 3D Tiles would show water "
                "on a roof. The mask affects the PNG only -- wetgrid_hsk.npy "
                "keeps every solved cell."
            ) if footprint is not None else None,
        },
        "landClasses": list(LAND_CLASSES),
        "landClassProvenance": (
            "ESA WorldCover 10 m v200 (2021), CC BY 4.0; see data/landcover_hsk.json"
            if ov.landcover_source == "esa-worldcover"
            else "SYNTHETIC PLACEHOLDER; run scripts/fetch_landcover.py"
        ),
    }
    # The routed mass-balance block is written only for the default output, where
    # the receiver/raw/routed artefacts were actually produced beside it.
    if args.out is None:
        meta["routed"] = routed_summary(cascade)
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    rel = attach_to_scene("wet_grid_path", out)
    if rel:
        print(f"[attach] wet_grid_path -> {rel}")

    r = result
    print(f"[wetgrid] {out}")
    print(f"[wetgrid] grid {ov.cols} x {ov.rows} @ {args.resolution} m")
    print(f"[wetgrid] runoff      {meta['results']['runoffVolumeM3']:>12,.0f} m3")
    print(f"[wetgrid] unstored    {r['volume_m3']:>12,.0f} m3")
    print(f"[wetgrid] wet area    {r['wet_area_ha']:>12,.1f} ha "
          f"({meta['results']['wetAreaFraction'] * 100:.1f}% of extent)")
    print(f"[wetgrid] mean depth {meta['results']['meanDepthInWetAreaM']:>12.3f} m")
    if result["cells_raised"]:
        print(f"[wetgrid] Priority-Flood raised {result['cells_raised']} cells "
              f"({100*result['cells_raised']/(ov.rows*ov.cols):.1f}%) out of depressions")
    print(f"[wetgrid] max flow accumulation: {result['accumulation'].max():.0f} cells")
    if cascade is not None:
        mb = cascade["massBalance"]
        print(f"[wetgrid] routed stored    {cascade['storedM3']:>12,.0f} m3")
        print(f"[wetgrid] routed discharge {cascade['dischargedM3']:>12,.0f} m3")
        print(f"[wetgrid] routed wet area  {cascade['wetCells'] * ov.cell_area_m2 / 10_000.0:>12,.1f} ha")
        print(f"[wetgrid] mass balance residual {mb['residualM3']:+.3e} m3 "
              f"(relative {mb['relativeResidual']:.2e}, tol {mb['tol']:.0e})")
        print(f"[wetgrid] depressions: {cascade['depressions']['count']} "
              f"({cascade['depressions']['depressionCells']} cells)")
    print(f"[wetgrid] terrain: {ov.terrain_source}   land cover: {ov.landcover_source}")
    print("[wetgrid] screening view - the headline wet grid is a level-pool allocation;")
    print("          the D8 fill-and-spill cascade is reported BESIDE it, additively.")
    print("          Not a flood prediction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
