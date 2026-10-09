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

    # --- shade_mask extraction: shade_fraction must not have moved --- #
    #
    # shade_mask() was pulled out of shade_fraction(), which now delegates to it.
    # A refactor like that can move a whole shadow edge without changing any
    # ordering, so three published instants are pinned to 4 dp. Magnitude, not
    # ordering, is the signal.
    dec_morn = shade_fraction(fps, grids, 22.44, 114.01, 355, 9)
    dec_noon = shade_fraction(fps, grids, 22.44, 114.01, 355, 14)
    june_mean = design_report(fps, grids, day_of_year=172)["shade"]["meanShadeFraction"]
    check("the December 09:00 shade is the pre-extraction value",
          dec_morn["shadeFraction"] == 0.1625, f"{dec_morn['shadeFraction']}")
    check("the December 14:00 shade is the pre-extraction value",
          dec_noon["shadeFraction"] == 0.2215, f"{dec_noon['shadeFraction']}")
    check("the June daily mean shade is the pre-extraction value",
          june_mean == 0.1151, f"{june_mean}")
    empty_shade = shade_fraction([], grids, 22.44, 114.01, 355, 14)
    check("an empty footprint returns the un-capped 401-step sweep",
          empty_shade["shadowSteps"] == 401 and empty_shade["shadowCapped"] is False,
          f"steps {empty_shade['shadowSteps']}, capped {empty_shade['shadowCapped']}")

    # --- Stage 4: sky-view factor --- #
    #
    # The lone-tower fixture is a THIN single-cell 50 m tower, deliberately. A
    # 4x4-cell (20x20 m) 50 m block is a large obstruction and legitimately drops
    # the open-field mean to ~0.957; using it as the "lone tower" would make the
    # >0.98 acceptance pass for the wrong reason. The thin tower is what actually
    # exercises the horizon search.
    from app.services.design_metrics import (sky_view_factor,
                                             sky_view_factor_raster)

    thin = np.zeros((40, 40), dtype=np.float32)
    thin[20, 20] = 50.0
    svf_thin = sky_view_factor(thin, 5.0)
    check("a thin single-cell tower leaves the open field above 0.98",
          svf_thin["meanOpenFieldSvf"] > 0.98, f"{svf_thin['meanOpenFieldSvf']}")
    check("and the tower cell itself sees no sky",
          svf_thin["minSvf"] == 0.0, f"{svf_thin['minSvf']}")
    check("the SVF grid is pinned to 16 azimuth bins",
          svf_thin["azimuthBins"] == 16, f"{svf_thin['azimuthBins']}")
    check("and to a 300 m horizon radius",
          svf_thin["searchRadiusM"] == 300.0, f"{svf_thin['searchRadiusM']}")

    block4 = np.zeros((40, 40), dtype=np.float32)
    block4[18:22, 18:22] = 50.0
    svf_block4 = sky_view_factor(block4, 5.0)
    check("a 4x4-cell 50 m block is a large obstruction, not a lone tower",
          svf_block4["meanOpenFieldSvf"] < 0.98,
          f"{svf_block4['meanOpenFieldSvf']} -- so it must not be the lone-tower fixture")

    uniform = np.full((40, 40), 50.0, dtype=np.float32)
    check("a uniform tall block over the whole grid sees almost no sky",
          sky_view_factor(uniform, 5.0)["meanSvf"] < 0.05,
          f"{sky_view_factor(uniform, 5.0)['meanSvf']}")

    ring_means = []
    for h_ring in (5.0, 10.0, 20.0, 40.0, 80.0):
        r = np.zeros((40, 40), dtype=np.float32)
        r[:3, :] = r[-3:, :] = h_ring
        r[:, :3] = r[:, -3:] = h_ring
        ring_means.append(sky_view_factor(r, 5.0)["meanOpenFieldSvf"])
    check("SVF falls monotonically as a surrounding ring rises",
          all(a >= b for a, b in zip(ring_means, ring_means[1:])),
          " > ".join(f"{v:.3f}" for v in ring_means))
    check("the SVF raster is deterministic across two runs",
          np.array_equal(sky_view_factor_raster(thin, 5.0),
                         sky_view_factor_raster(thin, 5.0)), "")
    import time as _time
    _t0 = _time.time()
    sky_view_factor(rasterise_height(fps, grids), 5.0)
    _svf_seconds = _time.time() - _t0
    check("a whole-district SVF completes inside one second",
          _svf_seconds < 1.0, f"{_svf_seconds:.2f}s")
    zero_svf = sky_view_factor(np.zeros((40, 40), dtype=np.float32), 5.0)
    check("a zero-height raster is all sky, not a crash",
          zero_svf["meanSvf"] == 1.0 and zero_svf["buildingCells"] == 0,
          f"mean {zero_svf['meanSvf']}")

    # --- Stage 4: the heat blend and its anchoring --- #
    from app.services.design_metrics import (blended_heat_field,
                                             heat_field_report,
                                             load_heat_calibration)

    land_flat = np.full((12, 12), 0.5, dtype=np.float32)
    svf_flat = np.full((12, 12), 0.5, dtype=np.float32)
    shad_flat = np.zeros((12, 12), dtype=bool)
    base_field = blended_heat_field(land_flat, svf_flat, shad_flat)
    greened = blended_heat_field(np.full((12, 12), 0.1, dtype=np.float32),
                                 svf_flat, shad_flat)
    massed = blended_heat_field(land_flat,
                                np.full((12, 12), 0.2, dtype=np.float32),
                                np.ones((12, 12), dtype=bool))
    check("greening only lowers the mean heat field",
          greened.mean() < base_field.mean(),
          f"{greened.mean():.4f} < {base_field.mean():.4f}")
    check("massing only lowers it too, via SVF and shadow",
          massed.mean() < base_field.mean(),
          f"{massed.mean():.4f} < {base_field.mean():.4f}")
    check("the field stays in [0, 1]",
          float(base_field.min()) >= 0.0 and float(base_field.max()) <= 1.0, "")

    import re as _re
    dm_src = (BACKEND_DIR / "app/services/design_metrics.py").read_text()
    check("the HEAT_INDEX table is not duplicated in design_metrics",
          not _re.search(r"HEAT_INDEX\s*=", dm_src)
          and not _re.search(r"^\s*(from|import)\s+.*HEAT_INDEX", dm_src, _re.M),
          "defined once in _overlay_common, as the design requires")

    # No calibration file is on disk, and this test must NOT require one. The
    # no-data branch is the honesty check: the mechanism ships unanchored.
    calibration = load_heat_calibration()
    report = heat_field_report(land_flat, svf_flat, shad_flat,
                               calibration=calibration)
    anchor = report["anchoring"]
    if calibration is None:
        check("with no calibration file the anchor source is 'none'",
              anchor["anchorSource"] == "none", str(anchor["anchorSource"]))
        check("and no range or path is invented",
              anchor["anchorRangeC"] is None and anchor["calibrationPath"] is None, "")
        check("the heat status is the relative-index label",
              report["status"] == "RELATIVE_INDEX_NOT_A_TEMPERATURE", report["status"])
        check("and the land index is still called hand-authored",
              anchor["heatIndexSource"]
              == "Hand-authored screening index; awaiting verification",
              anchor["heatIndexSource"])
    else:
        check("a present calibration is labelled from its quantity, not its product",
              anchor["anchorSource"] == "hko_station"
              and anchor["quantity"] == "HKO_air"
              and anchor["anchorRangeC"] == [26.4, 32.7]
              and anchor["calibrationPath"] == "reference/heat_anchor.json"
              and anchor["status"] == "ANCHORED_TO_PUBLISHED_REFERENCE",
              f"{anchor['anchorSource']} {anchor['anchorRangeC']} {anchor['status']}")
        check("and the land index is still called hand-authored",
              anchor["heatIndexSource"]
              == "Hand-authored screening index; awaiting verification",
              anchor["heatIndexSource"])
        check("the anchored warning names the source, the band and the site caveat",
              "Lau Fau Shan" in anchor["warning"]
              and "26.4-32.7" in anchor["warning"]
              and "not a measurement of this site" in anchor["warning"],
              anchor["warning"])
    check("the heat warning carries both mandated phrases",
          "Heat is not a temperature" in report["warning"]
          and "Screening-level; relative, not absolute" in report["warning"], "")

    # --- The anchor contract (design §8.2) --- #
    #
    # 1. The field is never a temperature. Scan the served report for a key that
    #    looks like a Celsius field; the anchor legitimately carries a band, but
    #    the *field* must not, on any path.
    def _celsius_like_keys(obj, path=""):
        hits = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if _re.search(r"(?i)(temp|temperature|lst).*c$", key) or \
                        _re.search(r"(?i)(celsius|degreesc)", key):
                    hits.append(f"{path}.{key}")
                hits.extend(_celsius_like_keys(value, f"{path}.{key}"))
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                hits.extend(_celsius_like_keys(value, f"{path}[{i}]"))
        return hits

    check("no key on the heat report looks like a Celsius temperature field",
          not _celsius_like_keys(report), str(_celsius_like_keys(report)))
    check("the mean and max index stay on the 0-1 scale",
          0.0 <= report["meanHeatIndex"] <= 1.0 and report["maxHeatIndex"] <= 1.0,
          f"{report['meanHeatIndex']} / {report['maxHeatIndex']}")
    from _overlay_common import HEAT_INDEX as _HI
    check("the hand-authored land-class index is pinned",
          _HI == {"paved": 1.0, "bare": 0.85, "green": 0.40,
                  "wetland": 0.20, "water": 0.05}, str(_HI))

    # 2. Determinism / no runtime network on the *heat path*: neither the heat
    #    service nor the heat route may import a network library, and nothing in
    #    app/ may import the fetcher. (ai_plan.py uses urllib for the separate,
    #    opt-in LLM provider -- a different path; this check is scoped to the heat
    #    path so it cannot be defeated by an unrelated module.)
    app_dir = BACKEND_DIR / "app"
    heat_path = [app_dir / "services/design_metrics.py", app_dir / "routers/scenes.py"]
    net_hits = []
    for src in heat_path:
        text = src.read_text()
        if _re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|socket)\b",
                      text, _re.M):
            net_hits.append(str(src.relative_to(BACKEND_DIR)))
    check("no heat-path module imports a network library",
          not net_hits, ", ".join(net_hits))
    app_fetcher_hits = [str(src.relative_to(BACKEND_DIR))
                        for src in app_dir.rglob("*.py")
                        if "fetch_heat_anchor" in src.read_text()]
    check("nothing under app/ imports the anchor fetcher",
          not app_fetcher_hits, ", ".join(app_fetcher_hits))
    check("load_heat_calibration reads only the committed calibration path",
          "settings.data_dir / HEAT_CALIBRATION_PATH"
          in (BACKEND_DIR / "app/services/design_metrics.py").read_text(), "")

    # 3. The parser, offline: the HKO sentinel "***" must be dropped, never read
    #    as 0 (which cools the band) or NaN (which poisons the mean). Includes the
    #    exact sentinel row the PM observed live.
    from fetch_heat_anchor import parse_window
    fixture = {
        "data": [
            ["2023", "7", "1", "26.0", "C"],
            ["2023", "7", "2", "27.0", "C"],
            ["2023", "7", "3", "***", ""],       # sentinel: dropped, not 0
            ["2024", "1", "14", "***", ""],      # the observed row, wrong month
            ["2024", "7", "4", "28.0", "C"],
            ["2010", "7", "5", "99.0", "C"],     # outside the window
        ]
    }
    values, stats = parse_window(fixture, 7, 2015, 2024)
    check("the parser drops the sentinel and counts it",
          values == [26.0, 27.0, 28.0] and stats["dropped"] == 1
          and stats["rows"] == 4,
          f"values={values} stats={stats}")
    check("the sentinel did not enter the mean as 0",
          abs(sum(values) / len(values) - 27.0) < 1e-9,
          f"mean {sum(values) / len(values):.4f}")

    # 4. A malformed calibration must fall back to the honest unanchored path,
    #    never a half-anchored block. Exercised on a temp dir, not the real file.
    import tempfile
    import unittest.mock as _mock
    from app.config import settings as _settings
    with tempfile.TemporaryDirectory() as _td:
        _ref = Path(_td) / "reference"
        _ref.mkdir()
        _bad = [
            {"quantity": "BOGUS", "rangeC": [1.0, 2.0], "mapsTo": "range"},
            {"quantity": "HKO_air", "mapsTo": "range"},          # no rangeC
            {"quantity": "HKO_air", "rangeC": [30.0, 20.0], "mapsTo": "range"},
            {"quantity": "HKO_air", "rangeC": [20.0, 30.0], "mapsTo": "elsewhere"},
        ]
        _ok = True
        for _doc in _bad:
            (_ref / "heat_anchor.json").write_text(json.dumps(_doc))
            with _mock.patch.object(_settings, "data_dir", Path(_td)):
                _loaded = load_heat_calibration()
                _blk = heat_field_report(land_flat, svf_flat, shad_flat,
                                         calibration=_loaded)["anchoring"]
            if _loaded is not None or _blk["anchorSource"] != "none":
                _ok = False
                break
        check("a malformed calibration returns None -> anchorSource 'none'",
              _ok, "the honest fallback, not a half-anchored block")

    # 5/6. The committed sidecar: the anchoring block lives once (nested), and the
    #      render day is the summer default. Two spellings of one value drift;
    #      a winter render under a July band is the inconsistency a judge sees.
    sidecar_path = _settings.rasters_dir / "heatmap_hsk.json"
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text())
        dup = [k for k in ("anchorSource", "anchorRangeC", "calibrationPath",
                           "quantity", "heatIndexSource") if k in sidecar]
        check("the sidecar keeps the anchoring block once, nested",
              not dup, str(dup))
        check("the sidecar render day is the summer default (196)",
              sidecar.get("blend", {}).get("solarDayOfYear") == 196,
              str(sidecar.get("blend", {}).get("solarDayOfYear")))
        check("the sidecar anchoring is anchored to the station",
              sidecar.get("anchoring", {}).get("anchorSource") == "hko_station",
              str(sidecar.get("anchoring", {}).get("anchorSource")))
    else:
        check("the heat sidecar is committed", False, f"missing {sidecar_path}")

    # --- Stage 5b: per-cell FAD mean preservation (the Q3 correctness check) --- #
    #
    # The area-weighted mean of cell_fad must equal the site-level FAD exactly.
    # This is the assertion that catches a wrong denominator (site area instead
    # of cell area) or a footprint stamped at full value on every cell it covers
    # rather than spread. Both leave a plausible-looking cost surface.
    from app.services.design_metrics import cell_fad

    class _SynthGrid:
        rows = 60
        cols = 60
        cell_area_m2 = 25.0

        def cell_of(self, lon, lat):
            return (int(lat), int(lon))

    sg = _SynthGrid()
    site = sg.rows * sg.cols * sg.cell_area_m2
    synth = [Footprint(10.0, 10.0, 10.0, 10.0, 20.0),
             Footprint(30.0, 30.0, 10.0, 10.0, 40.0),
             Footprint(45.0, 15.0, 20.0, 10.0, 15.0)]
    worst_rel, worst_abs = 0.0, 0.0
    for name, bearing in (("N", 0.0), ("NE", 45.0), ("E", 90.0), ("SE", 135.0),
                          ("S", 180.0), ("SW", 225.0), ("W", 270.0), ("NW", 315.0)):
        fad_raster = cell_fad(synth, sg, bearing)
        lhs = float((fad_raster * sg.cell_area_m2).sum() / site)
        raw = sum(projected_width_m(f.width_m, f.depth_m, bearing,
                                    f.bearing_deg) * f.height_m
                  for f in synth) / site
        worst_rel = max(worst_rel, abs(lhs - raw) / max(raw, 1e-12))
        public = frontal_area_density(synth, site)["byDirection"][name]
        worst_abs = max(worst_abs, abs(lhs - public))
    check("the area-weighted mean of cell_fad equals the site-level FAD",
          worst_rel < 1e-6,
          f"worst relative error {worst_rel:.2e} over 8 directions "
          f"(Q3: cell-area denominator, spread not stamped)")
    # frontal_area_density rounds byDirection to 4 dp, so the public value can
    # only be matched to that rounding -- 5e-5, not 1e-6. The 1e-6 identity above
    # is against the unrounded canonical sum.
    check("and the rounded public FAD agrees to its own 4-dp rounding",
          worst_abs < 5e-5, f"worst absolute gap {worst_abs:.2e}")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
