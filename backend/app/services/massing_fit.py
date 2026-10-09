"""The deterministic half of Option A: score candidate changes against the ledger.

:mod:`app.services.sandbox_model` knows what the ground is. This module decides
what to *do* about it -- and it does so by re-running the project's own storage
ledger over a modified land-class grid, never by inventing a score.

The split matters more than the code. The language model states an objective and
constraints; this file enumerates concrete, physically meaningful edits, measures
each one through the same arithmetic that produced the wet grid, and returns the
one that wins under the published weights. Every number in the response is a
delta the model actually produced, which is the only reason a judge should
believe any of it.

**Height is deliberately not a flood lever.** A taller building does not flood
less. Changing height moves shade and exposure and nothing else, so
:func:`height_options` reports it with an explicit ``floodDeltaM3: 0.0`` rather
than leaving the field out where a reader might assume it was overlooked -- or,
worse, assume a flood benefit that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.sandbox_model import (
    BUILDING_EXPOSURE_M2_PER_M,
    BUILDING_SHADE_M_PER_M,
    DEFAULT_RAINFALL_MM,
    LAND_CLASSES,
    BuildingContext,
    Grids,
    storage_ledger,
)

# What the caller may ask for. An open-ended "make it better" would be a licence
# for arbitrary scoring, so the objectives are named and their weights published
# here rather than buried in a prompt where nobody would check them.
OBJECTIVES: dict[str, dict[str, float]] = {
    "reduce_flood":  {"flood": 1.00, "heat": 0.00, "green": 0.35},
    "reduce_runoff": {"flood": 0.60, "heat": 0.00, "green": 0.35},
    "reduce_heat":   {"flood": 0.00, "heat": 1.00, "green": 0.35},
    "balanced":      {"flood": 0.50, "heat": 0.50, "green": 0.35},
}
DEFAULT_OBJECTIVE = "balanced"

# Green enters the cost as a negative, at a weight comparable to flood and heat:
# a scheme that solves flooding by paving the green space has not solved
# anything. That weight is an editorial judgement, not a derived quantity, and it
# is stated in every response so a reader can argue with it.
GREEN_WEIGHT_NOTE = (
    "The green weight is an editorial choice, not a derived quantity, and is "
    "published so it can be challenged."
)


@dataclass(frozen=True)
class Constraint:
    """The limits the caller sets. Defaults are the conservative ones."""

    no_net_green_loss: bool = True
    min_green_share_100m: float = 0.0
    allow_height_change: bool = False
    min_height_m: float = 10.0
    max_height_m: float = 120.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Constraint":
        raw = raw or {}
        c = cls()
        return cls(
            no_net_green_loss=bool(raw.get("no_net_green_loss",
                                           c.no_net_green_loss)),
            min_green_share_100m=float(raw.get("min_green_share_100m",
                                                c.min_green_share_100m)),
            allow_height_change=bool(raw.get("allow_height_change",
                                             c.allow_height_change)),
            min_height_m=float(raw.get("min_height_m", c.min_height_m)),
            max_height_m=float(raw.get("max_height_m", c.max_height_m)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "noNetGreenLoss": self.no_net_green_loss,
            "minGreenShare100m": self.min_green_share_100m,
            "allowHeightChange": self.allow_height_change,
            "minHeightM": self.min_height_m,
            "maxHeightM": self.max_height_m,
        }


def _candidate_edits() -> list[dict[str, Any]]:
    """The concrete changes considered, each a real land-class edit.

    Every entry is a *surface* change, because that is the only thing that moves
    the district's water balance. Height is not in this list and cannot be: a
    building's own footprint contributes runoff whether it is 10 m or 100 m tall.
    """
    paved = LAND_CLASSES.index("paved")
    green = LAND_CLASSES.index("green")
    return [
        {"id": "no_change", "label": "Leave it as it is",
         "target": None, "from": None, "to": None},
        {"id": "green_roof",
         "label": "Green the footprint (roof or podium garden)",
         "target": "footprint", "from": paved, "to": green},
        {"id": "perimeter_green",
         "label": "Green a 10 m perimeter around it",
         "target": "perimeter", "from": paved, "to": green},
        {"id": "site_to_green",
         "label": "Clear the footprint and its10 m surround to green",
         "target": "site", "from": paved, "to": green},
        {"id": "pave_the_green",
         "label": "Pave the surrounding green (the do-nothing default)",
         "target": "perimeter", "from": green, "to": paved},
    ]


def score_candidates(
    grids: Grids,
    ctx: BuildingContext,
    ring: list[list[float]],
    objective: str = DEFAULT_OBJECTIVE,
    constraints: dict[str, Any] | None = None,
    rainfall_mm: float = DEFAULT_RAINFALL_MM,
) -> dict[str, Any]:
    """Score every candidate by re-running the real ledger, then pick a winner."""
    weights = OBJECTIVES.get(objective) or OBJECTIVES[DEFAULT_OBJECTIVE]
    cons = Constraint.from_dict(constraints)

    cells = grids.cells_of_ring(ring)
    if not cells:
        return {"error": "building has no resolvable footprint cells"}

    row, col = grids.cell_of(ctx.lon, ctx.lat)
    # The search ring has to start *outside* the building. The first version used a
    # fixed 10 m radius, which for a 35 m envelope is entirely inside its own
    # footprint -- so "green the perimeter" silently changed zero cells and looked
    # like a candidate that did nothing. The ring is now the building's own
    # half-extent plus the margin.
    half = max(1, max(abs(r - row) for r, _ in cells), max(abs(c - col) for _, c in cells))
    margin = grids.radius_cells(10.0)
    reach = half + margin
    inside = set(cells)
    ring_cells = [
        (r, c)
        for r in range(max(0, row - reach), min(grids.rows, row + reach + 1))
        for c in range(max(0, col - reach), min(grids.cols, col + reach + 1))
        if (r, c) not in inside
    ]
    perimeter = ring_cells
    site = cells + ring_cells

    base = storage_ledger(grids, grids.land_class, rainfall_mm)
    paved_area = base["pavedAreaM2"]

    scored: list[dict[str, Any]] = []
    for cand in _candidate_edits():
        if cand["target"] == "footprint":
            targets = cells
        elif cand["target"] == "perimeter":
            targets = perimeter
        elif cand["target"] == "site":
            targets = site
        else:
            targets = []

        # A copy, every time. Mutating grids.land_class in place would let one
        # request silently become the next request's baseline.
        trial = grids.land_class.copy()
        changed = 0
        if cand["from"] is not None and targets:
            for r, c in targets:
                if int(trial[r, c]) == cand["from"]:
                    trial[r, c] = cand["to"]
                    changed += 1

        led = storage_ledger(grids, trial, rainfall_mm)

        d_unstored = led["unstoredM3"] - base["unstoredM3"]
        d_runoff = led["runoffM3"] - base["runoffM3"]
        d_green = led["greenAreaM2"] - base["greenAreaM2"]
        # The heat index is a 0-1 mean over the whole grid, so a change in a
        # handful of cells moves it very little. Scaling by paved area puts it on
        # a comparable footing to a volume in m3, rather than comparing 1e-6 with
        # 1e5 and concluding the flood term is the only one that matters.
        d_heat = led["heatMean"] - base["heatMean"]

        cost = (
            weights["flood"] * (-d_unstored)
            + weights["heat"] * (-d_heat * paved_area)
            + weights["green"] * (-d_green)
        )

        breaches: list[str] = []
        if cons.no_net_green_loss and d_green < -1.0:
            breaches.append(
                f"loses {abs(d_green):,.0f} m2 of green, and "
                f"'no_net_green_loss' is set")
        if (cons.min_green_share_100m
                and ctx.green_share_100m < cons.min_green_share_100m):
            breaches.append(
                f"site green share {ctx.green_share_100m:.2f} is already under the "
                f"required {cons.min_green_share_100m:.2f}; no candidate here "
                f"raises it, they only remove surface")
        if (not cons.allow_height_change
                and not (cons.min_height_m <= ctx.height_m <= cons.max_height_m)):
            breaches.append(
                f"current height {ctx.height_m:.1f} m is outside the permitted "
                f"{cons.min_height_m:.0f}-{cons.max_height_m:.0f} m and height "
                f"changes are not allowed")

        scored.append({
            "id": cand["id"],
            "label": cand["label"],
            "cellsChanged": changed,
            "deltaUnstoredM3": round(d_unstored, 1),
            "deltaRunoffM3": round(d_runoff, 1),
            "deltaGreenAreaM2": round(d_green, 1),
            "deltaHeatMean": round(d_heat, 8),
            "cost": round(cost, 2),
            "feasible": not breaches,
            "constraintBreaches": breaches,
        })

    feasible = [s for s in scored if s["feasible"]]
    best = min(feasible, key=lambda s: s["cost"]) if feasible else None

    # How much of the district's problem this building actually is.
    #
    # One 1,225 m2 envelope in a 7.9 km2 study area moves the unstored volume by
    # hundredths of a percent. That is not a rounding artefact and it is not
    # fixed by a cleverer search -- it is arithmetic. Reporting the share means
    # the panel cannot accidentally present a single building's fix as though it
    # solved the district, and it points at the honest use: aggregate over a
    # block or a scenario, not one tower.
    best_delta = next((abs(c["deltaUnstoredM3"]) for c in scored
                       if best and c["id"] == best["id"]), 0.0)
    district_share = (best_delta / base["unstoredM3"]) if base["unstoredM3"] else 0.0

    return {
        "bestChangeShareOfDistrict": round(district_share, 8),
        "scaleNote": (
            f"The best candidate changes the district's unstored volume by "
            f"{best_delta:,.0f} m3, which is {district_share * 100:.4f}% of the "
            f"{base['unstoredM3']:,.0f} m3 total. A single building cannot move a "
            f"{base['unstoredM3']:,.0f} m3 problem, and no search will change "
            f"that. This tool is for choosing what to do to *one* building on its "
            f"merits; district-scale claims need the change aggregated across many "
            f"buildings, which is what a scenario run does."
        ),
        "objective": objective if objective in OBJECTIVES else DEFAULT_OBJECTIVE,
        "weights": weights,
        "constraints": cons.to_dict(),
        "rainfallMm": rainfall_mm,
        "baseline": {k: round(v, 3) for k, v in base.items()},
        "candidates": scored,
        "candidatesEvaluated": len(scored),
        "feasibleCount": len(feasible),
        "recommended": best["id"] if best else None,
        "note": (
            "Every delta here is the project's own storage ledger re-run over a "
            "modified land-class grid, not a score invented for the purpose. "
            "Depth is not re-solved -- it is allocated from the resulting volume "
            "-- so these are volume comparisons and not inundation extents. "
            + GREEN_WEIGHT_NOTE
        ),
    }


def height_options(
    ctx: BuildingContext,
    constraints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Height alternatives, reported separately and explicitly.

    Shade and exposed surface scale with height; the flood term does not move at
    all. Reporting ``floodDeltaM3: 0.0`` rather than omitting the field is the
    point: an absent field reads as an oversight, and this one must not read as
    either an oversight or a benefit.
    """
    cons = Constraint.from_dict(constraints)
    wanted = [
        cons.min_height_m,
        ctx.height_m * 0.85,
        ctx.height_m,
        ctx.height_m * 1.15,
        cons.max_height_m,
    ]
    out: list[dict[str, Any]] = []
    seen: set[float] = set()
    for raw in wanted:
        h = round(max(cons.min_height_m, min(cons.max_height_m, raw)), 1)
        if h in seen:
            continue
        seen.add(h)
        out.append({
            "heightM": h,
            "shadeM": round(h * BUILDING_SHADE_M_PER_M, 2),
            "exposureM2": round(h * BUILDING_EXPOSURE_M2_PER_M, 1),
            "floodDeltaM3": 0.0,
            "floodNote": "height does not change runoff or storage",
            "isCurrent": abs(h - ctx.height_m) < 0.05,
        })
    return out
