#!/usr/bin/env python
"""Heat-exposure overlay (D3): land class -> relative index -> RGBA PNG.

    python scripts/make_heatmap.py --heatwave

Per-cell heat-exposure index from the land-class grid: paved and bare hold heat,
green is intermediate, water and wetland cool through evaporation. The result is
a *relative* screening index on a 0-1 scale -- **not** a temperature in degrees,
and not a modelled air-temperature field.

The `--heatwave` preset stretches the scale upward, standing in for the
"heatwave preset" in the Oct 6 spec. It is a display transform, deliberately
labelled as such: it changes how the same field is *shown*, never the field
itself, so a screenshot taken under the preset cannot be mistaken for a
different scenario.

Unlike the wet grid, this needs no terrain, which is why it is the overlay that
can honestly ship today.
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
    HEAT_INDEX,
    LAND_CLASSES,
    attach_to_scene,
    load_overlay,
)

# Display transform only. Scenario parameters belong on a simulation run, not
# baked into the renderer (ADR-024).
HEATWAVE_STRETCH = 0.7  # multiplies the index, compressing the top of the scale

# Perceptually ordered ramp for the index, cool -> hot.
RAMP = (
    (0.00, (33, 102, 172)),   # deep blue   - water, coolest
    (0.25, (66, 146, 198)),   # blue
    (0.45, (120, 195, 167)),  # teal-green  - green space
    (0.62, (240, 226, 150)),  # pale yellow
    (0.80, (233, 150, 80)),   # orange      - bare
    (1.00, (176, 42, 55)),    # dark red    - paved, hottest
)


def heat_index(land: np.ndarray) -> np.ndarray:
    """Land-class indices -> relative heat-exposure index in 0..1."""
    lut = np.array([HEAT_INDEX[name] for name in LAND_CLASSES], dtype=np.float64)
    return lut[land]


def ramp_rgba(index: np.ndarray) -> np.ndarray:
    """Map a 0..1 index field to RGBA through the display ramp.

    The outermost 1-pixel border is forced to alpha=0. Without this, Cesium's
    SingleTileImageryProvider clamps UV to the edge pixel, which visibly
    bleeds the boundary colour as a 1-pixel streak at the tile edge.
    """
    stops = np.array([s[0] for s in RAMP], dtype=np.float64)
    colours = np.array([s[1] for s in RAMP], dtype=np.float64)
    clipped = np.clip(index, 0.0, 1.0)

    out = np.empty(index.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = np.interp(clipped, stops, colours[:, channel])
    out[..., 3] = 255
    # Transparent border: prevents clamp-to-edge streaks at the raster boundary
    out[0, :, 3] = 0
    out[-1, :, 3] = 0
    out[:, 0, 3] = 0
    out[:, -1, 3] = 0
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=float, default=5.0, help="cell size, m")
    parser.add_argument(
        "--heatwave",
        action="store_true",
        help="apply the heatwave display stretch (display only, not a scenario)",
    )
    parser.add_argument(
        "--out", default=None, help="output PNG (default data/rasters/heatmap_hsk.png)"
    )
    args = parser.parse_args()

    ov = load_overlay(args.resolution)
    index = heat_index(ov.land_class)

    if args.heatwave:
        index = np.clip(index * HEATWAVE_STRETCH, 0.0, 1.0)

    landcover_meta: dict = {}
    _lc = settings.data_dir / "landcover_hsk.json"
    if _lc.exists():
        import json as _json
        try:
            landcover_meta = _json.loads(_lc.read_text())
        except (OSError, ValueError):
            landcover_meta = {}

    out = Path(args.out) if args.out else settings.rasters_dir / "heatmap_hsk.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(ramp_rgba(index), mode="RGBA").save(out)

    cell_area = ov.cell_area_m2()
    counts = {
        name: int((ov.land_class == i).sum()) for i, name in enumerate(LAND_CLASSES)
    }
    area_ha = {k: round(v * cell_area / 10_000.0, 2) for k, v in counts.items()}
    mean_index = float(index.mean())

    meta = {
        "artefact": "heatmap",
        "generated": "2026-10-06",
        "status": "RELATIVE_INDEX_NOT_A_TEMPERATURE",
        "warning": (
            "This is a relative heat-exposure index on a 0-1 scale derived from land "
            "class. It is NOT degrees Celsius and NOT a modelled air-temperature "
            "field. Do not present it as either."
        ),
        "inputs": {
            "resolution_m": args.resolution,
            "rows": ov.rows,
            "cols": ov.cols,
            "bounds": list(ov.bounds),
            "cellAreaM2": cell_area,
        },
        "assumptions": {
            "heatIndexByClass": HEAT_INDEX,
            "heatIndexSource": "Hand-authored screening index; awaiting verification",
            "heatwaveStretch": HEATWAVE_STRETCH if args.heatwave else None,
            "heatwaveNote": (
                "A display transform on the same field, not a scenario. Compare "
                "runs at the same stretch setting."
            ),
        },
        "results": {
            "meanIndex": round(mean_index, 4),
            "maxIndex": round(float(index.max()), 4),
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
    print(f"[heatmap] mean index   {mean_index:>8.3f}")
    print(f"[heatmap] area by class (ha):")
    for name, value in area_ha.items():
        print(f"[heatmap]   {name:<9} {value:>9,.1f}")
    print(f"[heatmap] land cover: {ov.landcover_source}  terrain: {ov.terrain_source}")
    print(f"[heatmap] RELATIVE INDEX - not degrees, not a modelled temperature field")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
