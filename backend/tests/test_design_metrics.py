"""Tests for the geometry-derived design metrics.

Three things are defended here, and only the first is arithmetic:

1. The geometry is right (solar position, projected width, swept shadows).
2. Every metric is tied to a rule Hong Kong's own AVA guidance states, and says so
   rather than presenting these as our own invention.
3. No metric claims a verdict. The AVA policy is explicit that whether a scheme
   meets a standard cannot be known, so a report printing "PASS" would invent one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.design_metrics import (  # noqa: E402
    GROUND_LEVEL_BAND_M,
    VR_COMFORTABLE_PEDESTRIAN,
    VR_CONGESTED_STREET,
    VR_OPEN_SPACE,
    Footprint,
    design_report,
    footprints_from_geojson,
    frontal_area_density,
    ground_level_void_fraction,
    height_variation_cv,
    projected_width_m,
    shade_fraction,
    site_coverage,
    solar_position,
)
from app.services.sandbox_model import load_grids  # noqa: E402

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

    features = json.loads(
        (BACKEND_DIR / "data/rasters/citymodel_hsk.json").read_text())["features"]
    fps = footprints_from_geojson(features)
    check("every envelope converts", len(fps) == len(features),
          f"{len(fps)} of {len(features)}")

    junk = footprints_from_geojson([
        {"geometry": {"coordinates": [[[1.0, 2.0], [3.0]]]}, "properties": {}},
        {"geometry": {"coordinates": [[[1.0, 2.0]]]}, "properties": {}},
        {"geometry": None, "properties": {}},
        {"geometry": {"coordinates": [[[114.0, 22.4], [114.001, 22.4],
                                       [114.001, 22.401], [114.0, 22.401]]]},
         "properties": {"height_m": "not a number"}},
    ])
    check("malformed features are skipped, not fatal", len(junk) == 1, f"{len(junk)} kept")

    alt_noon, _ = solar_position(22.44, 114.01, 172, 12.0)
    alt_dec, _ = solar_position(22.44, 114.01, 355, 12.0)
    # solar_position returns (altitude, azimuth); this took the first as azimuth
    # and then asserted 22.4 degrees was "easterly". It is the altitude.
    _, az_am = solar_position(22.44, 114.01, 355, 9.0)
    check("the summer sun is higher than the winter sun",
          alt_noon > alt_dec, f"{alt_noon:.1f} vs {alt_dec:.1f} deg")
    check("the June noon altitude is plausible for 22.4N",
          60.0 < alt_noon < 92.0, f"{alt_noon:.1f} deg")
    check("the December noon altitude is plausible for 22.4N",
          40.0 < alt_dec < 55.0, f"{alt_dec:.1f} deg")
    check("morning sun is easterly", 45.0 < az_am < 150.0, f"az {az_am:.1f} deg")
    check("there is no sun at midnight",
          solar_position(22.44, 114.01, 355, 23.0)[0] < 0, "")

    check("a square presents its side and its diagonal at 45 deg",
          abs(projected_width_m(10, 10, 0) - 10.0) < 0.01
          and abs(projected_width_m(10, 10, 45) - 14.142) < 0.01,
          f"N={projected_width_m(10, 10, 0):.2f}, NE={projected_width_m(10, 10, 45):.2f}")
    check("a long east-west slab is widest to a north or south wind",
          projected_width_m(100, 10, 0) < projected_width_m(100, 10, 90), "")

    # Footprint objects, not dicts: app.services.ventilation takes dicts and
    # this module takes the typed dataclass. Two conventions now exist for the
    # same calculation; the typed one is the one new code should use.
    cube = [Footprint(114.0, 22.44, 10.0, 10.0, 10.0)]
    fad1 = frontal_area_density(cube, 10_000.0)
    check("one 10 m cube over 1 ha gives FAD 0.01",
          abs(fad1["byDirection"]["N"] - 0.01) < 1e-6, f"{fad1['byDirection']['N']:.5f}")
    check("sixteen directions, per the AVA method",
          fad1["directionsComputed"] == 16, f"{fad1['directionsComputed']}")
    check("twice the buildings doubles the frontal area",
          abs(frontal_area_density(cube * 2, 10_000.0)["byDirection"]["N"] - 0.02) < 1e-6, "")
    check("a zero site area is refused, not divided by",
          "error" in frontal_area_density(cube, 0), "")

    check("the open-space VR band is the published 0.3",
          VR_OPEN_SPACE == 0.30, f"{VR_OPEN_SPACE}")
    check("the congested-street band is the published 0.05-0.1",
          VR_CONGESTED_STREET == (0.05, 0.10), str(VR_CONGESTED_STREET))
    check("the comfortable VR is the documented derivation",
          abs(VR_COMFORTABLE_PEDESTRIAN - 1.25 / 7.0) < 1e-9,
          f"{VR_COMFORTABLE_PEDESTRIAN:.4f} = 1.25/7")
    check("and it sits between the two published bands",
          VR_CONGESTED_STREET[1] < VR_COMFORTABLE_PEDESTRIAN < VR_OPEN_SPACE, "")

    varied = [Footprint(114.0, 22.44, 30, 30, h) for h in (12, 40, 18, 55, 15, 60)]
    uniform = [Footprint(114.0 + i * 0.0003, 22.44, 30, 30, 30) for i in range(6)]
    check("varying heights score higher than uniform heights",
          height_variation_cv(varied, 0) > height_variation_cv(uniform, 0),
          f"{height_variation_cv(varied, 0):.3f} vs {height_variation_cv(uniform, 0):.3f}")
    check("a uniform skyline scores zero variation",
          height_variation_cv(uniform, 0) == 0.0, "")

    towers = [Footprint(114.0 + i * 0.004, 22.44, 25, 25, 90) for i in range(4)]
    podium = [Footprint(114.0 + i * 0.004, 22.44, 80, 80, 11) for i in range(4)]
    check("a low podium blocks more street level than spaced towers",
          ground_level_void_fraction(podium) < ground_level_void_fraction(towers),
          f"podium {ground_level_void_fraction(podium):.3f} vs "
          f"towers {ground_level_void_fraction(towers):.3f}")
    check("the ground-level band excludes towers", GROUND_LEVEL_BAND_M == 12.0, "")
    check("site coverage of nothing is zero", site_coverage([]) == 0.0, "")
    check("site coverage is a fraction on the real model",
          0.0 < site_coverage(fps) < 1.0, f"{site_coverage(fps):.3f}")

    noon = shade_fraction(fps, grids, 22.44, 114.01, 355, 14)
    morn = shade_fraction(fps, grids, 22.44, 114.01, 355, 9)
    summer = shade_fraction(fps, grids, 22.44, 114.01, 172, 14)
    night = shade_fraction(fps, grids, 22.44, 114.01, 355, 22)
    check("a low winter sun shades more than a high summer sun",
          noon["shadeFraction"] > summer["shadeFraction"],
          f"winter {noon['shadeFraction']:.1%} vs summer {summer['shadeFraction']:.1%}")
    check("and the fraction is physical, not a 3% walk artefact",
          noon["shadeFraction"] > 0.05, f"{noon['shadeFraction']:.1%}")

    # --- saturation, the bug this check exists for --- #
    #
    # An earlier sweep ran every building out to the TALLEST building's shadow
    # length, so short buildings had their footprints painted right across the
    # district. The shaded set saturated, and shade was over-reported by 2.3x
    # (51.2% where the truth is 22.2%). Every ordering check above still passed,
    # because a saturated sweep is monotone in the right directions while being
    # wrong about the magnitude. Ordering is not enough; magnitude is the signal.
    half = footprints_from_geojson(features[: len(features) // 2])
    from app.services.scenario import MassingOp, apply_massing
    raised_all = shade_fraction(
        apply_massing(fps, [MassingOp("scale_all", multiplier=1.4)], grids.bounds),
        grids, 22.44, 114.01, 355, 14)["shadeFraction"]
    raised_half = shade_fraction(
        apply_massing(fps, [MassingOp("scale_all", multiplier=1.4, scope="west")],
                      grids.bounds),
        grids, 22.44, 114.01, 355, 14)["shadeFraction"]
    check("raising half the district shades strictly less than raising all of it",
          raised_half < raised_all,
          f"west {raised_half:.2%} vs all {raised_all:.2%} "
          f"(equal values mean the sweep saturated)")
    check("and the two are not merely close",
          raised_all - raised_half > 0.005,
          f"difference {raised_all - raised_half:.4f}")
    ladder = [shade_fraction(
        apply_massing(fps, [MassingOp("scale_all", multiplier=m)], grids.bounds),
        grids, 22.44, 114.01, 355, 14)["shadeFraction"]
        for m in (1.0, 1.2, 1.4, 1.6)]
    check("shade increases strictly with height, step by step",
          all(b > a for a, b in zip(ladder, ladder[1:])),
          " < ".join(f"{v:.1%}" for v in ladder))
    check("and a building is never swept past its own shadow",
          half and shade_fraction(half, grids, 22.44, 114.01, 355, 14)
          ["shadeFraction"] <= noon["shadeFraction"],
          "half the district cannot out-shade all of it")
    # The earlier version of this asserted morning shade > 20%, which was
    # calibrated against the saturated sweep. The physically sound claim is the
    # seasonal one: a lower sun casts longer shadows, so the winter solstice must
    # shade more ground than the summer solstice at the same hours.
    from app.services.design_metrics import design_report as _dr
    december = _dr(fps, grids, day_of_year=355)["shade"]["meanShadeFraction"]
    june = _dr(fps, grids, day_of_year=172)["shade"]["meanShadeFraction"]
    check("the low winter sun shades more ground than the high summer sun",
          december > june, f"December {december:.1%} vs June {june:.1%}")
    check("and both are a real fraction of the district, not noise",
          0.05 < june < december < 0.60,
          f"{june:.1%} .. {december:.1%}")
    check("no sun means no shade, with a reason",
          night["shadeFraction"] == 0.0 and "horizon" in night.get("note", ""), "")
    check("every instant declares itself a single instant",
          "daily mean radiant temperature" in noon["note"], "")

    r = design_report(fps, grids)
    check("the report covers the whole district",
          r["footprintCount"] == len(fps), f"{r['footprintCount']}")
    check("every metric carries the rule it comes from",
          all("rule" in r[k] for k in ("height", "siteCoverage",
                                       "groundLevelVoidFraction",
                                       "frontalAreaDensity")), "")
    check("the bands say they are not acceptance criteria",
          "cannot be known" in r["referenceBands"]["basis"], "")
    check("and the report prints NO verdict", r["verdict"] is None, str(r["verdict"]))
    check("with the reason stated", "cannot be known" in r["whyNoVerdict"], "")
    lim = r["frontalAreaDensity"]["limitation"].lower()
    check("the FAD limitation explains why orientation cannot fix it",
          "even in" in lim and "cannot distinguish" in lim, "")
    check("and points at the measure that can",
          "directionalexposure" in lim, "")
    check("the report carries a measure that IS direction-sensitive",
          r["directionalExposure"]["symmetryBroken"]
          and "neMinusSwM" in r["directionalExposure"],
          f"NE-SW {r['directionalExposure']['neMinusSwM']:+.2f} m")
    check("site coverage states which way its bias runs",
          "reads high" in r["siteCoverage"]["caveat"].lower(), "")
    check("the shade caveat names what is missing",
          "no surface energy balance" in r["shade"]["caveat"].lower(), "")

    # --- the directional exposure screen: three bugs this block exists for --- #
    import numpy as np
    from app.services.design_metrics import (directional_exposure,
                                             rasterise_height)

    # (a) A lone box must be symmetric. A directional metric that reports 50 m for
    #     north and 0 m for south is broken, not clever.
    lone = np.zeros((40, 40), dtype=np.float32)
    lone[18:22, 18:22] = 50.0
    lo = directional_exposure(lone)
    vals = {k: lo[k]["meanFirstHitHeightM"] for k in
            ("N", "NE", "E", "SE", "S", "SW", "W", "NW")}
    check("a lone box reads the same height from every direction",
          all(v == 50.0 for v in vals.values()), str(sorted(set(vals.values()))))

    # (b) The direction convention. Row index increases southward and column
    #     eastward, so a wind FROM the east travels toward decreasing columns. The
    #     first version of _TRAVEL had E and W swapped and all four diagonals
    #     mirrored; the symptom was a tall western edge reporting a high
    #     first-hit height for the *easterly*, which is backwards and silent.
    tall = np.zeros((40, 40), dtype=np.float32)
    tall[2:38, 2:38] = 15.0
    tall[1:3, 2:38] = 60.0      # tall NORTH edge
    tall[2:38, 1:3] = 60.0      # tall WEST edge
    tr = directional_exposure(tall)
    check("a wind from the north meets the tall northern edge",
          tr["N"]["meanFirstHitHeightM"] == 60.0,
          f"{tr['N']['meanFirstHitHeightM']:.2f} m")
    check("a wind from the west meets the tall western edge",
          tr["W"]["meanFirstHitHeightM"] == 60.0,
          f"{tr['W']['meanFirstHitHeightM']:.2f} m")
    check("a wind from the south meets only the low interior",
          tr["S"]["meanFirstHitHeightM"] < 40.0,
          f"{tr['S']['meanFirstHitHeightM']:.2f} m")
    check("and likewise a wind from the east",
          tr["E"]["meanFirstHitHeightM"] < 40.0,
          f"{tr['E']['meanFirstHitHeightM']:.2f} m")
    check("the north-east diagonal meets the tall corner",
          tr["NE"]["meanFirstHitHeightM"] == 60.0,
          f"{tr['NE']['meanFirstHitHeightM']:.2f} m")

    # (c) The reduction axis. For E and W the rays are ROWS, so the step axis is
    #     1, not 0. Reducing over axis 0 unconditionally reported one result per
    #     column and claimed 370 rays hit on a district where 518 rows contain a
    #     building -- an internal inconsistency that was visible in the output.
    H = rasterise_height(fps, grids)
    rows_with = int((H > 0).any(axis=1).sum())
    ex = directional_exposure(H)
    check("east and west count one ray per row, and that matches the raster",
          ex["E"]["raysWithBuilding"] == rows_with,
          f"E hit {ex['E']['raysWithBuilding']}, rows with a building {rows_with}")

    check("the real district is NOT symmetric about NE and SW",
          ex["symmetryBroken"],
          f"NE {ex['NE']['meanFirstHitHeightM']:.2f} m vs "
          f"SW {ex['SW']['meanFirstHitHeightM']:.2f} m")
    check("and every direction reports a height in a plausible range",
          all(0.0 < ex[k]["meanFirstHitHeightM"] < 200.0 for k in
              ("N", "NE", "E", "SE", "S", "SW", "W", "NW")),
          ", ".join(f"{k}:{ex[k]['meanFirstHitHeightM']:.1f}"
                    for k in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")))
    check("the closed form cannot break the symmetry, and says so",
          "cannot break the NE" in projected_width_m.__doc__
          or "cannot** break" in projected_width_m.__doc__,
          "documented limitation")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
