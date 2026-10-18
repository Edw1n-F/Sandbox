#!/usr/bin/env python
"""Rasterise the real watercourse (Tin Shui Wai Nullah) into a channel mask.

    python scripts/make_waterway_mask.py

Why this exists
---------------

The reservoir candidate pool excludes the *channel* — the ground where water
already flows away, the inverse of flood risk. The first attempt derived the
channel from the D8 flow-accumulation top quantile, but on this extract the
visible watercourse is NOT uniformly deep-blue in the accumulation field, so
the quantile cut misses most of it.

The authoritative source for "where is the actual watercourse" is OpenStreetMap:
the study area (Tin Shui Wai, Hong Kong) is crossed by the **天水圍渠 Tin Shui
Wai Nullah**, a surveyed open drainage nullah tagged ``waterway=drain``. The
Open3Dhk WATERBODY tiles turned out to be only a handful of small ponds, not the
nullah, so they are the wrong authority here. This script rasterises the OSM
waterway ways (drain / stream / ditch) that cross the grid, buffered to a
plausible channel half-width, onto the same 117 x 148 (5 m) analysis grid the
rest of the pipeline uses.

Source: OpenStreetMap, ODbL. The raw Overpass response is kept at
``data/reference/waterways_raw.json`` (fetched 18 Oct 2026).

Outputs into ``backend/data/rasters/``:
- ``waterway_hsk.npy``   bool, True where a cell centre falls within the channel

The mask is a *hard exclusion* for the reservoir pool, replacing the
flow-accumulation quantile channel cut: a surveyed nullah is measured geometry,
not a derived proxy.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath
from pyproj import Transformer

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

OSM_WATERWAYS = BACKEND_DIR / "data" / "reference" / "waterways_raw.json"

# Grid geometry, matching tiles_to_rasters.py / _overlay_common exactly.
ROWS, COLS = 117, 148
RESOLUTION_M = 5.0
WEST, SOUTH, EAST, NORTH = 113.9922, 22.4310, 113.9994, 22.4363

# Waterway kinds treated as "the channel". A nullah/drain/stream/ditch all carry
# water away; a standing ``natural=water`` polygon is a pond, not the channel.
WATERWAY_KINDS = {"drain", "stream", "ditch", "canal", "river"}

# Channel half-width in metres. The Tin Shui Wai Nullah is a wide concrete
# nullah (several tens of metres), but the exclusion only needs to cover the
# channel bed a basin must not sit on. 15 m (3 cells) is a defensible screening
# width: it keeps the nullah itself out without claiming the whole floodplain.
CHANNEL_HALF_WIDTH_M = 15.0


def _way_polylines(raw: dict) -> list[np.ndarray]:
    """World (HK1980 E, N) polylines for every waterway way in the response."""
    tr = Transformer.from_crs(4326, 2326, always_xy=True)
    out: list[np.ndarray] = []
    for e in raw.get("elements", []):
        tags = e.get("tags", {})
        if tags.get("waterway") not in WATERWAY_KINDS:
            continue
        geom = e.get("geometry")
        if not geom:
            continue
        lons = [p["lon"] for p in geom]
        lats = [p["lat"] for p in geom]
        E, N = tr.transform(lons, lats)
        out.append(np.column_stack([np.asarray(E), np.asarray(N)]))
    return out


def _buffered_paths(polylines: list[np.ndarray], half_width: float) -> list[MplPath]:
    """Turn each polyline into a closed polygon of ``half_width`` on each side.

    Builds left/right offset vertices from the segment normals, so a narrow
    nullah becomes a closed ribbon the point-in-polygon test can use.
    """
    paths: list[MplPath] = []
    for line in polylines:
        if len(line) < 2:
            continue
        d = np.diff(line, axis=0)
        seglen = np.linalg.norm(d, axis=1)
        seglen[seglen == 0] = 1.0
        n = np.column_stack([-d[:, 1], d[:, 0]]) / seglen[:, None]
        left = line[:-1] + n * half_width
        right = line[:-1] - n * half_width
        # A closed ribbon: forward along the left, back along the reversed right.
        ring = np.vstack([left, right[::-1], left[0]])
        paths.append(MplPath(ring))
    return paths


def rasterise() -> tuple[np.ndarray, dict]:
    raw = json.loads(OSM_WATERWAYS.read_text(encoding="utf-8"))
    polylines = _way_polylines(raw)
    if not polylines:
        raise RuntimeError("no waterway ways in the OSM response")

    tr = Transformer.from_crs(4326, 2326, always_xy=True)
    lon = WEST + (np.arange(COLS) + 0.5) / COLS * (EAST - WEST)
    lat = NORTH - (np.arange(ROWS) + 0.5) / ROWS * (NORTH - SOUTH)
    lon_g, lat_g = np.meshgrid(lon, lat)
    E, N = tr.transform(lon_g, lat_g)

    paths = _buffered_paths(polylines, CHANNEL_HALF_WIDTH_M)
    mask = np.zeros((ROWS, COLS), dtype=bool)
    pts = np.column_stack([E.ravel(), N.ravel()])
    for path in paths:
        mask |= path.contains_points(pts).reshape(ROWS, COLS)

    meta = {
        "artefact": "waterway-mask",
        "generated": date.today().isoformat(),
        "status": "REAL_WATERWAY_FROM_OSM",
        "source": {
            "name": "OpenStreetMap waterway ways (Tin Shui Wai Nullah)",
            "path": str(OSM_WATERWAYS),
            "crs": "EPSG:2326 (HK1980)",
            "licence": "ODbL",
            "ways": len(polylines),
            "channelHalfWidthM": CHANNEL_HALF_WIDTH_M,
            "method": "waterway ways buffered to a ribbon and point-in-polygon "
                      "rasterised onto the analysis grid",
        },
        "grid": {
            "rows": ROWS, "cols": COLS, "resolutionM": RESOLUTION_M,
            "bounds": [WEST, SOUTH, EAST, NORTH],
            "orientation": "north-up, row 0 = northernmost",
        },
        "results": {
            "waterwayCells": int(mask.sum()),
            "waterwayFraction": round(float(mask.mean()), 4),
        },
        "caveats": [
            "Channel width is a screening constant (half-width 15 m), not a survey "
            "of the nullah's true bank-to-bank width.",
            "The mask covers the channel bed a basin must not sit on; the broader "
            "floodplain is deliberately left in the candidate pool.",
        ],
    }
    return mask, meta


def main() -> int:
    mask, meta = rasterise()
    out = settings.rasters_dir / "waterway_hsk.npy"
    np.save(out, mask)
    out.with_suffix(".json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"[waterway] {out}")
    print(f"[waterway] {meta['results']['waterwayCells']} cells "
          f"({meta['results']['waterwayFraction'] * 100:.2f}% of extent) "
          f"from {meta['source']['ways']} ways")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
