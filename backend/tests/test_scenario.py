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
    AXIS_UP_MEANS,
    MassingOp,
    apply_massing,
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

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
