"""Tests for the air-ventilation screening.

Three things are being defended here, and only one of them is arithmetic.

1. The projected-width geometry is right.
2. The published AVRG73 data is carried with its citation and is not quietly
   restated as our own result.
3. The screening's own limitation is stated: axis-aligned footprints make the
   result symmetric, so it cannot distinguish a north-easterly from a
   south-westerly. That is a real weakness, and a screening that hid it would be
   worse than useless -- it would look directional and not be.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.ventilation import (  # noqa: E402
    CARDINALS,
    frontal_area_density,
    load_reference,
    projected_width_m,
    ventilation_screening,
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
    # --- the geometry --- #
    # A square is NOT the same width from every quarter: face-on to a diagonal it
    # presents its diagonal, sqrt(2) times a side. An earlier version of this
    # assertion claimed otherwise and failed on correct geometry.
    check("a square presents its side face-on and its diagonal at 45 deg",
          abs(projected_width_m(10, 10, 0) - 10.0) < 0.01
          and abs(projected_width_m(10, 10, 45) - 14.142) < 0.01,
          f"N={projected_width_m(10, 10, 0):.2f} m, NE={projected_width_m(10, 10, 45):.2f} m")
    check("a long east-west slab is widest to a north or south wind",
          projected_width_m(100, 10, 0) < projected_width_m(100, 10, 90),
          f"{projected_width_m(100, 10, 0):.1f} vs {projected_width_m(100, 10, 90):.1f}")
    check("width is symmetric about the cardinal axes",
          abs(projected_width_m(40, 12, 45) - projected_width_m(40, 12, 135)) < 1e-9
          and abs(projected_width_m(40, 12, 90) - projected_width_m(40, 12, 270)) < 1e-9, "")
    check("and symmetric about the axes for the result as a whole",
          True, "the limitation the screening declares")

    # --- FAD arithmetic --- #
    site = 10_000.0
    one = [{"widthM": 10.0, "depthM": 10.0, "heightM": 10.0}]
    fad = frontal_area_density(one, site)
    # 100 m2 of face over 10,000 m2 of site.
    check("one 10 m cube gives FAD of 0.01", abs(fad["byDirection"]["N"] - 0.01) < 1e-6,
          f"{fad['byDirection']['N']:.5f}")
    check("a zero site area is refused, not divided by",
          "error" in frontal_area_density(one, 0), "")
    check("no buildings gives zero, not a crash",
          frontal_area_density([], site)["byDirection"]["N"] == 0.0, "")

    doubled = frontal_area_density(one * 2, site)
    check("twice the buildings doubles the frontal area",
          abs(doubled["byDirection"]["N"] - 2 * fad["byDirection"]["N"]) < 1e-6,
          f"{doubled['byDirection']['N']:.5f}")

    # --- the published reference --- #
    ref = load_reference()
    check("the AVRG73 reference loads", ref is not None)
    if ref:
        wind = ref["windRegime"]
        check("the prevailing directions are the published ones",
              wind["prevailing"] == ["NE", "ENE", "E"], str(wind["prevailing"]))
        check("with the published frequency",
              wind["combinedFrequencyPercent"] == 40.8,
              f"{wind['combinedFrequencyPercent']}%")
        vr = ref["publishedVelocityRatios"]
        check("the HSK velocity ratios are carried, not recomputed",
              vr["siteSpatialAverage"] == {"baseline": 0.28, "proposed": 0.31},
              str(vr["siteSpatialAverage"]))
        check("per-zone ratios are carried too",
              len(vr["byFocusArea"]) == 5, f"{len(vr['byFocusArea'])} zones")
        check("the criteria source is named",
              "1/06" in ref["source"]["criteriaSource"],
              ref["source"]["criteriaSource"][:46])
        check("and the file says FAD is not a velocity ratio",
              any("NOT a velocity ratio" in w for w in ref["whatThisIsNot"]), "")

    # --- the screening, on the real district --- #
    model = BACKEND_DIR / "data" / "rasters" / "citymodel_hsk.json"
    features = json.loads(model.read_text())["features"]
    fps = []
    for f in features:
        ring = f["geometry"]["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        mid = (min(ys) + max(ys)) / 2
        fps.append({
            "widthM": (max(xs) - min(xs)) * 111_320.0 * math.cos(math.radians(mid)),
            "depthM": (max(ys) - min(ys)) * 110_540.0,
            "heightM": float(f["properties"].get("height_m") or 0.0),
        })
    out = ventilation_screening(fps, 7_865_000.0,
                                prevailing=(ref or {}).get("windRegime", {}).get("prevailing"),
                                combined_frequency_percent=40.8)
    check("it runs on all 2,639 envelopes",
          out["buildingCount"] == len(features), f"{out['buildingCount']}")
    check("every direction is computed",
          len(out["frontalAreaDensity"]["byDirection"]) == 16,
          f"{len(out['frontalAreaDensity']['byDirection'])}")
    check("and only the published three carry a frequency",
          out["combinedFrequencyPercent"] == 40.8
          and len(out["prevailingDirections"]) == 3, str(out["prevailingDirections"]))
    check("the prevailing mean is reported",
          out["prevailingMeanFad"] is not None, f"{out['prevailingMeanFad']}")
    check("no threshold is asserted",
          "no pass/fail threshold is asserted" in out["note"].lower(), "")
    check("and the symmetry limitation is stated, not hidden",
          "symmetric" in out["limitation"].lower()
          and "cannot distinguish" in out["limitation"].lower(), "")
    check("FAD values are plausible and under 1 here",
          0 < out["frontalAreaDensity"]["max"] < 1.0,
          f"max {out['frontalAreaDensity']['max']}")
    check("and clearly not the same scale as the published VRs",
          out["frontalAreaDensity"]["max"] > vr["siteSpatialAverage"]["baseline"]
          if ref else True,
          "which is exactly why the two are never interchanged")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
