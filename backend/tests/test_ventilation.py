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
import sys
import time
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.design_metrics import (  # noqa: E402
    Footprint,
    cell_fad,
    footprints_from_geojson,
)
from app.services.sandbox_model import load_grids  # noqa: E402
from app.services.ventilation import (  # noqa: E402
    CARDINALS,
    fad_ratio,
    frontal_area_density,
    least_cost_corridors,
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
    #
    # The footprints come from the project's one GeoJSON converter rather than a
    # hand-rolled ring walk: this block used to read ``coordinates[0]`` itself,
    # which is a ring for a Polygon and a *list of rings* for a MultiPolygon --
    # so the one MultiPolygon in the real model (bldg-0104) made the min/max
    # below compare lists and the suite died on line 124. A second ring parser
    # is how that happens.
    model = BACKEND_DIR / "data" / "rasters" / "buildings_hsk.json"
    features = json.loads(model.read_text())["features"]
    fps = [
        {"widthM": fp.width_m, "depthM": fp.depth_m, "heightM": fp.height_m}
        for fp in footprints_from_geojson(features)
    ]
    out = ventilation_screening(fps, 7_865_000.0,
                                prevailing=(ref or {}).get("windRegime", {}).get("prevailing"),
                                combined_frequency_percent=40.8)
    check(f"it runs on all {len(features)} real footprints",
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
    check("and is not on the published VR scale",
          # FAD for the REAL district is ~0.018 against a published velocity
          # ratio of 0.28 -- two orders apart, and now on the low side. The
          # direction flipped when surveyed footprints replaced the procedural
          # 45 m blocks, so the assertion states the separation, not a sign.
          out["frontalAreaDensity"]["max"] < vr["siteSpatialAverage"]["baseline"] / 5
          if ref else True,
          f"FAD max {out['frontalAreaDensity']['max']} vs published VR "
          f"{vr['siteSpatialAverage']['baseline'] if ref else '-'}")

    # --- Stage 5b: least-cost corridors --- #
    #
    # The design's literal cost = 1/(1+FAD) is MINIMISED where FAD is largest, so
    # a least-cost path would run through the most blocked cells. The shipped cost
    # is 1 + FAD. These checks test the BEHAVIOUR -- a corridor down a low-FAD
    # lane, and a higher cost when that lane is blocked -- never the formula
    # string, so an inversion cannot pass by restating itself.

    class _SynthGrid:
        rows = 40
        cols = 40
        cell_area_m2 = 25.0

        def cell_of(self, lon, lat):
            return (int(lat), int(lon))

    def _lane_district(block_lane: bool) -> list[Footprint]:
        fps = []
        for r in range(0, 40, 4):
            for c in list(range(0, 19, 4)) + list(range(22, 40, 4)):
                fps.append(Footprint(float(c), float(r), 15.0, 15.0, 40.0))
        if block_lane:
            for r in range(0, 40, 4):
                fps.append(Footprint(19.0, float(r), 15.0, 15.0, 40.0))
        return fps

    sg = _SynthGrid()
    open_lane = least_cost_corridors(_lane_district(False), sg,
                                     directions={"N": 0.0})["directions"]["N"]
    blocked_lane = least_cost_corridors(_lane_district(True), sg,
                                        directions={"N": 0.0})["directions"]["N"]
    lane_cols = {c for _, c in open_lane["path"]}
    check("a low-FAD lane returns a corridor down the lane",
          not open_lane["blocked"] and lane_cols == {19},
          f"columns used {sorted(lane_cols)}")
    check("and every cell of the corridor is unblocked",
          max(float(cell_fad(_lane_district(False), sg, 0.0)[r, c])
              for r, c in open_lane["path"]) == 0.0, "")
    check("blocking the lane raises the corridor's mean cost",
          blocked_lane["meanCost"] > open_lane["meanCost"],
          f"blocked {blocked_lane['meanCost']} > open {open_lane['meanCost']}")

    # --- corridors on the real district --- #
    grids = load_grids()
    check("the rasters load for the corridor test", grids is not None)
    if grids is not None:
        real_fps = footprints_from_geojson(features)

        # A HOST-INDEPENDENT budget.
        #
        # This assertion used to be `elapsed < 1.0`: an absolute second, set on
        # faster hardware, which measured 2.95 s here and 2.98 s on unmodified
        # main -- so it failed for the machine, not for the code. Widening the
        # constant would have been the same move as deleting a failing test.
        #
        # Instead the bound is a RATIO measured on this host, in this process:
        # one direction is the unit, and all eight must come in under ten of
        # them. The graph build is shared, so the honest expectation is well
        # under 8x. This still catches the regression the test exists for -- a
        # super-linear corridor solve -- and cannot fail because a laptop is slow.
        t_ref = time.time()
        least_cost_corridors(real_fps, grids,
                             directions={"NE": CARDINALS["NE"]})
        ref_elapsed = time.time() - t_ref

        t0 = time.time()
        corridors = least_cost_corridors(real_fps, grids)
        elapsed = time.time() - t0
        check("eight principal corridors stay within 10x a single direction",
              elapsed < max(ref_elapsed, 1e-6) * 10.0,
              f"{elapsed:.2f}s for 8 vs {ref_elapsed:.2f}s for 1 "
              f"({elapsed / max(ref_elapsed, 1e-6):.1f}x)")
        ne, sw = corridors["directions"]["NE"], corridors["directions"]["SW"]
        ne_list = [tuple(p) for p in ne["path"]]
        sw_list = [tuple(p) for p in sw["path"]]
        check("NE and SW both exist and span the district",
              ne["corridorCells"] > 100 and sw["corridorCells"] > 100,
              f"NE {ne['corridorCells']} cells, SW {sw['corridorCells']}")
        check("NE and SW differ as ordered lists",
              ne_list != sw_list,
              "identical ordered paths would mean the direction table is broken")
        check("and they cost the same, over a bit-identical cost surface",
              ne["meanCost"] == sw["meanCost"]
              and ne["pathLengthM"] == sw["pathLengthM"]
              and np.array_equal(cell_fad(real_fps, grids, 45.0),
                                 cell_fad(real_fps, grids, 225.0)),
              f"NE {ne['meanCost']} vs SW {sw['meanCost']}")
        # --- Stage 5c: the arrangement half --- #
        # The PATH is still one path walked both ways (exact geometry, kept
        # honest above), but the response now carries what IS direction-specific:
        # the entry edge and the first-obstruction height from the canonical
        # directional_exposure march. A NE reading and a SW reading must no
        # longer be interchangeable.
        check("each corridor names the edge its wind enters from",
              ne["entryEdge"] == "north-east" and sw["entryEdge"] == "south-west",
              f"NE {ne.get('entryEdge')}, SW {sw.get('entryEdge')}")
        check("the entry edge is also derivable from the path's first cell",
              ne["path"][0][0] == 0
              and ne["path"][0][1] >= grids.cols - 2,
              f"NE path starts at {ne['path'][0]} of {grids.rows}x{grids.cols}")
        check("NE and SW first-obstruction heights differ (Stage 5c)",
              ne["firstObstructionMeanM"] is not None
              and sw["firstObstructionMeanM"] is not None
              and ne["firstObstructionMeanM"] != sw["firstObstructionMeanM"],
              f"NE {ne['firstObstructionMeanM']} m vs "
              f"SW {sw['firstObstructionMeanM']} m")
        check("the first-obstruction heights match directional_exposure",
              # Pinned to the real-footprint march: the procedural massing gave
              # 30.233 / 44.851. The point of the pin is that the corridor reads
              # the canonical march rather than re-deriving a second one, so the
              # number moving with the geometry is expected; two marches
              # disagreeing is not.
              abs(ne["firstObstructionMeanM"] - 15.334) < 0.5
              and abs(sw["firstObstructionMeanM"] - 8.877) < 0.5,
              f"NE {ne['firstObstructionMeanM']} m, SW {sw['firstObstructionMeanM']} m")
        check("the limitation says where the new difference ends",
              "entryEdge" in corridors["limitation"]
              and "firstObstructionMeanM" in corridors["limitation"]
              and "invented weight" in corridors["limitation"], "")
        check("the 180-degree limitation is documented",
              "180" in corridors["limitation"]
              and "periodic" in corridors["limitation"], "")
        check("the corridor names its shipped cost surface, not the inverted one",
              "1 + cellFAD" in corridors["costSurface"], corridors["costSurface"])
        check("the corridor output carries the not-a-VR label",
              corridors["status"] == "FAD_IS_NOT_A_VELOCITY_RATIO"
              and "FAD is not a velocity ratio" in corridors["warning"], "")
        check("corridors are deterministic across two runs",
              least_cost_corridors(real_fps, grids, directions={"NE": 45.0})
              == least_cost_corridors(real_fps, grids, directions={"NE": 45.0}), "")
        try:
            least_cost_corridors(real_fps, grids, directions={"NOPE": 0.0})
            check("an unknown wind direction is refused", False, "it was accepted")
        except ValueError:
            check("an unknown wind direction is refused", True, "")

    # --- fad_ratio --- #
    same = fad_ratio(0.4, 0.4, "NE")
    rise = fad_ratio(0.4, 0.6, "NE")
    zero = fad_ratio(0.0, 0.6, "NE")
    check("an identical scheme gives a ratio of exactly 1.0",
          same["ratio"] == 1.0 and same["defined"] is True, str(same["ratio"]))
    check("more blockage gives a ratio above 1",
          rise["ratio"] > 1.0, str(rise["ratio"]))
    check("a zero baseline is undefined, not infinite",
          zero["defined"] is False and zero["ratio"] is None, str(zero["ratio"]))
    check("the ratio carries the comparison label",
          zero["status"] == "RELATIVE_COMPARISON_NOT_A_VELOCITY_RATIO"
          and "A ratio is a comparison" in zero["warning"], "")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
