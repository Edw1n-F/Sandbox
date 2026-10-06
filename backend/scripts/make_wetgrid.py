#!/usr/bin/env python
"""Wet-grid overlay (D2): CN runoff -> storage -> fill from the lowest cells up.

    python scripts/make_wetgrid.py --rainfall 450

Pipeline, in the order the baseline storage ledger requires:

1. **Generation.** SCS-CN runoff depth per cell from the rainfall depth and the
   class Curve Number (hydrologic soil group D).
2. **Retention.** Subtract what the standing water and wetland already hold,
   plus a depression allowance on the non-retention cells. This is the ADR-015
   partition: land cover sets how much *runs off*, explicit storage sets how much
   can be *held*, and no cell is counted in both.
3. **Allocation.** Distribute the leftover volume across cells starting from the
   lowest ground, giving each cell a depth until the volume is exhausted.

The result is a depth field, emitted as an RGBA PNG with a metadata sidecar
carrying every constant, so the number can be defended or dismissed later.

**The surface is synthetic** (see `_overlay_common`). This is a screening view of
where water would collect given a plausible topography -- it is not a flood
prediction and must not be presented as one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from _overlay_common import (  # noqa: E402
    attach_to_scene,
    COUNTED_AS,
    CURVE_NUMBER_HSG_D,
    LAND_CLASSES,
    load_overlay,
    runoff_depth,
)

# Depth (m) a water or wetland cell is assumed to already hold. This is the
# "available storage" term and is an ASSUMPTION, not a measurement: the HSK EIA
# deliberately does not publish retention volumes for the flood storage lakes.
STORAGE_DEPTH_M = {"water": 1.50, "wetland": 0.60}

# Additional depression storage on generation cells (m of water held in
# micro-topography before runoff leaves the cell).
DEPRESSION_STORAGE_M = 0.02

# Ramp for the PNG: transparent below this depth, opaque at MAX_VIS_DEPTH_M.
MIN_VIS_DEPTH_M = 0.005
MAX_VIS_DEPTH_M = 0.30


def _fill_to_volume(
    elevation: np.ndarray, volume_m3: float, cell_area_m2: float
) -> tuple[np.ndarray, float]:
    """Solve for the water-surface elevation that holds ``volume_m3``.

    Depth in cell i is ``max(0, W - e_i)``, so volume is monotonic in W and a
    bisection converges unconditionally. Returns the depth field and the level.
    """
    flat = elevation.ravel().astype(np.float64)
    if volume_m3 <= 0.0:
        return np.zeros_like(elevation), float(flat.min())

    def volume_at(level: float) -> float:
        return float(np.maximum(0.0, level - flat).sum() * cell_area_m2)

    lo, hi = float(flat.min()), float(flat.max())
    # Grow the upper bound until it can hold the requested volume.
    span = max(hi - lo, 1.0)
    while volume_at(hi) < volume_m3 and (hi - lo) < span * 100.0:
        hi += span
    if volume_at(hi) < volume_m3:
        # More water than the basin can hold: everything is submerged.
        depth = np.maximum(0.0, hi - flat).reshape(elevation.shape)
        return depth, hi

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if volume_at(mid) < volume_m3:
            lo = mid
        else:
            hi = mid
    level = 0.5 * (lo + hi)
    depth = np.maximum(0.0, level - flat).reshape(elevation.shape)
    return depth, level


def compute(rainfall_mm: float, resolution_m: float) -> dict:
    ov = load_overlay(resolution_m)
    cell_area = ov.cell_area_m2()

    # ---- 1. generation -----------------------------------------------------
    cn = np.zeros_like(ov.elevation)
    for name, value in CURVE_NUMBER_HSG_D.items():
        cn[ov.class_mask(name)] = value

    runoff_m = runoff_depth(rainfall_mm, cn)

    # ---- 2. retention ------------------------------------------------------
    storage_m = np.full_like(ov.elevation, DEPRESSION_STORAGE_M)
    for name, depth in STORAGE_DEPTH_M.items():
        storage_m[ov.class_mask(name)] = depth

    # A retention cell generates nothing: an existing pond's contribution is
    # already inside its storage term, so counting it again would double count.
    runoff_m[ov.class_mask("water")] = 0.0

    unstored_m = np.maximum(runoff_m - storage_m, 0.0)

    volume_m3 = float(unstored_m.sum() * cell_area)

    # ---- 3. allocation: fill from the lowest ground up --------------------
    # Physically this is a level pool: find the water-surface elevation W such
    # that the volume of water below it equals the unstored volume. That is
    # exactly "allocate from the lowest cells upward", and unlike spreading a
    # uniform depth over N cells it produces a flat water surface, which is
    # what a real pond looks like and what makes the picture defensible.
    depth_field, water_level = _fill_to_volume(ov.elevation, volume_m3, cell_area)
    wet_cells = int((depth_field > 0).sum())

    return {
        "overlay": ov,
        "rainfall_mm": rainfall_mm,
        "runoff_m": runoff_m,
        "unstored_m": unstored_m,
        "depth_field": depth_field,
        "volume_m3": volume_m3,
        "water_level_m": water_level,
        "wet_cells": wet_cells,
        "wet_area_ha": wet_cells * cell_area / 10_000.0,
        "total_area_ha": ov.rows * ov.cols * cell_area / 10_000.0,
    }


def to_rgba(depth_field: np.ndarray) -> np.ndarray:
    """Depth field -> RGBA, transparent where dry, deep blue where wet."""
    norm = np.clip(
        (depth_field - MIN_VIS_DEPTH_M) / (MAX_VIS_DEPTH_M - MIN_VIS_DEPTH_M), 0.0, 1.0
    )
    alpha = (norm * 235).astype(np.uint8)
    # Deep blue -> cyan as depth increases, so the gradient reads as depth.
    red = (30 * (1.0 - norm)).astype(np.uint8)
    green = (90 + 120 * (1.0 - norm)).astype(np.uint8)
    blue = np.full_like(alpha, 235)
    return np.dstack([red, green, blue, alpha])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rainfall", type=float, default=450.0, help="rainfall depth, mm")
    parser.add_argument("--resolution", type=float, default=5.0, help="cell size, m")
    parser.add_argument(
        "--out", default=None, help="output PNG (default data/rasters/wetgrid_hsk.png)"
    )
    args = parser.parse_args()

    result = compute(args.rainfall, args.resolution)
    ov = result["overlay"]

    out = Path(args.out) if args.out else settings.rasters_dir / "wetgrid_hsk.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(to_rgba(result["depth_field"]), mode="RGBA").save(out)

    meta = {
        "artefact": "wetgrid",
        "generated": "2026-10-06",
        "status": "SCREENING_VIEW_SYNTHETIC_TERRAIN",
        "warning": (
            "Terrain is SYNTHETIC (see _overlay_common). No DEM was available, so "
            "this shows where water would collect over a plausible surface. It is "
            "not a flood prediction and carries no surveyed elevation."
        ),
        "inputs": {
            "rainfall_mm": args.rainfall,
            "resolution_m": args.resolution,
            "rows": ov.rows,
            "cols": ov.cols,
            "bounds": list(ov.bounds),
            "cell_area_m2": ov.cell_area_m2(),
        },
        "assumptions": {
            "hydrologicSoilGroup": "D",
            "curveNumberByClass": CURVE_NUMBER_HSG_D,
            "curveNumberSource": "USDA NRCS TR-55; awaiting independent verification",
            "storageDepthM": STORAGE_DEPTH_M,
            "depressionStorageM": DEPRESSION_STORAGE_M,
            "countedAs": COUNTED_AS,
            "doubleCountingRule": (
                "Land cover sets the generation term; explicit storage sets the "
                "retention term. Open water generates nothing so a pond is never "
                "counted in both (ADR-015)."
            ),
            "verticalDatum": "mPD",
        },
        "results": {
            "runoffVolumeM3": round(
                float(result["runoff_m"].sum() * ov.cell_area_m2()), 1
            ),
            "unstoredVolumeM3": round(result["volume_m3"], 1),
            "wetCells": result["wet_cells"],
            "wetAreaHa": round(result["wet_area_ha"], 2),
            "totalAreaHa": round(result["total_area_ha"], 2),
            "wetAreaFraction": round(
                result["wet_area_ha"] / max(result["total_area_ha"], 1e-9), 4
            ),
            "meanDepthInWetAreaM": round(
                result["volume_m3"]
                / max(result["wet_cells"] * ov.cell_area_m2(), 1e-9),
                4,
            ),
        },
        "render": {
            "minVisibleDepthM": MIN_VIS_DEPTH_M,
            "maxVisibleDepthM": MAX_VIS_DEPTH_M,
            "note": "Depth beyond maxVisibleDepthM is visually saturated, not capped in the data.",
        },
        "landClasses": list(LAND_CLASSES),
        "landClassProvenance": (
            "SYNTHETIC PLACEHOLDER, not measured land cover. Replace by dropping "
            "landclass_hsk.npy into backend/data/."
        ),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    rel = attach_to_scene("wet_grid_path", out)
    if rel:
        print(f"[attach] wet_grid_path -> {rel}")

    r = result
    print(f"[wetgrid] {out}")
    print(f"[wetgrid] grid {ov.cols} x {ov.rows} @ {args.resolution} m")
    print(f"[wetgrid] runoff      {meta['results']['runoffVolumeM3']:>12,.0f} m3")
    print(f"[wetgrid] unstored    {r['volume_m3']:>12,.0f} m3")
    print(f"[wetgrid] wet area    {r['wet_area_ha']:>12,.1f} ha "
          f"({meta['results']['wetAreaFraction'] * 100:.1f}% of extent)")
    print(f"[wetgrid] mean depth {meta['results']['meanDepthInWetAreaM']:>12.3f} m")
    print("[wetgrid] SYNTHETIC TERRAIN - screening view, not a flood prediction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
