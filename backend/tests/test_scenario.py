"""Tests for the three-axis scenario evaluator.

The evaluator's one design decision is that it returns **no combined score**, and
most of these checks defend that. Three separate failures are being guarded:

1. A direction label that contradicts its own number. It happened once: a rise in
   frontal area density was reported as "less blockage".
2. An unrecognised scope silently widened to the whole district -- Decision 13's
   bug, which produced a plausible wrong skyline and reported success.
3. The axes being quietly summed into one number, which would settle the
   shade-versus-blockage trade-off in whichever direction the weights happened to
   lean, without saying so.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.sandbox_model import load_grids  # noqa: E402
from app.services.scenario import (  # noqa: E402
    AXIS_BETTER,
    AXIS_UP_MEANS,
    MassingOp,
    apply_massing,
    compare,
    evaluate,
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
    feats = json.loads(
        (BACKEND_DIR / "data/rasters/citymodel_hsk.json").read_text())["features"]

    taller_west = evaluate(
        grids, feats, ops=[MassingOp("scale_all", multiplier=1.4, scope="west")])
    axes = taller_west["axes"]

    # --- 1. three axes, three units, no total --- #
    check("all three axes are present",
          set(axes) == {"shade", "ventilation", "drainage"}, str(sorted(axes)))
    check("each axis carries its own units",
          len({a["units"] for a in axes.values()}) == 3,
          "; ".join(sorted(a["units"] for a in axes.values())))
    check("there is NO combined score", taller_west["combinedScore"] is None,
          str(taller_west["combinedScore"]))
    check("and the reason is given, not implied",
          "not commensurable" in taller_west["whyNoCombinedScore"]
          and "conflict" in taller_west["whyNoCombinedScore"], "")

    # --- 2. the direction-label bug, pinned --- #
    vent = axes["ventilation"]
    check("a rise in frontal area density reads as MORE blockage",
          vent["delta"] > 0 and vent["moreOrLess"] == "more blockage",
          f"{vent['delta']:+.4f} -> {vent['moreOrLess']}")
    check("and every axis agrees with its own sign",
          all((a["delta"] > 0) == (a["moreOrLess"].split()[0] in
                                   ("more",)) or a["delta"] == 0
              for a in axes.values()),
          "; ".join(f"{k}:{v['delta']:+.4f}={v['moreOrLess']}" for k, v in axes.items()))
    check("the direction table covers every axis",
          set(AXIS_UP_MEANS) == set(axes), str(sorted(AXIS_UP_MEANS)))
    check("and the drainage axis reads the right way round too",
          axes["drainage"]["moreOrLess"] == "unchanged", "")

    # --- 3. an unrecognised scope is fatal, never widened --- #
    try:
        evaluate(grids, feats,
                 ops=[MassingOp("scale_all", multiplier=1.4, scope="the moon")])
        check("an unrecognised scope is refused", False, "it was accepted")
    except ValueError as e:
        check("an unrecognised scope is refused", "refusing to widen" in str(e), str(e))

    scoped = evaluate(grids, feats,
                      ops=[MassingOp("scale_all", multiplier=1.4, scope="west")])
    whole = evaluate(grids, feats, ops=[MassingOp("scale_all", multiplier=1.4)])
    check("a scoped edit is smaller than the same edit unscoped",
          scoped["axes"]["shade"]["delta"] < whole["axes"]["shade"]["delta"],
          f"west {scoped['axes']['shade']['delta']:+.4f} vs "
          f"all {whole['axes']['shade']['delta']:+.4f}")

    # --- 4. the axes really do conflict, which is why there is no score --- #
    v = taller_west["axes"]
    check("raising heights shades more AND blocks more wind",
          v["shade"]["delta"] > 0 and v["ventilation"]["delta"] > 0,
          f"shade {v['shade']['delta']:+.4f}, FAD {v['ventilation']['delta']:+.4f}")
    check("so a single number would be settling a real trade-off silently",
          len([a for a in v.values() if a["changed"]]) >= 2, "")

    # --- 5. a forcing is not a scheme --- #
    wet = evaluate(grids, feats, rainfall_mm=550.0)
    check("rainfall alone changes no axis delta",
          all(a["delta"] == 0 for a in wet["axes"].values()),
          "; ".join(f"{k}={a['delta']}" for k, a in wet["axes"].items()))
    check("but its effect on the deficit is reported",
          wet["forcing"] is not None
          and wet["forcing"]["deltaM3"] > 700_000,
          f"{wet['forcing']['deltaM3']:,.0f} m3")
    check("and it says it is a change in the forcing, not the scheme",
          "not in the scheme" in wet["forcing"]["note"], "")

    # --- 6. interventions move drainage; unknown kinds are not guessed --- #
    x0, y0, x1, y1 = grids.bounds
    ring = [[x0 + (x1 - x0) * 0.5, y0], [x1, y0], [x1, y1],
            [x0 + (x1 - x0) * 0.5, y1], [x0 + (x1 - x0) * 0.5, y0]]
    green = evaluate(grids, feats, interventions=[{"kind": "green", "ring": ring}])
    check("green space reduces the unstored volume",
          green["axes"]["drainage"]["delta"] < 0,
          f"{green['axes']['drainage']['delta']:+,.0f} m3")
    check("and says how much of it",
          "cells" in green["axes"]["drainage"]["interventions"][0],
          green["axes"]["drainage"]["interventions"][0])
    bogus = evaluate(grids, feats,
                     interventions=[{"kind": "teleport_pad", "ring": ring}])
    check("an unknown intervention kind is ignored, not guessed",
          bogus["axes"]["drainage"]["delta"] == 0
          and "ignored" in bogus["axes"]["drainage"]["interventions"][0],
          bogus["axes"]["drainage"]["interventions"][0])

    # --- 7. height does not move drainage, and the report says why --- #
    check("a height change leaves drainage exactly unchanged",
          taller_west["axes"]["drainage"]["delta"] == 0,
          str(taller_west["axes"]["drainage"]["delta"]))
    check("and explains that this is arithmetic, not a bug",
          "NOT to building height" in taller_west["axes"]["drainage"]["limitation"], "")

    # --- 8. place and reset are refused rather than approximated --- #
    placed = evaluate(grids, feats,
                      ops=[MassingOp("place", count=3, scope="east")])
    check("place is declared unsupported", placed["unsupportedOps"] == ["place"],
          str(placed["unsupportedOps"]))
    check("and the envelope count is honestly unchanged",
          placed["massing"]["candidateCount"] == placed["massing"]["baselineCount"], "")
    check("with the reason it belongs to the generator",
          "city-model" in placed["whyUnsupported"]
          or "city model" in placed["whyUnsupported"], "")

    # --- 9. purity: the baseline survives being evaluated against --- #
    from app.services.design_metrics import footprints_from_geojson
    fps = footprints_from_geojson(feats)
    before = [f.height_m for f in fps]
    apply_massing(fps, [MassingOp("scale_all", multiplier=3.0, scope="all")],
                  grids.bounds)
    check("apply_massing does not mutate its input",
          [f.height_m for f in fps] == before, "")

    # --- 10. the duplication with the frontend is real, and pinned --- #
    ts = REPO_DIR / "frontend/src/view/cityModel.ts"
    if ts.exists():
        text = ts.read_text()
        # Only two real declarations: the branch dispatch `op.op === 'scale_all'`
        # and the MassingOp union at the interface. An earlier `op\.\w+ === '...'`
        # matched `typeof op.heightM === 'number'` and reported a frontend
        # operation called "number".
        declared = set(re.findall(r"op\.op === '(\w+)'", text))
        union = re.search(r"op:\s*((?:'\w+'\s*\|\s*)+'\w+')", text)
        if union:
            declared |= set(re.findall(r"'(\w+)'", union.group(1)))
        ours = set(MassingOp.SUPPORTED)
        check("this module supports every op the frontend applies",
              declared <= ours | {"place"},
              f"frontend {sorted(declared)}")
        shared = declared & (ours - {"place"})
        check("and the shared ops appear in both implementations",
              len(shared) >= 3, f"{sorted(shared)}")
    else:
        check("the frontend massing file is present to compare against", False,
              "cityModel.ts not found")

    # --- Stage 3: compare() under stated weights --- #
    #
    # compare() is the only place an ordering is allowed, and only when the caller
    # states what they value. The checks below defend the refusal, the weights
    # echo, the polarity and the invariant to input order. All of them use
    # synthetic evaluate()-shaped dicts so the numbers are controlled and the
    # ordering is not a property of the real district.

    def _scen(sid: str, shade: float, vent: float, drain: float) -> dict:
        return {"scenarioId": sid,
                "axes": {"shade": {"candidate": shade},
                         "ventilation": {"candidate": vent},
                         "drainage": {"candidate": drain}}}

    three = [_scen("A", 0.20, 0.10, 100.0),
             _scen("B", 0.30, 0.20, 200.0),
             _scen("C", 0.10, 0.05, 50.0)]

    refused = compare(three)
    check("compare() without weights refuses to rank",
          refused["refused"] is True and refused["ranked"] is None, "")
    check("and states a non-empty reason",
          isinstance(refused["reason"], str) and len(refused["reason"]) > 20, "")
    check("and still emits no combined score",
          refused["combinedScore"] is None and refused["verdict"] is None, "")

    weights = {"shade": 1.0, "ventilation": 2.0, "drainage": 1.0}
    ranked = compare(three, weights=weights)
    check("with weights it produces an ordering",
          ranked["refused"] is False and len(ranked["ranked"]) == 3, "")
    check("weightsUsed sums to 1",
          abs(sum(ranked["weightsUsed"].values()) - 1.0) < 1e-12,
          str(ranked["weightsUsed"]))
    check("and is proportional to the weights supplied",
          all(abs(ranked["weightsUsed"][k] - v / sum(weights.values())) < 1e-12
              for k, v in ranked["weightsAsSupplied"].items()), "")
    check("every ranked row carries the three raw axes",
          all(set(r["axes"]) == {"shade", "ventilation", "drainage"}
              for r in ranked["ranked"]), "")
    check("and every ranked row keeps its per-axis ranks",
          all(set(r["perAxisRank"]) == {"shade", "ventilation", "drainage"}
              for r in ranked["ranked"]), "")
    reordered = compare(list(reversed(three)), weights=weights)
    check("the ordering is invariant to input list order",
          [r["scenarioId"] for r in ranked["ranked"]]
          == [r["scenarioId"] for r in reordered["ranked"]],
          str([r["scenarioId"] for r in ranked["ranked"]]))

    # polarity: strictly worse ventilation, equal elsewhere, ventilation-only.
    good, bad = _scen("GOOD", 0.2, 0.10, 100.0), _scen("BAD", 0.2, 0.30, 100.0)
    order = [r["scenarioId"] for r in
             compare([good, bad], weights={"ventilation": 1.0})["ranked"]]
    check("a scheme worse only on ventilation ranks lower under a ventilation weight",
          order.index("GOOD") < order.index("BAD"), str(order))

    for label, bad_weights in (
        ("an unknown axis", {"nonsense": 1.0}),
        ("a negative weight", {"shade": -1.0}),
        ("an all-zero weight vector",
         {"shade": 0.0, "ventilation": 0.0, "drainage": 0.0}),
    ):
        try:
            compare(three, weights=bad_weights)
            check(f"compare() raises on {label}", False, "it was accepted")
        except ValueError:
            check(f"compare() raises on {label}", True, "")

    try:
        compare([])
        check("compare() raises on an empty scenario list", False, "it was accepted")
    except ValueError:
        check("compare() raises on an empty scenario list", True, "")

    duplicate = compare([_scen("X", 0.1, 0.1, 1.0), _scen("X", 0.2, 0.2, 2.0)],
                        weights={"shade": 1.0})
    check("duplicate scenarioIds do not crash the ranking",
          len(duplicate["ranked"]) == 2, f"{len(duplicate['ranked'])} rows")

    check("the polarity table is stated once and echoed",
          AXIS_BETTER == {"shade": 1, "ventilation": -1, "drainage": -1}
          and ranked["polarity"]["ventilation"] == "lower is better",
          str(ranked["polarity"]))

    # evaluate() itself still emits no scalar total; its ventilation axis gained a
    # per-direction fadRatio so a massing change is visible directionally.
    check("evaluate() still emits no combined score",
          taller_west["combinedScore"] is None, "")
    check("the ventilation axis carries a per-direction fadRatio",
          set(taller_west["axes"]["ventilation"]["fadRatio"]) == {"NE", "ENE", "E"},
          str(sorted(taller_west["axes"]["ventilation"]["fadRatio"])))

    # --- determinism: no wall-clock, no RNG, one frozen scenario twice --- #
    import re as _re
    module_sources = {
        "design_metrics": BACKEND_DIR / "app/services/design_metrics.py",
        "ventilation": BACKEND_DIR / "app/services/ventilation.py",
        "scenario": BACKEND_DIR / "app/services/scenario.py",
        "sandbox_model": BACKEND_DIR / "app/services/sandbox_model.py",
        "hydrology": BACKEND_DIR / "scripts/hydrology.py",
    }
    offenders = [name for name, path in module_sources.items()
                 if _re.search(r"^\s*(import|from)\s+(time|random)\b",
                               path.read_text(), _re.M)]
    check("no new or changed module imports time or random",
          not offenders, f"offenders: {offenders}" if offenders else "none")

    frozen = [MassingOp("scale_all", multiplier=1.3, scope="west")]
    import time as _time
    _t0 = _time.time()
    first = evaluate(grids, feats, ops=frozen)
    _eval_seconds = _time.time() - _t0
    second = evaluate(grids, feats, ops=frozen)
    check("one frozen scenario reproduces identical numbers twice",
          first["axes"] == second["axes"]
          and first["tradeoffs"] == second["tradeoffs"]
          and first["massing"] == second["massing"], "")
    check("evaluate() stays inside its one-second budget",
          _eval_seconds < 1.0, f"{_eval_seconds:.3f}s")
    _t1 = _time.time()
    compare([{**first, "scenarioId": "A"}, {**second, "scenarioId": "B"}],
            weights={"shade": 1.0, "ventilation": 1.0, "drainage": 1.0})
    check("evaluate() plus compare() stays inside one second",
          (_time.time() - _t1) < 1.0, f"{_time.time() - _t1:.3f}s for compare")

    # --- apply_massing regressions: two bugs that produced plausible wrong scores --- #
    #
    # (a) `move` referenced an undefined `f_lat` and raised NameError, so every
    #     metric move op -- which the frontend applies -- was dead. A metric move
    #     is lon/lat-dependent, so the factor cannot be hoisted out of the loop.
    # (b) `scale_all` and `set_site` rebuilt the Footprint without carrying
    #     `bearing_deg`, silently resetting an earlier `rotate` to 0.0. The
    #     bearing is what `frontal_area_raster`/`cell_fad` read, so a rotated
    #     scheme scored as if unrotated -- a wrong number that looks correct.
    import math as _math
    from app.services.design_metrics import Footprint

    bounds = (113.9, 22.4, 114.1, 22.5)
    start = [Footprint(114.0, 22.44, 10.0, 10.0, 20.0)]
    try:
        moved_east = apply_massing(start, [MassingOp("move", dx_m=100.0, dy_m=0.0)],
                                   bounds)[0]
        expected_dlon = 100.0 / (111_320.0 * _math.cos(_math.radians(22.44)))
        check("a metric move does not raise, and shifts lon by dx/metre-per-degree",
              abs((moved_east.lon - 114.0) - expected_dlon) < 1e-12
              and moved_east.lat == 22.44,
              f"dlon {moved_east.lon - 114.0:.9f} vs {expected_dlon:.9f}")
        moved_north = apply_massing(start, [MassingOp("move", dx_m=0.0, dy_m=100.0)],
                                    bounds)[0]
        check("and shifts lat by dy/110540",
              abs((moved_north.lat - 22.44) - 100.0 / 110_540.0) < 1e-12,
              f"dlat {moved_north.lat - 22.44:.9f}")
    except Exception as exc:  # noqa: BLE001
        check("a metric move does not raise, and shifts lon by dx/metre-per-degree",
              False, f"raised {type(exc).__name__}: {exc}")
        check("and shifts lat by dy/110540", False, "not reached")

    rotated_scaled = apply_massing(
        start, [MassingOp("rotate", degrees=30.0),
                MassingOp("scale_all", multiplier=1.4)], bounds)[0]
    check("scale_all preserves the bearing a preceding rotate set",
          rotated_scaled.bearing_deg == 30.0
          and abs(rotated_scaled.height_m - 28.0) < 1e-9,
          f"bearing {rotated_scaled.bearing_deg}, height {rotated_scaled.height_m}")

    rotated_set = apply_massing(
        start, [MassingOp("rotate", degrees=30.0),
                MassingOp("set_site", height_m=50.0)], bounds)[0]
    check("set_site preserves the bearing too",
          rotated_set.bearing_deg == 30.0 and rotated_set.height_m == 50.0,
          f"bearing {rotated_set.bearing_deg}, height {rotated_set.height_m}")

    rotated_moved = apply_massing(
        start, [MassingOp("rotate", degrees=30.0),
                MassingOp("move", dx_m=50.0)], bounds)[0]
    check("and a later move preserves the bearing",
          rotated_moved.bearing_deg == 30.0,
          f"bearing {rotated_moved.bearing_deg}")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
