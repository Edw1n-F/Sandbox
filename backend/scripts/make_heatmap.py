#!/usr/bin/env python
"""Heat overlay (D3): observed LST + land class + sky view + shade + wind shelter
-> surface-temperature anomaly field -> diverging RGBA PNG.

    python scripts/make_heatmap.py

This is the offline twin of the live run's heatmap: the same
``temperature_field`` model and the same ``anomaly_rgba`` diverging ramp as
``app/services/run_service.py``, so the pre-run baseline overlay and a live run
overlay are rendered by one code path and cannot drift.

The field is anchored to an observed Landsat 8/9 LST scene (canonical capture
2022-09-03) and deviates from the site mean by geometry: paved and bare hold
heat, green is intermediate, water and wetland cool through evaporation; a cell
that loses sky to a nearby tower, sits in its shadow, or is wind-blocked is
hotter or cooler than the same land class in the open. The rendered map is the
**anomaly** (deviation from the site mean, in C): blue = cooler, neutral =
average, red = a genuine hot spot. A well-designed site reads near-neutral --
that is the point, and it is why the neutral band of the ramp is narrow.

The old 0-1 relative heat index and its `--heatwave` display stretch are gone:
temperature is now a scenario input (the slider sets the scenario mean surface
temperature, and the field re-anchors), not a baked display transform.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.design_metrics import (  # noqa: E402
    SITE_LAT_DEG,
    SITE_LON_DEG,
    HEAT_STATUS,
    HEAT_SUMMER_DAY_OF_YEAR,
    cell_fad,
    footprints_from_geojson,
    rasterise_height,
    shade_mask,
    sky_view_factor_raster,
)
from app.services.run_service import anomaly_rgba  # noqa: E402  (one ramp, one home)
from app.services.temperature_field import (  # noqa: E402
    load_observed_lst,
    temperature_field,
)
from _overlay_common import (  # noqa: E402
    LAND_CLASSES,
    attach_to_scene,
    load_overlay,
)


def _load_city_model() -> list[dict] | None:
    """The procedural massing features, or None when they have not been built."""
    path = settings.rasters_dir / "buildings_hsk.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("features")
    except (OSError, ValueError):
        return None


def heat_index(land: np.ndarray) -> np.ndarray:
    """Land-class indices -> relative heat-exposure index in 0..1.

    The *input* to the temperature field's land-class deviation term, not the
    rendered quantity.
    """
    from _overlay_common import HEAT_INDEX

    lut = np.array([HEAT_INDEX[name] for name in LAND_CLASSES], dtype=np.float32)
    return lut[land]


def geometry_terms(ov, day_of_year: int, hour: float) -> dict[str, np.ndarray] | None:
    """SVF / shaded / FAD rasters for the current massing, or None without one.

    Same solar inputs as the live run (14:00 on the July anchor day) and the
    same NE-wind FAD bearing (45 deg), so the offline overlay and a live run
    differ only in scenario mean -- never in sun or wind geometry.
    """
    features = _load_city_model()
    if not features:
        return None
    fps = footprints_from_geojson(features)
    height = rasterise_height(fps, ov)
    cell_m = float(ov.cell_area_m2) ** 0.5
    return {
        "svf": sky_view_factor_raster(height, cell_m),
        "shaded": shade_mask(fps, ov, SITE_LAT_DEG, SITE_LON_DEG, day_of_year,
                             hour).astype(np.float32),
        "fad": cell_fad(fps, ov, 45.0).astype(np.float32),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=float, default=5.0, help="cell size, m")
    parser.add_argument("--day-of-year", type=int, default=HEAT_SUMMER_DAY_OF_YEAR,
                        help="solar day for the shadow term (default 196, 15 July "
                             "-- matches the July anchor)")
    parser.add_argument("--hour", type=float, default=14.0,
                        help="local solar hour for the shadow term (default 14)")
    parser.add_argument(
        "--out", default=None, help="output PNG (default data/rasters/heatmap_hsk.png)"
    )
    args = parser.parse_args()

    ov = load_overlay(args.resolution)
    land_index = heat_index(ov.land_class)
    terms = geometry_terms(ov, args.day_of_year, args.hour)
    lst_base, lst_meta = load_observed_lst(ov.rows, ov.cols)

    # No scenario mean: the baseline anchors to the observed LST scene's own
    # mean. The slider (a run input) is what moves the mean.
    fields = temperature_field(
        land_index=land_index,
        svf=terms["svf"] if terms else np.ones(ov.land_class.shape, dtype=np.float32),
        shaded=terms["shaded"] if terms else np.zeros(ov.land_class.shape, dtype=np.float32),
        fad=terms["fad"] if terms else np.zeros(ov.land_class.shape, dtype=np.float32),
        lst_base=lst_base,
        scenario_mean_c=None,
    )

    landcover_meta: dict = {}
    _lc = settings.data_dir / "landcover_hsk.json"
    if _lc.exists():
        try:
            landcover_meta = json.loads(_lc.read_text())
        except (OSError, ValueError):
            landcover_meta = {}

    out = Path(args.out) if args.out else settings.rasters_dir / "heatmap_hsk.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(anomaly_rgba(fields["anomalyC"]), mode="RGBA").save(out)

    cell_area = ov.cell_area_m2
    counts = {
        name: int((ov.land_class == i).sum()) for i, name in enumerate(LAND_CLASSES)
    }
    area_ha = {k: round(v * cell_area / 10_000.0, 2) for k, v in counts.items()}
    anomaly = fields["anomalyC"]

    meta = {
        "artefact": "heatmap",
        "generated": date.today().isoformat(),
        "status": lst_meta["available"] and "SURFACE_TEMPERATURE_ANCHORED_TO_LST"
                  or "SURFACE_TEMPERATURE_NO_LST_BASE",
        "warning": (
            "Surface temperature anchored to an observed Landsat LST scene; the "
            "geometry terms (land class, sky view, shade, wind shelter) shift "
            "cells away from the site mean as deviations in degrees C. "
            "Screening output, not a meteorological forecast."
        ),
        "temperature": {
            "surfaceMeanC": round(float(fields["meanSurfaceC"]), 2),
            "surfaceMaxC": round(float(fields["maxSurfaceC"]), 2),
            "meanAnomalyC": round(float(fields["meanAnomalyC"]), 3),
            "maxAnomalyC": round(float(fields["maxAnomalyC"]), 2),
            "airMeanC": round(float(fields["meanSurfaceC"]) - float(fields["surfaceToAirOffsetC"]), 2),
            "observedMeanC": lst_meta.get("observedMeanC"),
            "lstSource": lst_meta.get("source"),
            "lstAvailable": lst_meta["available"],
        },
        "inputs": {
            "resolution_m": args.resolution,
            "rows": ov.rows,
            "cols": ov.cols,
            "bounds": list(ov.bounds),
            "cellAreaM2": cell_area,
            "solarDayOfYear": args.day_of_year,
            "solarHour": args.hour,
        },
        "assumptions": {
            "windBearingDeg": 45.0,
            "airOffsetC": 12.0,
            "note": ("Air temperature is estimated as surface minus 12 C, the "
                     "observed LST-air gap for this scene type. No "
                     "evapotranspiration model, so temperature never moves "
                     "rainfall or runoff."),
        },
        "results": {
            "meanAnomalyC": round(float(anomaly.mean()), 4),
            "maxAnomalyC": round(float(anomaly.max()), 4),
            "cellCountByClass": counts,
            "areaHaByClass": area_ha,
            "imperviousShare": round(
                (area_ha["paved"]) / max(sum(area_ha.values()), 1e-9), 4
            ),
        },
        "landClasses": list(LAND_CLASSES),
        "landClassProvenance": (
            f"ESA WorldCover 10 m v200 (2021), CC BY 4.0, tiles "
            f"{landcover_meta.get('source', {}).get('tiles')}, resampled to "
            f"{args.resolution:g} m by nearest neighbour. See "
            f"data/landcover_hsk.json for the ESA->ours class mapping."
            if ov.landcover_source == "esa-worldcover"
            else "SYNTHETIC PLACEHOLDER, not measured land cover. Run "
                 "scripts/fetch_landcover.py to replace it with ESA WorldCover."
        ),
        "landCoverSource": ov.landcover_source,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    rel = attach_to_scene("heatmap_path", out)
    if rel:
        print(f"[attach] heatmap_path -> {rel}")

    print(f"[heatmap] {out}")
    print(f"[heatmap] grid {ov.cols} x {ov.rows} @ {args.resolution} m")
    print(f"[heatmap] surface mean {fields['meanSurfaceC']:.2f} C  "
          f"max {fields['maxSurfaceC']:.2f} C")
    print(f"[heatmap] anomaly  mean {fields['meanAnomalyC']:+.3f} C  "
          f"max {fields['maxAnomalyC']:+.2f} C")
    print(f"[heatmap] air mean (surface {fields['surfaceToAirOffsetC']:+.0f}) "
          f"{fields['meanSurfaceC'] - fields['surfaceToAirOffsetC']:.2f} C")
    print(f"[heatmap] LST base: {lst_meta.get('source')} "
          f"available={lst_meta['available']}")
    print(f"[heatmap] area by class (ha):")
    for name, value in area_ha.items():
        print(f"[heatmap]   {name:<9} {value:>9,.1f}")
    print(f"[heatmap] land cover: {ov.landcover_source}  terrain: {ov.terrain_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
