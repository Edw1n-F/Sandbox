"""Tests for the Option A context join and the deterministic candidate scorer.

The most important test here is not about the scorer at all: it is
`test_the_ledger_reproduces_the_script`, which pins this module's arithmetic to
the arithmetic `make_wetgrid.py` already ran. Without it, every delta the scorer
reports could be computed slightly differently from the overlays the project
publishes, and the tool would be confidently measuring a model of the model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.massing_fit import (  # noqa: E402
    DEFAULT_OBJECTIVE,
    OBJECTIVES,
    Constraint,
    height_options,
    score_candidates,
)
from app.services.sandbox_model import (  # noqa: E402
    LAND_CLASSES,
    context_for,
    load_grids,
    storage_ledger,
)

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {label}{f' -- {detail}' if detail else ''}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}{f' -- {detail}' if detail else ''}")


def main() -> int:
    grids = load_grids()
    check("the rasters load", grids is not None)
    if grids is None:
        print("\n0/1 checks passed")
        return 1

    model = BACKEND_DIR / "data" / "rasters" / "buildings_hsk.json"
    features = json.loads(model.read_text())["features"]
    check("the city model has buildings", len(features) > 2000, f"{len(features)}")

    # --- the ledger must agree with the script that generated the overlays --- #
    sidecar = json.loads(
        (BACKEND_DIR / "data" / "rasters" / "wetgrid_hsk.json").read_text())
    res = sidecar["results"]
    led = storage_ledger(grids, grids.land_class)
    check("the ledger reproduces the script's runoff volume exactly",
          abs(led["runoffM3"] - res["runoffVolumeM3"]) < 1.0,
          f"{led['runoffM3']:,.1f} vs {res['runoffVolumeM3']:,.1f}")
    check("and its unstored volume exactly",
          abs(led["unstoredM3"] - res["unstoredVolumeM3"]) < 1.0,
          f"{led['unstoredM3']:,.1f} vs {res['unstoredVolumeM3']:,.1f}")
    check("and it serves the same headline volume",
          abs(grids.unstored_m3 - res["unstoredVolumeM3"]) < 1.0,
          f"{grids.unstored_m3:,.1f}")

    # --- the scorer must not mutate the shared grid --- #
    before = grids.land_class.copy()
    feature = features[900]
    ring = feature["geometry"]["coordinates"][0]
    ctx = context_for(grids, feature["properties"]["id"], ring,
                      feature["properties"])
    score_candidates(grids, ctx, ring)
    check("scoring leaves the shared land-class grid untouched",
          bool((before == grids.land_class).all()),
          "a mutation here makes one request the next request's baseline")

    # --- context --- #
    check("context carries a land class", ctx.land_class in LAND_CLASSES,
          ctx.land_class)
    check("context carries a curve number for it", ctx.curve_number > 0,
          f"CN {ctx.curve_number}")
    check("context reads a flood depth from the solved field",
          ctx.flood_depth_m >= 0.0, f"{ctx.flood_depth_m:.3f} m")
    check("the footprint resolves to whole 5 m cells, not a centroid",
          ctx.footprint_cells > 1, f"{ctx.footprint_cells} cells")
    check("the coarse DEM is labelled as coarse",
          any("90 m" in n for n in ctx.notes))
    check("every context record states its own screening limits",
          any("Screening only" in n for n in ctx.notes))

    # --- candidates --- #
    result = score_candidates(grids, ctx, ring, objective="reduce_flood")
    check("candidates are scored", result["candidatesEvaluated"] >= 4,
          f"{result['candidatesEvaluated']}")
    check("a recommendation comes back", result["recommended"] is not None,
          str(result["recommended"]))

    green = next(c for c in result["candidates"] if c["id"] == "green_roof")
    check("greening the footprint reduces the unstored volume",
          green["deltaUnstoredM3"] < 0, f"{green['deltaUnstoredM3']:,.0f} m3")
    check("and adds green area", green["deltaGreenAreaM2"] > 0,
          f"{green['deltaGreenAreaM2']:,.0f} m2")
    check("greening the footprint is not a no-op", green["cellsChanged"] > 0,
          f"{green['cellsChanged']} cells")

    ordered = [c for c in result["candidates"]
               if c["id"] in ("green_roof", "perimeter_green", "site_to_green")]
    # cost is a penalty, so a *better* option has the *lower* number. The first
    # version of this assertion had the comparison backwards and failed on
    # correct behaviour.
    check("bigger green interventions score strictly better",
          all(a["cost"] > b["cost"] for a, b in zip(ordered, ordered[1:])),
          " > ".join(f"{c['id']}={c['cost']:,.0f}" for c in ordered))
    check("the district-scale limit is stated, not buried",
          result["bestChangeShareOfDistrict"] < 0.01,
          f"{result['bestChangeShareOfDistrict'] * 100:.4f}% of the district")

    # --- constraints --- #
    # Find a building that actually has green within reach. The first building
    # tried sits in a wholly paved block, so "pave the surrounding green" had
    # nothing to pave and the check passed without testing anything.
    with_green = None
    for f in features:
        r = f["geometry"]["coordinates"][0]
        c2 = context_for(grids, f["properties"]["id"], r, f["properties"])
        sc = score_candidates(grids, c2, r, objective="reduce_heat",
                              constraints={"no_net_green_loss": False})
        pv = next(c for c in sc["candidates"] if c["id"] == "pave_the_green")
        if pv["cellsChanged"] > 0:
            with_green = (f, c2, sc, pv)
            break
    check("some building has green close enough to pave",
          with_green is not None,
          f"tested {len(features)} buildings")
    if with_green:
        _f, _c, _sc, pv = with_green
        check("paving green there loses green area and gains runoff",
              pv["deltaGreenAreaM2"] < 0 and pv["deltaRunoffM3"] > 0,
              f"{pv['cellsChanged']} cells, {pv['deltaGreenAreaM2']:,.0f} m2 green, "
              f"{pv['deltaRunoffM3']:+,.0f} m3 runoff")
        strict = score_candidates(grids, _c,
                                  _f["geometry"]["coordinates"][0],
                                  objective="reduce_heat")
        pv_strict = next(c for c in strict["candidates"]
                         if c["id"] == "pave_the_green")
        check("and no_net_green_loss blocks exactly that candidate",
              not pv_strict["feasible"],
              "; ".join(pv_strict["constraintBreaches"])[:70])

    blocked = score_candidates(grids, ctx, ring,
                               constraints={"min_green_share_100m": 0.99})
    check("an unreachable green minimum blocks every candidate",
          blocked["feasibleCount"] == 0,
          f"{blocked['feasibleCount']} feasible")

    # --- objectives and defaults --- #
    unknown = score_candidates(grids, ctx, ring, objective="make it nice")
    check("an unknown objective falls back rather than inventing weights",
          unknown["objective"] == DEFAULT_OBJECTIVE, unknown["objective"])
    check("every named objective publishes its weights",
          all(set(v) == {"flood", "heat", "green"} for v in OBJECTIVES.values()),
          ", ".join(sorted(OBJECTIVES)))
    check("the green weight is disclosed as a judgement",
          "editorial choice" in unknown["note"])

    # --- height is honestly not a flood lever --- #
    heights = height_options(ctx)
    check("height options are offered", len(heights) >= 3, f"{len(heights)}")
    check("no height option claims a flood benefit",
          all(h["floodDeltaM3"] == 0.0 for h in heights),
          "a taller building does not flood less")
    check("the current height is marked once",
          sum(1 for h in heights if h["isCurrent"]) == 1)
    check("shade increases with height",
          [h["shadeM"] for h in heights] == sorted(h["shadeM"] for h in heights))

    # --- constraints object --- #
    c = Constraint.from_dict({"max_height_m": 55, "no_net_green_loss": False})
    check("constraints parse from a loose dict",
          c.max_height_m == 55 and c.no_net_green_loss is False)
    check("and default to the conservative option",
          Constraint.from_dict(None).no_net_green_loss is True)

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
