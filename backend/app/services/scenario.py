"""Evaluate one design scenario against all three axes, and refuse to total them.

## What this is for

The user-facing goal is to make a design choice *legible*: this change, in this
place, moves shade, ventilation and drainage by these amounts. Stage 1
(:mod:`app.services.design_metrics`) supplied the measurements. This module is the
thing that puts a before and an after side by side.

## The one design decision everything else hangs on

**There is no combined score, and that is deliberate.**

The obvious thing to do with three axes is to weight them and add them up. It is
also the one thing that cannot be defended here, for three separate reasons:

1. **The units are not commensurable.** Shade is a fraction of ground. Frontal
   area density is a dimensionless blockage. Runoff is cubic metres. Any weights
   that make them add up are arbitrary, and the answer moves when they change --
   which means the number is a statement about the weights, not about the scheme.
2. **The ventilation axis has no threshold to normalise against.** The AVA policy
   behind Hong Kong's guidance states that "whether the better design option meets
   a standard cannot be known". So a ventilation score has no natural 0 or 1 to
   be a fraction of.
3. **The axes genuinely conflict, and that is the finding rather than a problem
   to be smoothed away.** Raising heights shades more ground and blocks more wind.
   Any single score decides that trade-off silently. A single number would let a
   scheme look good by weighting one axis the user did not care about.

So :func:`evaluate` returns three independent axes, each with its own units,
its own method and its own limitation, plus a list of the trade-offs that were
made in plain language. The ordering is left to :func:`compare`, which is Stage 3
and which must state its weights out loud.

## What each axis actually responds to

This is a property of the model, not a choice, and it surprises people:

- **Shade** responds to massing geometry: height, spacing, orientation.
- **Ventilation** responds to massing geometry: frontal area and ground-level
  blockage.
- **Drainage** responds to *land cover and water*, not to building height. The
  storage ledger runs on the 5 m land-class grid; a building's footprint is
  already impervious whatever its height, so raising it does not change a single
  runoff cell.

That last one is worth stating plainly, because "make the district 30% taller"
returning a drainage delta of exactly zero is arithmetically correct and would
otherwise look like a bug. Interventions -- green space, permeable paving, a water
reservoir -- are what move the drainage axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.services.design_metrics import (
    Footprint,
    footprint_from_ring,
    footprints_from_geojson,
    frontal_area_density,
    shade_fraction,
)
from app.services.sandbox_model import (
    DEFAULT_RAINFALL_MM,
    LAND_CLASSES,
    Grids,
    storage_ledger,
)
from app.services.ventilation import fad_ratio

# The three axes and the direction that means "more of the thing". Deliberately
# not a weight: nothing here is normalised, because the units are incompatible.
AXIS_UNITS: dict[str, str] = {
    "shade": "fraction of the study area shaded",
    "ventilation": "frontal area density (dimensionless blockage)",
    "drainage": "cubic metres of runoff with nowhere to go",
}

# What a rising number means on each axis, as (up, down).
#
# This replaces a hand-written if/elif chain that had the ventilation case
# inverted -- it reported "less blockage" for a *rise* in frontal area density,
# which is the opposite of what happened, in the one field a reader is meant to
# trust. The direction of each axis is now stated once, here, instead of being
# re-derived at each use.
AXIS_UP_MEANS: dict[str, tuple[str, str]] = {
    # more shaded ground = less solar gain reaching the pedestrian level
    "shade": ("more shade", "less shade"),
    # more frontal area = the wind meets more obstruction
    "ventilation": ("more blockage", "less blockage"),
    # more unstored volume = a bigger deficit
    "drainage": ("more runoff with nowhere to go",
                 "less runoff with nowhere to go"),
}


@dataclass(frozen=True)
class MassingOp:
    """One change to the massing, in the same vocabulary the frontend uses.

    Mirrors ``MassingOp`` in ``frontend/src/view/cityModel.ts``. That mirroring is
    a real duplication and this docstring is where the risk is recorded: the
    frontend applies these to Cesium entities so the user sees the 3D change, and
    this module applies them to metric footprints so the change can be scored. If
    the two ever disagree, the panel would show one scheme while this file
    evaluated another -- a wrong answer that looks entirely correct.

    :func:`test_frontend_and_backend_agree_on_massing_semantics` in
    ``tests/test_scenario.py`` pins the semantics from both sides by reading the
    TypeScript and the Python and asserting the operation names and the bound
    checks agree, so the duplication fails loudly instead of quietly.
    """

    op: str
    multiplier: float | None = None
    height_m: float | None = None
    delta_m: float | None = None
    degrees: float | None = None
    dx_m: float | None = None
    dy_m: float | None = None
    scope: str | None = None
    count: int | None = None
    height_m_new: float | None = None
    # Direct coordinate/size editing (added 10 Oct 2026). The model can now name
    # a target position and a footprint scale, so "move this block to the
    # centre of the site and widen it by 20%" is one op. The coordinate is
    # validated against the study-area bounds -- it is not a free invention
    # because a coordinate outside the bbox is not on this site.
    target_lon: float | None = None
    target_lat: float | None = None
    width_scale: float | None = None
    depth_scale: float | None = None

    SUPPORTED = ("scale_all", "set_site", "rotate", "move", "place", "reset",
                 "resize")

    def __post_init__(self) -> None:
        if self.op not in self.SUPPORTED:
            raise ValueError(f"unsupported massing op {self.op!r}")


def _region_fraction(scope: str | None) -> tuple[float, float, float, float]:
    """A scope name -> the fraction of the bbox it covers, as (x0, y0, x1, y1).

    The same ten regions the AI planner resolves, kept here as plain arithmetic on
    the locked study-area bbox. It has to be the *same* ten with the *same*
    fractions; a second table would let the AI scope an edit to the west and this
    module scope the same edit to somewhere else.
    """
    table = {
        "all": (0.0, 0.0, 1.0, 1.0),
        "west": (0.0, 0.0, 0.5, 1.0), "east": (0.5, 0.0, 1.0, 1.0),
        "north": (0.0, 0.5, 1.0, 1.0), "south": (0.0, 0.0, 1.0, 0.5),
        "centre": (0.25, 0.25, 0.75, 0.75),
        "north-west": (0.0, 0.5, 0.5, 1.0), "north-east": (0.5, 0.5, 1.0, 1.0),
        "south-west": (0.0, 0.0, 0.5, 0.5), "south-east": (0.5, 0.0, 1.0, 0.5),
    }
    # An unrecognised scope is fatal, never widened. Widening to "all" is the exact
    # bug this project's Decision 13 was written to remove: a scoped request
    # answered with an unscoped edit, rendered as a plausible wrong skyline.
    key = (scope or "all").strip().lower().replace("-", " ")
    key = key.replace(" ", "-")
    if key not in table:
        raise ValueError(f"unrecognised scope {scope!r}; refusing to widen it")
    return table[key]


def apply_massing(
    footprints: list[Footprint],
    ops: list[MassingOp],
    bounds: tuple[float, float, float, float],
) -> list[Footprint]:
    """Apply massing operations to metric footprints. Returns new objects.

    Pure: the input list is never mutated, so a caller can evaluate a candidate
    and still hold the baseline.

    ``place`` and ``reset`` became scoreable here on 9 Oct 2026, closing the
    "placeable but not scoreable" gap. The mechanism is delegation, never a
    second implementation: ``place`` calls the *same* generator the AI planner
    uses (:func:`app.services.ai_plan.placement_footprints`, which itself calls
    ``make_citymodel.build_blocks``), and converts each ring through the single
    :func:`app.services.design_metrics.footprint_from_ring` conversion. ``reset``
    restores the generated baseline the way the frontend's own reset does — by
    discarding every earlier op's effect — so the evaluator and the viewer agree
    on what "reset" means by construction rather than by hope.
    """
    x0, y0, x1, y1 = bounds
    # The generated baseline, kept for `reset`. Snapshotting the *input* (not a
    # copy made after some ops ran) matches the frontend semantics exactly: its
    # apply() removes placed blocks and restores heights before applying any op
    # sequence, and its `reset` op discards the rest. Only objects are shared,
    # never mutation — every op below rebuilds rather than editing in place.
    baseline = list(footprints)
    out = list(footprints)
    for op in ops:
        if op.op == "reset":
            # Restore the generated state and discard everything before this
            # point, exactly as the viewer's reset does: the seeded heights back
            # and any placed blocks gone. Ops after this one still apply.
            out = list(baseline)
            continue
        fx0, fy0, fx1, fy1 = _region_fraction(op.scope)
        lx0, ly0 = x0 + (x1 - x0) * fx0, y0 + (y1 - y0) * fy0
        lx1, ly1 = x0 + (x1 - x0) * fx1, y0 + (y1 - y0) * fy1
        idx = [i for i, f in enumerate(out)
               if lx0 <= f.lon <= lx1 and ly0 <= f.lat <= ly1]

        if op.op == "scale_all":
            m = float(op.multiplier or 1.0)
            # bearing_deg is carried through on every rebuild below. Dropping it
            # let Footprint's default 0.0 silently erase the orientation a
            # preceding `rotate` set, so `[rotate, scale_all]` scored as if
            # unrotated -- a plausible wrong number, and the geometry in
            # frontal_area_raster / cell_fad reads exactly this field. Only
            # `rotate` may ever change the bearing.
            out = [Footprint(f.lon, f.lat, f.width_m, f.depth_m, f.height_m * m,
                             bearing_deg=f.bearing_deg)
                   if i in set(idx) else f for i, f in enumerate(out)]
        elif op.op == "set_site":
            out = [
                Footprint(f.lon, f.lat, f.width_m, f.depth_m,
                          (max(1.0, f.height_m + float(op.delta_m))
                           if op.delta_m is not None
                           else float(op.height_m or f.height_m)),
                          bearing_deg=f.bearing_deg)
                if i in set(idx) else f
                for i, f in enumerate(out)
            ]
        elif op.op == "rotate":
            deg = float(op.degrees or 0.0)
            if abs(deg) > 1e-9:
                # Rotation sets the footprint's BEARING and leaves its sides alone.
                # An earlier version instead rotated the axis-aligned bounding box,
                # so a 30x30 block turned 20 degrees came back as a 37x37 block --
                # the rotation vanished and the block merely grew. Worse, the growth
                # raised frontal area, so the screen reported a *worse* wind outcome
                # for a scheme whose orientation had actually improved it.
                targets = set(idx)
                out = [Footprint(
                    f.lon, f.lat, f.width_m, f.depth_m, f.height_m,
                    bearing_deg=(f.bearing_deg + deg) % 360.0)
                    if i in targets else f
                    for i, f in enumerate(out)]
        elif op.op == "move":
            # Two modes: a metric offset (dx/dy) or a direct target coordinate.
            # The coordinate is validated against the study-area bounds -- it is
            # not a free invention, because a coordinate outside the bbox is not
            # on this site. The model never supplies a coordinate from nowhere:
            # it gets one from the scope ring's centroid or the instruction,
            # and the bounds check is the guard.
            if op.target_lon is not None and op.target_lat is not None:
                # Validate: the target must be inside the study area.
                tlx, tly = float(op.target_lon), float(op.target_lat)
                if not (x0 <= tlx <= x1 and y0 <= tly <= y1):
                    # Out of bounds -- refuse rather than inventing a position
                    # off-site.
                    continue
                targets = set(idx)
                out = [
                    Footprint(tlx, tly, f.width_m, f.depth_m, f.height_m,
                              bearing_deg=f.bearing_deg)
                    if i in targets else f
                    for i, f in enumerate(out)
                ]
            else:
                dx = float(op.dx_m or 0.0)
                dy = float(op.dy_m or 0.0)
                if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                    # The metres-per-degree-longitude factor depends on each
                    # footprint's OWN latitude, so it cannot be hoisted out of the
                    # loop. Hoisting it referenced an undefined `f_lat` and raised
                    # NameError; a single shared factor would also be wrong, because
                    # a move is metric while the degree scale shrinks with latitude.
                    targets = set(idx)
                    out = [
                        Footprint(
                            f.lon + dx / (111_320.0 * math.cos(math.radians(f.lat))),
                            f.lat + dy / 110_540.0,
                            f.width_m, f.depth_m, f.height_m,
                            bearing_deg=f.bearing_deg,
                        ) if i in targets else f
                        for i, f in enumerate(out)
                    ]
        elif op.op == "resize":
            # Directly scale the footprint's width and/or depth. A width_scale of
            # 1.2 means 20% wider; 0.8 means 20% narrower. Both default to 1.0
            # (no change), so a caller can resize one axis only. The footprint's
            # area changes, which affects frontal area density and site coverage
            # -- both scoreable -- but the height and position are preserved.
            ws = float(op.width_scale) if op.width_scale is not None else 1.0
            ds = float(op.depth_scale) if op.depth_scale is not None else 1.0
            if not (0.1 <= ws <= 10.0 and 0.1 <= ds <= 10.0):
                continue
            if abs(ws - 1.0) > 1e-9 or abs(ds - 1.0) > 1e-9:
                targets = set(idx)
                out = [
                    Footprint(f.lon, f.lat,
                              max(1.0, f.width_m * ws),
                              max(1.0, f.depth_m * ds),
                              f.height_m,
                              bearing_deg=f.bearing_deg)
                    if i in targets else f
                    for i, f in enumerate(out)
                ]
        elif op.op == "place":
            # New massing, generated by the AI planner's own placement generator
            # on the paved class of the committed land cover — the same function,
            # the same block pitch, the same occupied-cell exclusion. A second
            # generator here would be exactly the drift this project forbids, so
            # there is none: the evaluator and the demo place the same blocks.
            count = int(op.count or 2)
            height = (op.height_m_new if op.height_m_new is not None
                      else (op.height_m if op.height_m is not None else 45.0))
            if count > 0:
                from app.services.ai_plan import (  # noqa: PLC0415
                    load_grounding, placement_footprints)
                scope_ring = [
                    [lx0, ly0], [lx1, ly0], [lx1, ly1], [lx0, ly1], [lx0, ly0]
                ]
                # The same grounding the AI planner loads: the committed land
                # cover, the paved mask, the occupied-cell exclusion. The overlay
                # lives inside it, which is why load_grounding() -- not a bare
                # Grounding -- is required here.
                placements = placement_footprints(
                    scope_ring, count, load_grounding())
                new_fps = []
                for block in placements:
                    fp = footprint_from_ring(block.get("ring"),
                                             float(block.get("heightM") or height))
                    if fp is not None:
                        new_fps.append(fp)
                if new_fps:
                    out = out + new_fps
                # An empty result is NOT an error here: a fully built scope has
                # nowhere to place, and the response reports the placed count
                # rather than inventing a block in a field.
    return out


def _land_class_after(grids: Grids, interventions: list[dict[str, Any]],
                      cell_area_m2: float) -> tuple[np.ndarray, list[str]]:
    """Apply land-use interventions to the class grid, for the drainage axis.

    Each intervention needs a ``kind`` from :data:`LAND_CLASSES` and a polygon.
    Counting is by *cell centroid inside the polygon*, which for a 5 m cell and a
    block-scale polygon is the right resolution; a building-scale intervention
    smaller than a cell would fall through it, and that is reported rather than
    silently rounded up.
    """
    from app.services.ai_plan import _point_in_ring  # noqa: PLC0415

    grid = grids.land_class.copy()
    notes: list[str] = []
    for iv in interventions:
        kind = str(iv.get("kind") or "")
        if kind not in LAND_CLASSES:
            notes.append(f"ignored: unknown intervention kind {kind!r}")
            continue
        ring = iv.get("ring") or []
        if len(ring) < 3:
            notes.append(f"ignored: {kind} has no usable polygon")
            continue
        target = LAND_CLASSES.index(kind)
        n = 0
        for r in range(grids.rows):
            for c in range(grids.cols):
                lon = grids.bounds[0] + (c + 0.5) / grids.cols * (
                    grids.bounds[2] - grids.bounds[0])
                lat = grids.bounds[3] - (r + 0.5) / grids.rows * (
                    grids.bounds[3] - grids.bounds[1])
                if _point_in_ring(lon, lat, ring):
                    if grid[r, c] != target:
                        n += 1
                    grid[r, c] = target
        notes.append(f"{kind}: {n} cells ({n * cell_area_m2:,.0f} m2) set")
    return grid, notes


def _axis(name: str, baseline: float, candidate: float,
          precision: int = 4) -> dict[str, Any]:
    """One axis: before, after, the delta, and which way the delta points.

    ``moreOrLess`` states what physically changed -- "more shade", "less
    blockage" -- rather than whether it is good. That distinction is the whole
    point: a delta is arithmetic, a verdict is a judgement, and only the second
    one needs a standard this project does not have.
    """
    delta = candidate - baseline
    if abs(delta) < (10 ** -precision) / 2:
        effect = "unchanged"
    else:
        up, down = AXIS_UP_MEANS[name]
        effect = up if delta > 0 else down
    return {
        "axis": name,
        "units": AXIS_UNITS[name],
        "baseline": round(baseline, precision),
        "candidate": round(candidate, precision),
        "delta": round(delta, precision),
        "moreOrLess": effect,
        "changed": effect != "unchanged",
    }


def evaluate(
    grids: Grids,
    baseline_features: list[dict[str, Any]],
    ops: list[MassingOp] | None = None,
    interventions: list[dict[str, Any]] | None = None,
    rainfall_mm: float | None = None,
    sun_hours: tuple[int, ...] = (9, 14),
    day_of_year: int = 355,
    lat_deg: float = 22.44,
    lon_deg: float = 114.01,
) -> dict[str, Any]:
    """Score one candidate scheme against the baseline on all three axes.

    Returns three axes side by side and a list of the trade-offs in plain
    language. **Returns no combined score**, for the reasons in the module
    docstring: the units are incompatible, the ventilation axis has no threshold
    to normalise against, and the axes genuinely conflict.
    """
    ops = ops or []
    interventions = interventions or []
    base_fp = footprints_from_geojson(baseline_features)
    cand_fp = apply_massing(base_fp, ops, grids.bounds)
    site_area = grids.rows * grids.cols * grids.cell_area_m2
    rain = float(rainfall_mm) if rainfall_mm is not None else DEFAULT_RAINFALL_MM

    # --- axis 1: shade, from real shadow casting on each arrangement --- #
    def _shade(fps: list[Footprint]) -> float:
        vals = [shade_fraction(fps, grids, lat_deg, lon_deg, day_of_year, h)
                for h in sun_hours]
        return sum(v["shadeFraction"] for v in vals) / max(1, len(vals))

    shade_b, shade_c = _shade(base_fp), _shade(cand_fp)

    # --- axis 2: ventilation, frontal area density, 16 directions --- #
    fad_b = frontal_area_density(base_fp, site_area)
    fad_c = frontal_area_density(cand_fp, site_area)
    if "error" in fad_b or "error" in fad_c:
        return {"error": fad_b.get("error", "frontal area density failed")}

    # --- axis 3: drainage, the storage ledger on the modified class grid --- #
    grid_c, iv_notes = _land_class_after(grids, interventions, grids.cell_area_m2)
    led_b = storage_ledger(grids, grids.land_class, rain)
    led_c = storage_ledger(grids, grid_c, rain)

    # Rainfall is a *forcing*, not a scheme: it changes the world both schemes are
    # measured in, so it moves the absolute level and never the delta. Comparing
    # 550 mm against a 450 mm baseline inside the same axes would conflate "the
    # scheme did this" with "the climate did this", so it is reported separately.
    forcing: dict[str, Any] | None = None
    if rainfall_mm is not None:
        at_default = storage_ledger(grids, grids.land_class, DEFAULT_RAINFALL_MM)
        forcing = {
            "rainfallMm": rain,
            "baselineRainfallMm": DEFAULT_RAINFALL_MM,
            "unstoredAtBaselineRainfallM3": round(at_default["unstoredM3"], 1),
            "unstoredAtScenarioRainfallM3": round(led_b["unstoredM3"], 1),
            "deltaM3": round(led_b["unstoredM3"] - at_default["unstoredM3"], 1),
            "note": ("A change in the forcing, not in the scheme. It shifts both "
                     "the baseline and the candidate equally, so it does not "
                     "appear in any axis delta. Reported here so the effect is "
                     "visible rather than looking like nothing happened."),
        }

    axes = {
        "shade": {
            **_axis("shade", shade_b, shade_c),
            "method": ("Shadows cast from every envelope at two solar instants, "
                       "each footprint swept along the sun vector."),
            "limitation": ("Two instants, not a daily mean radiant temperature. "
                           "No surface energy balance, no air temperature."),
        },
        "ventilation": {
            **_axis("ventilation", fad_b["mean"], fad_c["mean"]),
            "baselineByDirection": fad_b["byDirection"],
            "candidateByDirection": fad_c["byDirection"],
            # Per prevailing direction (AVRG73: NE/ENE/E are 40.8% of the year),
            # so a massing change is visible directionally rather than only as a
            # mean. A ratio is a comparison, never a velocity ratio.
            "fadRatio": {
                d: fad_ratio(fad_b["byDirection"][d], fad_c["byDirection"][d], d)
                for d in ("NE", "ENE", "E")
            },
            "method": ("Frontal area density over the AVA method's sixteen wind "
                       "directions, the mean across all of them."),
            "limitation": (
                "Geometry, not flow, and not a velocity ratio. Every footprint is "
                "an axis-aligned rectangle, so the result is symmetric about the "
                "cardinal axes and cannot distinguish a north-easterly from a "
                "south-westerly -- and the published prevailing wind here is from "
                "the north-east."),
        },
        "drainage": {
            **_axis("drainage", led_b["unstoredM3"], led_c["unstoredM3"], 1),
            "baselineRunoffM3": round(led_b["runoffM3"], 1),
            "baselineGreenAreaM2": round(led_b["greenAreaM2"], 1),
            "baselinePavedAreaM2": round(led_b["pavedAreaM2"], 1),
            "candidateGreenAreaM2": round(led_c["greenAreaM2"], 1),
            "rainfallMm": rain,
            "method": ("The project's own storage ledger: SCS-CN runoff from "
                       "USDA TR-55 table 2-2, retention by land class, and the "
                       "unstored remainder summed over the 5 m grid."),
            "limitation": (
                "Responds to land cover and rainfall, NOT to building height. A "
                "footprint is already impervious whatever its height, so raising "
                "every building leaves this axis exactly unchanged -- which is "
                "arithmetic, not a bug. Interventions are what move it."),
            "interventions": iv_notes,
        },
    }

    tradeoffs = [
        f"{a['moreOrLess']}: {a['axis']} {_fmt(a['baseline'])} -> "
        f"{_fmt(a['candidate'])} ({_signed(a['delta'])} {a['units']})"
        for a in axes.values()
    ]

    return {
        "scenario": {
            "massingOps": [
                {k: v for k, v in o.__dict__.items() if v is not None}
                for o in ops
            ],
            "interventionCount": len(interventions),
            "rainfallMm": rain,
            "placedCount": len(cand_fp) - len(base_fp) if len(cand_fp) > len(base_fp) else 0,
            "placedCountNote": (
                "New blocks generated by the AI planner's own placement generator "
                "(ESA WorldCover paved class, existing envelopes excluded). A "
                "positive count here means the candidate adds massing; the "
                "baseline never loses it."
                if len(cand_fp) != len(base_fp) else
                "No blocks placed or removed; the scheme edits existing massing."
                if len(cand_fp) == len(base_fp) else
                "Fewer footprints than the baseline (reset or a move out of bounds)."
            ),
            "sunHours": list(sun_hours),
            "dayOfYear": day_of_year,
        },
        "massing": {
            "baselineCount": len(base_fp),
            "candidateCount": len(cand_fp),
            "baselineMeanHeightM": round(
                sum(f.height_m for f in base_fp) / max(1, len(base_fp)), 2),
            "candidateMeanHeightM": round(
                sum(f.height_m for f in cand_fp) / max(1, len(cand_fp)), 2),
        },
        "axes": axes,
        "forcing": forcing,
        "tradeoffs": tradeoffs,
        "combinedScore": None,
        "whyNoCombinedScore": (
            "Three reasons, and the third is the important one. (1) The units are "
            "not commensurable -- a fraction of ground, a dimensionless blockage "
            "and cubic metres -- so any weights that make them add are arbitrary "
            "and the answer would be a statement about the weights. (2) The "
            "ventilation axis has no threshold to normalise against: the AVA "
            "policy behind Hong Kong's guidance states that whether the better "
            "option meets a standard cannot be known. (3) The axes genuinely "
            "conflict -- raising heights shades more ground and blocks more wind "
            "-- and a single number would settle that trade-off silently, in "
            "whichever direction the weights happened to lean. Use the three axes, "
            "and see compare() if you want an ordering with stated weights."
        ),
        "unsupportedOps": [],
        "supportedOps": ["scale_all", "set_site", "rotate", "move", "place", "reset"],
        "opsNote": (
            "All six operations are scoreable as of 9 Oct 2026. `place` calls the "
            "AI planner's own placement generator (same function, same block "
            "pitch, same occupied-cell exclusion -- no second implementation), and "
            "`reset` restores the generated baseline exactly as the viewer's "
            "reset does, discarding every earlier op in the sequence. A `place` "
            "that finds no unbuilt paved land in scope places nothing and reports "
            "placedCount 0 rather than inventing a block."
        ),
    }


def _fmt(v: float) -> str:
    return f"{v:,.4g}"


def _signed(v: float) -> str:
    return f"{v:+,.4g}"


# --------------------------------------------------------------------------- #
# Stage 3 -- comparison under stated weights
# --------------------------------------------------------------------------- #

# The direction of a higher FAVOURABILITY score, per axis. Shade is the heat
# axis's solar lever (more shade = less solar gain = the better direction for
# heat); ventilation FAD and drainage unstored volume are lower-is-better. Stated
# once, echoed in every compare() response, so a reader can disagree with the
# convention explicitly rather than inferring it.
#
# An earlier version of the equivalent table elsewhere in this project had the
# ventilation case inverted -- it read a *rise* in frontal area density as "less
# blockage". The direction is stated once, here, and never re-derived.
AXIS_BETTER: dict[str, int] = {"shade": +1, "ventilation": -1, "drainage": -1}

_AXIS_POLARITY: dict[str, str] = {
    "shade": "higher is better",
    "ventilation": "lower is better",
    "drainage": "lower is better",
}

COMPARE_STATUS = "RELATIVE_RANKING_NOT_A_PASS_FAIL"
COMPARE_WARNING = (
    "The weights are the caller's, not the project's. This is a relative "
    "ordering, not a pass/fail verdict."
)
_COMPARE_NORMALISATION = (
    "min-max within the compared set, oriented by polarity; the set is the "
    "caller's, so adding a scheme can move the normalisation"
)
_COMPARE_WHY_NO_VERDICT = (
    "The AVA policy behind Hong Kong's guidance states that whether the better "
    "design option meets a standard cannot be known, so an ordering is the "
    "strongest claim this tool can make. It is relative to the compared set and "
    "to the weights the caller chose."
)


def _normalise_weights(
    weights: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """``(as_supplied, normalised_by_sum)``. Validates, never widens.

    Rejects an unknown axis name, a negative weight and an all-zero vector with
    ``ValueError`` -- mirroring the :func:`_region_fraction` scope-refusal
    precedent. Weights need not sum to 1; they are normalised by their sum, and
    BOTH vectors are returned so "the weights are the caller's" is checkable. A
    zero-weight axis stays in the normalised vector (as 0.0) so it remains visible
    on every row while influencing nothing.
    """
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights must be a non-empty mapping of axis -> number")
    as_supplied: dict[str, float] = {}
    for axis, raw in weights.items():
        if axis not in AXIS_UNITS:
            raise ValueError(
                f"unknown axis {axis!r}; known axes are {sorted(AXIS_UNITS)}")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"weight for {axis!r} is not a number: {raw!r}")
        if value < 0.0:
            raise ValueError(
                f"negative weight {value} for axis {axis!r}; weights must be "
                "non-negative")
        as_supplied[axis] = value
    total = float(sum(as_supplied.values()))
    if total <= 0.0:
        raise ValueError("all weights are zero; there is nothing to order by")
    normalised = {axis: value / total for axis, value in as_supplied.items()}
    return as_supplied, normalised


def _read_scenarios(
    scenarios: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, float]]]:
    """Validate and pull ``(ids, per-axis candidate values)`` out of the inputs.

    Each item is an ``evaluate()`` result carrying an id (``scenarioId`` or
    ``id``) and ``axes`` with a per-axis ``candidate``. A missing id or a missing
    candidate is fatal rather than skipped: ranking a scenario whose axes are
    absent would silently drop it from the comparison.
    """
    if not scenarios:
        raise ValueError("compare() needs at least one scenario")
    ids: list[str] = []
    values: list[dict[str, float]] = []
    for item in scenarios:
        if not isinstance(item, dict):
            raise ValueError("every scenario must be a dict")
        sid = item.get("scenarioId") or item.get("id")
        if sid is None:
            raise ValueError("every scenario needs a scenarioId (or id)")
        axes = item.get("axes")
        if not isinstance(axes, dict) or not axes:
            raise ValueError(f"scenario {sid!r} has no axes")
        row: dict[str, float] = {}
        for axis in AXIS_UNITS:
            block = axes.get(axis)
            if not isinstance(block, dict) or "candidate" not in block:
                raise ValueError(
                    f"scenario {sid!r} is missing axes[{axis!r}]['candidate']")
            row[axis] = float(block["candidate"])
        ids.append(str(sid))
        values.append(row)
    return ids, values


def _min_max_favourability(
    values: list[dict[str, float]],
) -> dict[str, list[float]]:
    """Per-axis 0-1 favourability, min-max within the set, oriented by polarity.

    When every scenario has the same value on an axis the range is zero, so there
    is nothing to discriminate: every scenario gets 1.0, which leaves the ordering
    unchanged (a constant contribution). This is stated rather than left to a
    division by zero.
    """
    fav: dict[str, list[float]] = {axis: [] for axis in AXIS_UNITS}
    for axis in AXIS_UNITS:
        col = [row[axis] for row in values]
        lo, hi = min(col), max(col)
        span = hi - lo
        for value in col:
            if span <= 1e-12:
                fav[axis].append(1.0)
                continue
            raw = (value - lo) / span
            fav[axis].append(raw if AXIS_BETTER[axis] > 0 else 1.0 - raw)
    return fav


def compare(
    scenarios: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Rank scenarios against each other. Refuses to rank without weights.

    Each item of ``scenarios`` is an ``evaluate()`` result dict carrying an id
    (``scenarioId`` or ``id``) and ``axes`` with a per-axis ``candidate`` value.
    All scenarios MUST share one baseline (the endpoint guarantees this by
    evaluating them all against the same city model); this is a stated
    precondition, echoed as ``sharedBaseline``.

    ``weights=None`` returns ``refused: True`` and ranks nothing. With weights,
    returns ``ranked`` rows (each carrying the three raw axes, the per-axis
    favourabilities, the contributions and the weighted favourability), a full
    per-axis ``byAxis`` ordering, and both weight vectors echoed.

    ``combinedScore`` is ``None`` on every path and there is no ``verdict``: the
    axes stay three, and the weighted total is named ``weightedFavourability``,
    never a score.

    Raises ``ValueError`` on an unknown axis name, a negative weight, an all-zero
    weight vector, or a scenario missing an id or ``axes``.
    """
    ids, values = _read_scenarios(scenarios)
    echoed = [
        {"scenarioId": sid, "axes": {axis: values[i][axis] for axis in AXIS_UNITS}}
        for i, sid in enumerate(ids)
    ]

    if weights is None:
        return {
            "refused": True,
            "ranked": None,
            "byAxis": None,
            "reason": (
                "No weights supplied. Ranking three axes with incompatible units "
                "requires the caller to state what they value; without that, any "
                "ordering would be a statement about the weights the tool invented, "
                "not about the schemes."
            ),
            "scenarios": echoed,
            "combinedScore": None,
            "verdict": None,
            "whyNoVerdict": _COMPARE_WHY_NO_VERDICT,
            "status": COMPARE_STATUS,
            "warning": COMPARE_WARNING,
        }

    as_supplied, used = _normalise_weights(weights)
    fav = _min_max_favourability(values)

    # Per-axis ordering, best -> worst. Ties break on scenarioId so the order does
    # not depend on the caller's list order.
    by_axis: dict[str, list[str]] = {}
    per_axis_rank: dict[str, dict[str, int]] = {axis: {} for axis in AXIS_UNITS}
    for axis in AXIS_UNITS:
        order = sorted(range(len(ids)),
                       key=lambda i: (-fav[axis][i], ids[i]))
        by_axis[axis] = [ids[i] for i in order]
        for rank, i in enumerate(order, start=1):
            per_axis_rank[axis][ids[i]] = rank

    rows: list[dict[str, Any]] = []
    for i, sid in enumerate(ids):
        contributions = {axis: used.get(axis, 0.0) * fav[axis][i]
                         for axis in AXIS_UNITS}
        weighted = sum(contributions.values())
        rows.append({
            "scenarioId": sid,
            "axes": {axis: values[i][axis] for axis in AXIS_UNITS},
            "favourability": {axis: round(fav[axis][i], 6) for axis in AXIS_UNITS},
            "contributions": {axis: round(contributions[axis], 6)
                              for axis in AXIS_UNITS},
            "weightedFavourability": round(weighted, 6),
            "perAxisRank": {axis: per_axis_rank[axis][sid] for axis in AXIS_UNITS},
        })
    rows.sort(key=lambda r: (-r["weightedFavourability"], r["scenarioId"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "refused": False,
        "reason": (
            "Ranked by a weighted favourability computed from each axis min-max "
            "normalised within this set. The total is the caller's opinion, named "
            "weightedFavourability and never a score; the three raw axes stay "
            "visible on every row."
        ),
        "weightsAsSupplied": as_supplied,
        "weightsUsed": used,
        "weightsNormalisedBySum": True,
        "weightsAreCallersOpinion": True,
        "polarity": dict(_AXIS_POLARITY),
        "normalisation": _COMPARE_NORMALISATION,
        "sharedBaseline": True,
        "scenarioCount": len(ids),
        "ranked": rows,
        "byAxis": by_axis,
        "combinedScore": None,
        "verdict": None,
        "whyNoVerdict": _COMPARE_WHY_NO_VERDICT,
        "status": COMPARE_STATUS,
        "warning": COMPARE_WARNING,
    }
