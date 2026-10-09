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

    SUPPORTED = ("scale_all", "set_site", "rotate", "move", "place", "reset")

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
    """
    x0, y0, x1, y1 = bounds
    out = list(footprints)
    for op in ops:
        fx0, fy0, fx1, fy1 = _region_fraction(op.scope)
        lx0, ly0 = x0 + (x1 - x0) * fx0, y0 + (y1 - y0) * fy0
        lx1, ly1 = x0 + (x1 - x0) * fx1, y0 + (y1 - y0) * fy1
        idx = [i for i, f in enumerate(out)
               if lx0 <= f.lon <= lx1 and ly0 <= f.lat <= ly1]

        if op.op == "scale_all":
            m = float(op.multiplier or 1.0)
            out = [Footprint(f.lon, f.lat, f.width_m, f.depth_m, f.height_m * m)
                   if i in set(idx) else f for i, f in enumerate(out)]
        elif op.op == "set_site":
            out = [
                Footprint(f.lon, f.lat, f.width_m, f.depth_m,
                          (max(1.0, f.height_m + float(op.delta_m))
                           if op.delta_m is not None
                           else float(op.height_m or f.height_m)))
                if i in set(idx) else f
                for i, f in enumerate(out)
            ]
        elif op.op == "rotate":
            deg = float(op.degrees or 0.0)
            if abs(deg) > 1e-9:
                rad = math.radians(deg)
                ca, sa = abs(math.cos(rad)), abs(math.sin(rad))
                out = [Footprint(
                    f.lon, f.lat,
                    f.width_m * ca + f.depth_m * sa,
                    f.width_m * sa + f.depth_m * ca,
                    f.height_m) if i in set(idx) else f
                    for i, f in enumerate(out)]
        elif op.op == "move":
            dlon = float(op.dx_m or 0.0) / (111_320.0 * math.cos(math.radians(f_lat)))
            dlat = float(op.dy_m or 0.0) / 110_540.0
            if abs(float(op.dx_m or 0.0)) > 1e-9 or abs(float(op.dy_m or 0.0)) > 1e-9:
                out = [Footprint(f.lon + dlon, f.lat + dlat, f.width_m, f.depth_m,
                                 f.height_m) if i in set(idx) else f
                       for i, f in enumerate(out)]
        # `place` and `reset` are deliberately not implemented here. Placing new
        # blocks means generating footprints on unbuilt paved land, and resetting
        # means regenerating the seeded heights; both belong to the city-model
        # generator, and a second implementation here would drift from it. The
        # honest answer is that this module cannot score them yet, so it says so
        # rather than approximating.
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
        "unsupportedOps": sorted({
            o.op for o in ops if o.op in ("place", "reset")
        }),
        "whyUnsupported": (
            "place needs new footprints generated on unbuilt paved land and reset "
            "needs the seeded heights back; both belong to the city-model "
            "generator. Reimplementing either here would drift from it, so this "
            "module reports them as unsupported rather than approximating."
        ),
    }


def _fmt(v: float) -> str:
    return f"{v:,.4g}"


def _signed(v: float) -> str:
    return f"{v:+,.4g}"
