"""Tests for the LST-anchored surface-temperature field.

These pin the new ``temperature_field`` pipeline: the observed LST base, the
geometry delta (land class + sky view + shade + wind shelter), the scenario
mean re-anchor, and the surface-to-air offset. They run without a live city
model or network -- the LST data is on disk under ``data/reference/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import numpy as np  # noqa: E402

from app.services.temperature_field import (  # noqa: E402
    CANONICAL_SCENE_ID,
    SURFACE_TO_AIR_OFFSET_C,
    load_observed_lst,
    resample_to_grid,
    temperature_field,
)


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  {status}  {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(name)


def main() -> int:
    print("=== temperature_field ===")

    # ---- 1. the observed LST loads and resamples ---------------------------
    base, meta = load_observed_lst(117, 148)
    check("the canonical LST scene is available on disk", meta["available"])
    check("it is the 2022-09-03 clean scene",
          meta["sceneId"] == CANONICAL_SCENE_ID)
    check("the observed mean is a real summer surface temperature",
          38.0 < meta["observedMeanC"] < 52.0, str(meta["observedMeanC"]))
    check("the resampled base is the full 5 m grid shape",
          base is not None and base.shape == (117, 148),
          str(None if base is None else base.shape))

    # ---- 2. resample preserves the mean (bilinear, same bbox) --------------
    src = np.random.default_rng(0).uniform(38.0, 46.0, (20, 24)).astype(np.float32)
    out = resample_to_grid(src, 117, 148)
    check("resample keeps the mean within 1% of the source",
          abs(float(out.mean()) - float(src.mean())) < 0.01 * float(src.mean()),
          f"{out.mean():.2f} vs {src.mean():.2f}")

    # ---- 3. the scenario mean re-anchor is exact ----------------------------
    shape = (117, 148)
    land = np.random.default_rng(1).uniform(0.0, 1.0, shape).astype(np.float32)
    svf = np.random.default_rng(2).uniform(0.3, 1.0, shape).astype(np.float32)
    shaded = np.random.default_rng(3).uniform(0.0, 1.0, shape).astype(np.float32)
    fad = (np.random.default_rng(4).uniform(0.0, 0.4, shape)).astype(np.float32)

    r = temperature_field(land_index=land, svf=svf, shaded=shaded, fad=fad,
                          lst_base=base, scenario_mean_c=47.0)
    check("the field mean matches the requested scenario mean exactly",
          abs(r["meanSurfaceC"] - 47.0) < 0.01, str(r["meanSurfaceC"]))
    check("the anomaly mean is zero by construction",
          abs(r["meanAnomalyC"]) < 1e-4, str(r["meanAnomalyC"]))
    check("the air estimate is surface minus the documented offset",
          abs(r["meanSurfaceC"] - r["airEstimateC"].mean()
              - SURFACE_TO_AIR_OFFSET_C) < 1e-3)
    check("the surface-to-air offset is the documented 12 C",
          SURFACE_TO_AIR_OFFSET_C == 12.0)

    # ---- 4. wind shelter raises temperature, shade lowers it ----------------
    # Spatial variation is required: a uniform field centres to zero, which is
    # the correct "well-designed, nothing stands out" case. Here a band of cells
    # is sheltered (low sky view + high FAD) while the rest stays open. No LST
    # base so the anomaly reflects *only* the geometry terms, not the scene's
    # own spatial structure.
    r_open = temperature_field(
        land_index=np.full(shape, 0.5, np.float32),
        svf=np.ones(shape, np.float32),        # fully open sky
        shaded=np.zeros(shape, np.float32),     # no shade
        fad=np.zeros(shape, np.float32),        # no wind blockage
        lst_base=None)
    # A horizontal band of cells with low sky view and high FAD (wind-blocked).
    svf_band = np.ones(shape, np.float32)
    fad_band = np.zeros(shape, np.float32)
    svf_band[:, 40:80] = 0.3
    fad_band[:, 40:80] = 0.4
    r_sheltered = temperature_field(
        land_index=np.full(shape, 0.5, np.float32),
        svf=svf_band,
        shaded=np.zeros(shape, np.float32),
        fad=fad_band,
        lst_base=None)
    # The sheltered band reads hotter than the open field's max anomaly.
    check("a sheltered, blocked band reads hotter than the open site",
          r_sheltered["maxAnomalyC"] > r_open["maxAnomalyC"],
          f"{r_open['maxAnomalyC']:.2f} vs {r_sheltered['maxAnomalyC']:.2f}")
    check("an open, uniform site reads near-zero anomaly",
          abs(r_open["maxAnomalyC"]) < 1e-4, str(r_open["maxAnomalyC"]))

    # ---- 5. no LST -> synthetic neutral base, still finite ------------------
    r_synth = temperature_field(land_index=land, svf=svf, shaded=shaded, fad=fad,
                                lst_base=None, scenario_mean_c=30.0)
    check("a synthetic base (no LST) still re-anchors to the requested mean",
          abs(r_synth["meanSurfaceC"] - 30.0) < 0.01)
    check("and carries the screening status",
          r_synth["status"].startswith("SCREENING"))

    print("=== temperature_field: all passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
