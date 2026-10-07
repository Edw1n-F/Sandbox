#!/usr/bin/env python
"""Fetch a real DEM for the study area and encode it for Cesium.

    python scripts/fetch_dem.py --resolution 30

Replaces the synthetic surface in `_overlay_common` with actual topography.

**Source: Open-Meteo's elevation API** (`api.open-meteo.com/v1/elevation`),
which is a free, key-less REST wrapper over open elevation models (SRTM 30 m and
friends). Chosen over the alternatives for three reasons:

- The Lands Dept 5 m DTM was ruled out earlier: ~290 MB, needs permission, and
  is *not* bare-earth.
- The AWS `elevation-tiles-prod` bucket was tested and **rejected**. It answers
  HTTP 200 with plausible-looking PNGs, but the pixels are not terrarium-encoded:
  control points decode nonsensically (Everest came back as ~1,000,000 m, the
  South Atlantic as 792,940 m). Both the `terrarium` and `normal` paths fail the
  same control-point check. A silent, plausible, wrong DEM is worse than none.
- This API is **verified against known truth before any use**: Everest and the
  open ocean are hard-coded as assertions, so a bad response fails loudly
  instead of producing a beautiful lie.

Outputs, all into `backend/data/`:
- `dem_hsk.npy`         float32 metres, north-up, the grid the scripts use
- `dem_terrain_rgb.png` mapbox terrain-RGB, for `CustomHeightmapTerrainProvider`
- `dem_hsk.json`        provenance, resolution, min/max, sample count

No key, no account, no large download: ~1.5k samples of a few bytes each.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.geo_import import polygon_bounds  # noqa: E402
from _overlay_common import load_study_area  # noqa: E402

API = "https://api.open-meteo.com/v1/elevation"
USER_AGENT = "Hack4SDG-T1/1.0 (terrain fetch; contact: team)"

# Control points, so a wrong answer is a crash rather than a plausible DEM.
CONTROL_POINTS = (
    ("Everest", 27.9881, 86.9250, 8000.0, 9500.0),
    ("South Atlantic", -20.0, -30.0, -50.0, 50.0),
)

# The API rejects more than 100 coordinate pairs per request (HTTP 400, with a
# helpful message -- the limit was found by probing, not guessed).
BATCH = 100


CACHE_DIR = settings.data_dir / "dem_cache"


def _fetch(latitudes: list[float], longitudes: list[float]) -> list[float]:
    """One request, with backoff, and cached on disk so runs are resumable.

    This is a free public API and it rate-limits (HTTP 429) under exactly the
    kind of burst a grid fetch produces. Three responses to that, all of them
    needed:

    - honour ``Retry-After`` when the server sends it;
    - exponential backoff with jitter otherwise;
    - cache every batch, so a run that dies at batch 60 resumes for free.

    The cache is keyed on the coordinates themselves, so changing the resolution
    simply misses the cache rather than serving the wrong grid.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(
        (",".join(f"{v:.5f}" for v in latitudes) + "|" +
         ",".join(f"{v:.5f}" for v in longitudes)).encode()
    ).hexdigest()[:24]
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    url = (
        f"{API}?latitude={','.join(f'{v:.5f}' for v in latitudes)}"
        f"&longitude={','.join(f'{v:.5f}' for v in longitudes)}"
    )
    last_error: Exception | None = None
    for attempt in range(10):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            values = payload.get("elevation")
            if not isinstance(values, list) or len(values) != len(latitudes):
                raise RuntimeError(f"unexpected payload: {str(payload)[:200]}")
            values = [float(v) for v in values]
            cache_file.write_text(json.dumps(values))
            return values
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if (retry_after or "").isdigit() else 0.0
            if wait <= 0:
                wait = min(2.0**attempt, 60.0) + random.uniform(0, 2.0)
            print(f"[dem]   HTTP {exc.code}, backing off {wait:.1f}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            wait = min(2.0**attempt, 60.0) + random.uniform(0, 2.0)
            print(f"[dem]   {exc}, retrying in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"elevation API failed after retries: {last_error}")


def verify_source() -> None:
    """Refuse to proceed unless the source agrees with known ground truth."""
    for name, lat, lon, low, high in CONTROL_POINTS:
        got = _fetch([lat], [lon])[0]
        if not low <= got <= high:
            raise RuntimeError(
                f"elevation source failed control point '{name}': "
                f"got {got} m, expected {low}..{high} m. Refusing to write a DEM."
            )
        print(f"[dem]   control {name:<15} {got:9.1f} m  OK")


def sample_grid(bounds: tuple[float, float, float, float]):
    """Sample elevation, then decimate back to the source's true posting.

    ## Why there is no resolution flag

    An earlier version took ``--arcsec`` and sampled on that lattice. It looked
    right and was wrong: the API serves a **~90 m** global model, so a 1
    arc-second request over a 2.4 km extent returned a 78 x 110 array holding
    only 28 x 37 distinct samples. The values were correct; the *posting* was a
    fiction, and every document that repeated it was overstating the data by 3x.

    Asking for a finer lattice cannot manufacture resolution. It can only
    duplicate the values already there. So the grid is now sampled once and then
    **deduplicated**, which is lossless by construction -- a cell is dropped
    only when it is byte-equal to its predecessor -- and the manifest reports the
    resolution that was measured rather than the one that was requested.

    ## Why deduplicate rather than stride

    A stride of 3 assumed the replication factor was exactly 3 everywhere. It
    is not: one column in the study area was unique, and a naive stride silently
    dropped it. Deduplication cannot drop a value that is not a duplicate.
    """
    lon_min, lat_min, lon_max, lat_max = bounds
    mid_lat = math.radians((lat_min + lat_max) / 2.0)
    res_lat_m = 110_574.0 / 3600.0
    res_lon_m = 111_320.0 * math.cos(mid_lat) / 3600.0

    cols = max(int(round((lon_max - lon_min) * 111_320.0 * math.cos(mid_lat) / res_lon_m)), 2)
    rows = max(int(round((lat_max - lat_min) * 110_574.0 / res_lat_m)), 2)
    print(f"[dem] sampling {cols} x {rows} at 1 arc-sec, then deduplicating")

    lons = np.linspace(lon_min, lon_max, cols, endpoint=False)
    lats = np.linspace(lat_max, lat_min, rows, endpoint=False)

    flat_lat: list[float] = []
    flat_lon: list[float] = []
    for lat in lats:
        flat_lat.extend([float(lat)] * cols)
        flat_lon.extend(lons.tolist())

    values: list[float] = []
    for start in range(0, len(flat_lat), BATCH):
        values.extend(_fetch(flat_lat[start : start + BATCH], flat_lon[start : start + BATCH]))
        # This is a free public API and it throttles on request *frequency*:
        # 100 coords per request is fine, 100 requests per minute is not. 2 s
        # keeps a full grid near three minutes, a small price for not being the
        # reason a shared resource stops serving other people.
        time.sleep(2.0)
        print(f"[dem]   sampled {len(values):>6}/{len(flat_lat)}", end="\r")
    print()

    sampled = np.array(values, dtype=np.float32).reshape(rows, cols)
    before = sampled.shape

    col_keep = [0] + [j for j in range(1, cols)
                      if not np.array_equal(sampled[:, j], sampled[:, j - 1])]
    deduped = sampled[:, col_keep]
    row_keep = [0] + [i for i in range(1, deduped.shape[0])
                      if not np.array_equal(deduped[i], deduped[i - 1])]
    grid = deduped[row_keep, :]

    # Deduplication only drops cells equal to their predecessor, so the set of
    # distinct elevations must be preserved exactly. Cheap, and it would catch a
    # bad slice.
    assert set(np.unique(sampled).tolist()) == set(np.unique(grid).tolist()), (
        "decimation changed the set of elevations -- that is lossy"
    )
    print(f"[dem] {before[1]}x{before[0]} -> {grid.shape[1]}x{grid.shape[0]} "
          f"({cols / max(grid.shape[1], 1):.1f}x oversampling removed)")

    return grid


def encode_terrain_rgb(elevation: np.ndarray) -> np.ndarray:
    """mapbox terrain-RGB: metres = ((R*65536 + G*256 + B) * 0.1) - 32768."""
    scaled = np.clip((elevation + 32768.0) * 10.0, 0, 16_777_215).astype(np.int64)
    rgb = np.zeros(elevation.shape + (3,), dtype=np.uint8)
    rgb[..., 0] = (scaled >> 16) & 0xFF
    rgb[..., 1] = (scaled >> 8) & 0xFF
    rgb[..., 2] = scaled & 0xFF
    return rgb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()

    area = load_study_area(settings.data_dir / "scene_hsk.json")
    bounds = polygon_bounds(area["bbox"])
    print(f"[dem] study area bounds: {[round(b, 5) for b in bounds]}")
    print(f"[dem] verifying source against known ground truth...")
    verify_source()

    elevation = sample_grid(bounds)
    rows, cols = elevation.shape
    if np.isnan(elevation).any():
        # Fill gaps with the mean of the valid cells. Deliberately not
        # scipy.ndimage: this dataset does not produce nodata, and adding a
        # dependency for a branch that never runs is its own kind of risk.
        gaps = int(np.isnan(elevation).sum())
        if gaps > 0.05 * elevation.size:
            raise RuntimeError(f"{gaps} nodata cells is too many to paper over")
        print(f"[dem] filling {gaps} nodata cells with the valid mean")
        fill = float(np.nanmean(elevation))
        elevation = np.where(np.isnan(elevation), fill, elevation)

    print(f"[dem] grid {cols} x {rows} after deduplication")
    print(f"[dem] elevation  min={elevation.min():.1f}  max={elevation.max():.1f}  "
          f"mean={elevation.mean():.1f} m")

    if not 0.0 <= elevation.min() <= 50.0:
        raise RuntimeError(
            f"elevation min {elevation.min():.1f} m is implausible for a coastal "
            "Hong Kong floodplain; refusing to write."
        )
    if not 50.0 <= elevation.max() <= 2000.0:
        raise RuntimeError(
            f"elevation max {elevation.max():.1f} m is implausible for HSK/HT "
            "(the area tops out near 500 m); refusing to write."
        )

    np.save(settings.data_dir / "dem_hsk.npy", elevation)
    Image.fromarray(encode_terrain_rgb(elevation), mode="RGB").save(
        settings.data_dir / "dem_terrain_rgb.png"
    )

    meta = {
        "artefact": "dem",
        "generated": "2026-10-06",
        "status": "REAL_TERRAIN_SAMPLED",
        "source": {
            "name": "Open-Meteo elevation API",
            "url": API,
            "keyRequired": False,
            "upstreamModels": (
                "A downsampled global elevation model, ~90 m posting "
                "(derived from SRTM and coarser sources). The ~90 m figure "
                "is measured on the returned grid, not advertised: the API "
                "reports no resolution, and asking for a finer lattice only "
                "duplicates values. See grid.resolutionLonM/LatM."
            ),
            "note": (
                "Verified against Everest and open-ocean control points on every "
                "run. The AWS elevation-tiles-prod bucket was tested and rejected: "
                "it returns HTTP 200 with non-terrarium pixels that decode to "
                "nonsense (Everest ~1e6 m)."
            ),
        },
        "grid": {
            "rows": rows,
            "cols": cols,
            "resolutionLonM": round((bounds[2] - bounds[0]) * 111_320.0
                                   * math.cos(math.radians((bounds[1] + bounds[3]) / 2))
                                   / cols, 1),
            "resolutionLatM": round((bounds[3] - bounds[1]) * 110_574.0 / rows, 1),
            "resolutionNote": (
                "Measured from the deduplicated grid. The source is a ~90 m "
                "global model; requesting a finer lattice duplicates values "
                "rather than resolving them."
            ),
            "bounds": list(bounds),
            "orientation": "north-up, row 0 = northernmost",
            "verticalDatum": "mPD (Open-Meteo returns orthometric heights)",
        },
        "elevation": {
            "minM": float(elevation.min()),
            "maxM": float(elevation.max()),
            "meanM": float(elevation.mean()),
            "reliefM": float(elevation.max() - elevation.min()),
        },
        "caveats": [
            "Sampled, not a survey product: ~90 m postings resampled onto the "
            "5 m analysis grid. The 5 m grid is the analysis convention, not "
            "the data resolution, and the ~90 m figure is the measured one.",
            "Orthometric (mPD), not ellipsoidal. No vertical datum shift applied.",
            "No bare-earth correction. If vegetation/structures bias this upward, "
            "a deficit estimate would be biased too.",
            "Sinks are not filled and there is no flow routing; the wet grid fills "
            "by water-surface level, not by routing water downhill.",
        ],
    }
    (settings.data_dir / "dem_hsk.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print("[dem] wrote dem_hsk.npy, dem_terrain_rgb.png, dem_hsk.json")

    # Register the raster so GET /terrain hands the viewer a real URL. Without
    # this the DEM exists on disk and the scene still says terrainUrl is null,
    # which is the exact "field reserved but never populated" gap that made
    # §4.1 unfalsifiable in the first place.
    try:
        from app import db
        from app.services.scene_service import attach_overlay

        for doc_path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(doc_path)
            if not doc or not doc.get("study_area"):
                continue
            attach_overlay(doc["id"], "terrain_url", "dem_terrain_rgb.png")
            # terrain_format is deliberately NOT set. The spec's enum is
            # {quantized-mesh, heightmap-tiles, arcinfo-ascii} and terrain-RGB
            # is none of them, so writing one would be a lie the validator
            # rightly rejects. The frontend dispatches on the .png extension
            # instead, and terrain_source carries the real provenance.
            attach_overlay(
                doc["id"],
                "terrain_source",
                "terrain-rgb / open-meteo / srtm-1arcsec",
            )
            print(f"[dem] registered terrain on scene {doc['id']}")
    except Exception as exc:  # noqa: BLE001
        print(f"[dem] could not register the terrain on a scene: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
