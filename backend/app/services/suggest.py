"""The suggest step of the user intervention loop (P0.2 of the loop plan).

"Where could a project go, and what would it move?" -- answered from the study
area's own rasters, deterministically, with the arithmetic shown.

## What this is not

Not a recommendation, not a score, and not an AI. There is no model in this
loop and there never will be: a suggestion the user cannot reproduce is a
suggestion they cannot argue with, and the demo needs them to be able to. The
same query twice returns the same ranking, to the same number, forever.

## One axis at a time

Every candidate is ranked **within its axis** by that axis's own number:

===================  ==========================================
``drainage``         storage added (m3) + runoff avoided (m3)
``heat``             cells converted from hard to vegetated
``runoff``           runoff avoided (m3) on a curve-number change
===================  ==========================================

There is deliberately no combined score. The units are incommensurable and the
axes genuinely conflict -- the deep basin that stores the most water is the
paved ground that adds the most heat -- so an ordering across axes would be a
number nobody could defend. Comparing is allowed; grading is not.

## Where the numbers come from

The estimates are computed with the ledger's own constants and formulas, per
cell, over the candidate's true footprint: ``CURVE_NUMBER_HSG_D`` and
``runoff_depth`` for the runoff delta, ``STORAGE_DEPTH_M`` for storage. They
are **not** the level-pool allocation the run performs -- that is a whole-grid
solve, and quoting it per site would be a 1 s-per-site endpoint for a number
the run will produce anyway. The basis string on every estimate says so, and
the ``runContext`` carries the run's own deficit number beside them.

Nothing here is a verdict: whether any candidate is a good idea is what the
run is for.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.services import siting
from app.services.sandbox_model import Grids, load_grids

# The closed set of honesty labels this project uses: an UPPER_SNAKE string
# that says what the data *is*. This one means: sites read from measured
# rasters, ranked by per-cell arithmetic, screening level.
STATUS = "SITES_FROM_MEASURED_RASTERS"

# Axes in report order. No score, no ordering between them.
AXIS_ORDER = ("drainage", "flood", "heat", "runoff")

# Which pools belong to which axis, and the number each is ranked by.
_POOL_FOR_AXIS: dict[str, tuple[str, ...]] = {
    "drainage": ("water_reservoir", "underground_tank", "flood_storage"),
    "flood": ("water_reservoir",),
    "heat": ("green_space",),
    "runoff": ("permeable_paving", "underground_tank"),
}

_AXIS_BASIS = {
    "drainage": (
        "storage is footprint cells x the working depth the class (or the "
        "intervention's own storageDepthM) gives; runoff avoided is the "
        "SCS-CN depth change per cell, summed over the footprint"
    ),
    "flood": (
        "flood-risk ratio R = direct upstream inflow (m3) / raw-DEM slope (m/m): "
        "where a convergence meets a flat bed the water collects and over-tops; "
        "each face is ranked by its summed inflow over its mean slope"
    ),
    "heat": (
        "cells converted from hard standing to vegetated ground, with the "
        "relative heat index moving from the hard value to the vegetated one"
    ),
    "runoff": (
        "runoff avoided is the SCS-CN depth change per cell, summed over the "
        "footprint; no land class changes, so heat does not move"
    ),
}

_WARNING = (
    "Every candidate is a parcel read from land cover, the wet grid and the "
    "surveyed building footprints at 5 m. None is a surveyed site with an "
    "owner or a works programme. Estimates are per-cell ledger arithmetic, "
    "not the level-pool allocation a run performs. Screening level."
)


def _class_of(grids: Grids, cells: list[tuple[int, int]]) -> str:
    """The dominant land class over a cell set -- the ledger's own rule."""
    from scripts._overlay_common import LAND_CLASSES

    counts: dict[str, int] = {}
    for r, c in cells:
        name = LAND_CLASSES[int(grids.land_class[r, c])]
        counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _cn_for(name: str) -> float:
    from scripts._overlay_common import CURVE_NUMBER_HSG_D

    return float(CURVE_NUMBER_HSG_D[name])


def _runoff_depth(rainfall_mm: float, cn: float) -> float:
    """SCS-CN runoff depth in mm (the ledger's own formula, copied not drifted).

    Q = (P - 0.2 S)^2 / (P + 0.8 S), S = (25400 / CN) - 254.
    The constant is **254**, not 2540 -- see ``_overlay_common.runoff_depth``,
    where the derivation is written out.
    """
    retention = (25400.0 / cn) - 254.0
    if rainfall_mm <= 0.2 * retention:
        return 0.0
    return ((rainfall_mm - 0.2 * retention) ** 2
            / (rainfall_mm + 0.8 * retention)) / 1000.0  # mm -> m


def _effect_for(kind: str) -> dict[str, Any]:
    """The land-cover effect this kind has, from the ledger's own table."""
    from app.services.sandbox_model import INTERVENTION_EFFECTS

    return INTERVENTION_EFFECTS.get(kind, {})


def _estimate(grids: Grids, site: dict[str, Any],
              rainfall_mm: float) -> dict[str, Any]:
    """What this candidate would move, in the axis's own units.

    Computed per cell over the candidate's true footprint with the ledger's
    constants: no simulation, no solver, no model. The ``basis`` string is what
    makes that visible to whoever reads the suggestion.
    """
    cells = site["cells"]
    effect = _effect_for(site["kind"])
    covered = site.get("coveredCellCount", len(cells))
    cell_area = grids.cell_area_m2

    # The class each covered cell becomes, and the class it is today.
    before = _class_of(grids, cells)
    after = effect.get("to") or before

    runoff_avoided_m3 = 0.0
    if effect.get("to") is not None and after != before:
        depth_before = _runoff_depth(rainfall_mm, _cn_for(before))
        depth_after = _runoff_depth(rainfall_mm, _cn_for(after))
        runoff_avoided_m3 = (depth_before - depth_after) * covered * cell_area
    elif "cn" in effect:
        # A curve-number-only intervention moves the paved and bare cells and
        # changes nothing else -- the same rule the ledger applies, so the
        # count below is the count of cells that will actually move.
        from scripts._overlay_common import LAND_CLASSES

        movable = [name for name in
                   (LAND_CLASSES[int(grids.land_class[r, c])] for r, c in cells)
                   if name in ("paved", "bare")]
        depth_before = _runoff_depth(rainfall_mm, _cn_for(before))
        depth_after = _runoff_depth(rainfall_mm, float(effect["cn"]))
        runoff_avoided_m3 = (depth_before - depth_after) * len(movable) * cell_area

    storage_added_m3 = 0.0
    if effect.get("to") is not None:
        storage_added_m3 = _storage_depth(site) * covered * cell_area

    # The flood axis ranks by *inflow alone*: totalInflowM3 is the water that
    # arrives at the site, and it is the direct source of flood risk. The slope
    # was already the *admission* gate (``siting.reservoir_candidates`` only
    # keeps patches under RESERVOIR_SLOPE_MAX), so dividing by it again would
    # only re-amplify a signal that has already done its filtering work. The
    # reservoir pool publishes ``totalInflowM3``; every other kind has none, so
    # the risk is None there and the flood axis (which only draws from the
    # reservoir pool) is the only place it is read.
    flood_risk = None
    if site.get("totalInflowM3") is not None:
        flood_risk = round(float(site["totalInflowM3"]), 1)

    return {
        "cellsCovered": covered,
        "cellsOfSite": len(cells),
        "areaHa": round(covered * cell_area / 10_000.0, 2),
        "classToday": before,
        "classAfter": after,
        # Signed, and named for what it is: an open water surface has
        # CN 100 in TR-55 (impervious), so converting paved ground to a
        # reservoir can *raise* the runoff depth while storing water.
        # Quoting a negative "avoided" would be a contradiction, so the
        # field is a delta and the sign means what it says.
        "runoffDeltaM3": round(runoff_avoided_m3, 1),
        "storageAddedM3": round(storage_added_m3, 1),
        "heatIndexDelta": round(_heat_delta(before, after), 3),
        "floodRisk": flood_risk,
        "basis": _AXIS_BASIS[
            "drainage" if site["kind"] in ("water_reservoir", "underground_tank",
                                           "flood_storage")
            else "heat" if site["kind"] == "green_space" else "runoff"
        ],
    }


def _storage_depth(site: dict[str, Any]) -> float:
    """The working depth a candidate would use, in metres.

    The class default, or the intervention's own ``storageDepthM`` when the
    candidate carries one -- the same precedence the ledger applies, so the
    number quoted here is the number the run would use.
    """
    from scripts._overlay_common import STORAGE_DEPTH_M

    params = site.get("params") or {}
    if "storageDepthM" in params:
        return float(params["storageDepthM"])
    effect = _effect_for(site["kind"])
    to = effect.get("to")
    if to and to in STORAGE_DEPTH_M:
        return float(STORAGE_DEPTH_M[to])
    return 0.0


def _heat_delta(before: str, after: str) -> float:
    """How far the relative heat index moves, signed (negative is cooler)."""
    from scripts._overlay_common import HEAT_INDEX

    return float(HEAT_INDEX.get(after, HEAT_INDEX.get(before, 0.0))
                 - HEAT_INDEX.get(before, 0.0))


def _params_for(site: dict[str, Any]) -> dict[str, Any]:
    """The per-intervention parameters a candidate carries, if any."""
    kind = site["kind"]
    if kind == "underground_tank":
        return {"storageDepthM": siting.TANK_DEPTH_M}
    if kind == "flood_storage":
        return {"storageDepthM": siting.FLOODABLE_DEPTH_M}
    if kind == "permeable_paving":
        return {"curveNumber": siting.PERMEABLE_CN}
    if kind == "water_reservoir":
        depth = site.get("depthM")
        if depth is not None:
            # Validated rather than trusted, exactly as the ledger does: a
            # 40 m depth over a hectare is not a reservoir. One predicate for
            # the range, so a candidate this pool offers can never be one the
            # run then refuses.
            from app.services.sandbox_model import depth_is_acceptable

            if depth_is_acceptable(depth):
                return {"storageDepthM": round(float(depth), 2)}
    return {}


def suggest(rainfall_mm: float = 450.0,
            max_per_axis: int = 3) -> dict[str, Any]:
    """Ranked candidates, one axis at a time, with the arithmetic shown.

    Deterministic: the pools are sorted by cell count (stable), each axis's
    ranking key is a number, and ties fall back to that same cell count, so no
    call ever depends on iteration order or on anything clock-shaped.
    """
    grids = load_grids()
    if grids is None:  # pragma: no cover - the rasters exist in this repo
        return {
            "status": "NO_LEDGER_GRID",
            "warning": "no wet grid: run scripts/make_wetgrid.py",
            "axes": [],
        }

    pools = siting.all_candidates()

    # Sites that need a depth (the reservoir candidates) carry it, so the
    # estimate and the ledger agree on the working depth.
    for site in pools.get("water_reservoir", []):
        site["depthM"] = _max_depth(site["cells"])

    from app.services.sandbox_model import apply_interventions

    @lru_cache(maxsize=64)
    def _probe(kind: str, geometry_json: str,
               params_json: str) -> tuple[bool, str]:
        """The ledger's verdict on one candidate, memoised by its geometry.

        A suggestion that the ledger would refuse is not a suggestion -- pin it
        and nothing happens, which is the silent no-op this loop exists to
        avoid. So each candidate goes through the real guard (25% for one
        intervention, 40% cumulative) before it is offered.

        Memoised because the same contour is offered under two kinds (the
        impervious block is both a tank site and a paving site) and because
        rasterising a 6,800-cell polygon is the most expensive thing this
        module does. The key is the geometry itself, so a changed contour is a
        new probe.
        """
        features = [{
            "id": "probe",
            "intervention_type": kind,
            "geometry": json.loads(geometry_json),
            "params": json.loads(params_json),
        }]
        applied, skipped = apply_interventions(grids, features)[3:5]
        if applied:
            return True, ""
        return False, (skipped[0].get("why") if skipped else "the ledger refused it")

    def pinnable(candidate: dict[str, Any]) -> tuple[bool, str]:
        intervention = candidate["plan"]["interventions"][0]
        return _probe(
            candidate["kind"],
            json.dumps(intervention["geometry"], sort_keys=True),
            json.dumps(intervention.get("params") or {}, sort_keys=True),
        )

    axes: list[dict[str, Any]] = []
    for axis in AXIS_ORDER:
        ranked: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for kind in _POOL_FOR_AXIS[axis]:
            for site in pools.get(kind, []):
                estimate = _estimate(grids, site, rainfall_mm)
                candidate = {
                    "kind": kind,
                    "site": {
                        "geometry": site["geometry"],
                        "centroid": site["centroid"],
                        "areaHa": site["areaHa"],
                        "cells": len(site["cells"]),
                        "evidence": site["evidence"],
                    },
                    "estimate": estimate,
                    # A ready-to-post plan payload: pinning a suggestion means
                    # creating exactly this, so the suggester never has to
                    # describe geometry in words.
                    "plan": {
                        "interventions": [{
                            "geometry": site["geometry"],
                            "intervention_type": kind,
                            "source": "manual",
                            "params": _params_for(site),
                            "style": _style_for(kind),
                        }],
                    },
                }
                applied_ok, why = pinnable(candidate)
                if applied_ok:
                    ranked.append(candidate)
                else:
                    refused.append({
                        "kind": kind,
                        "cells": len(site["cells"]),
                        "why": why,
                    })
        key = {
            "drainage": lambda c: (c["estimate"]["storageAddedM3"],
                                    c["estimate"]["runoffDeltaM3"],
                                    c["site"]["cells"]),
            "flood": lambda c: (c["estimate"]["floodRisk"] or 0.0,
                                c["site"]["cells"]),
            "heat": lambda c: (c["estimate"]["heatIndexDelta"],
                               c["site"]["cells"]),
            "runoff": lambda c: (c["estimate"]["runoffDeltaM3"],
                                 c["site"]["cells"]),
        }[axis]
        ranked.sort(key=key, reverse=True)
        axes.append({
            "axis": axis,
            "basis": _AXIS_BASIS[axis],
            "candidates": ranked[:max_per_axis],
            "candidatesAvailable": len(ranked),
            # Not hidden: a user who knows the area has one big paved block
            # should be told why it is not in the list.
            "candidatesRefused": refused,
        })

    return {
        "status": STATUS,
        "warning": _WARNING,
        "generatedFrom": {
            "landCover": "ESA WorldCover 10 m v200 (2021), CC BY 4.0",
            "wetGrid": "scripts/make_wetgrid.py, level-pool allocation",
            "buildings": "Open3dhk 3D Tiles fused with Overture Maps "
                         "2026-09-23.0",
            "deterministic": True,
        },
        "runContext": {
            "rainfallMm": round(float(rainfall_mm), 1),
            "siteCells": grids.rows * grids.cols,
            "siteAreaHa": round(grids.rows * grids.cols
                                 * grids.cell_area_m2 / 10_000.0, 1),
        },
        "axes": axes,
    }


def _max_depth(cells: list[tuple[int, int]]) -> float | None:
    depth = siting.basin_depth()
    if depth is None:
        return None
    return round(float(max(depth[r, c] for r, c in cells)), 2)


def _style_for(kind: str) -> dict[str, Any]:
    from app.services.ai_plan import STYLE_FOR_KIND

    return dict(STYLE_FOR_KIND.get(kind, {"color": "#8250df", "opacity": 0.6}))
