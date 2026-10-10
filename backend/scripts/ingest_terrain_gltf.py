#!/usr/bin/env python
"""Replace the synthetic DEM with real terrain from the HK Lands Dept glTF.

    python scripts/ingest_terrain_gltf.py

## Source
`backend/6-NW-12D/TERRAIN(TB)/T17250324000106E10/` — 148,644 vertices covering
~750 m × 600 m of HSK/HT at ~0.8 m resolution. Coordinate system: HK1980 Grid
(EPSG:2326), converted to WGS84 (EPSG:4326) via pyproj.

## Matrix convention
The glTF `nodes[0].matrix` is column-major 4×4 (OpenGL / Cesium convention):
  [1,0,0,0, 0,0,-1,0, 0,1,0,0, tx, ty, tz, 1]
Column vectors (3×3 rotation block):
  col0 = [1, 0, 0] → local X maps to world X  → HK1980 easting
  col1 = [0, 0,-1] → local Y maps to world -Z → northing:  N = ly + northing_o
  col2 = [0, 1, 0] → local Z maps to world Y  → elevation: h = lz + elev_o
Translation (column 3, indices 12–14):
  matrix[12] = tx → easting_origin
  matrix[13] = ty → elevation_origin  (world Y intercept, not northing)
  matrix[14] = tz → −northing_origin  (world Z intercept, sign flipped by col1)

Local POSITION (lx, ly, lz) → HK1980 absolute:
  E = tx + lx
  N = −tz + ly     (world Z = −ly + tz; northing = −(world Z) = ly − tz)
  h = ty + lz      (world Y = lz + ty)

## Outside-coverage fill
The terrain tile covers only part of the study area. Cells outside the
convex hull of the terrain points are filled with 0 m (flat ground).
The extension interface: replace `_fill_outside` below to use a better
source when one becomes available.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.geo_import import build_grid, load_study_area, polygon_bounds  # noqa: E402

TERRAIN_DIR = (
    BACKEND_DIR
    / "6-NW-12D"
    / "TERRAIN(TB)"
    / "T17250324000106E10"
)
GLTF_FILE = TERRAIN_DIR / "T17250324000106E10.gltf"


# --------------------------------------------------------------------------- #
# Coordinate transform helpers
# --------------------------------------------------------------------------- #


def _parse_matrix(matrix16: list[float]) -> tuple[float, float, float]:
    """Return (easting, northing, elevation_origin) from a column-major 4×4 glTF matrix."""
    # Columns 0-3 are stored sequentially (4 elements each).
    # Column 3 = [tx, ty, tz, 1] → indices 12, 13, 14, 15
    tx = matrix16[12]  # easting
    ty = matrix16[13]  # elevation origin
    tz = matrix16[14]  # -northing (sign flipped by the rotation)
    return tx, -tz, ty  # (easting, northing, elevation_origin)


def _local_to_hk1980(
    lx: np.ndarray,
    ly: np.ndarray,
    lz: np.ndarray,
    easting_origin: float,
    northing_origin: float,
    elev_origin: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply the glTF rotation+translation to local XYZ → HK1980 (E, N, h).

    Row-major form of the column-major glTF matrix [1,0,0,0, 0,0,-1,0, 0,1,0,0, tx,ty,tz,1]:
      world_x = lx + tx   → easting
      world_y = lz + ty   → elevation (glTF Y-up maps local Z to world Y)
      world_z = -ly + tz  → northing via: N = -world_z = ly - tz = ly + northing_origin
    """
    easting = easting_origin + lx
    northing = northing_origin + ly   # local Y → northing offset
    elevation = elev_origin + lz      # local Z → elevation
    return easting, northing, elevation


def _hk1980_to_wgs84(
    easting: np.ndarray, northing: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Transform HK1980 Grid (EPSG:2326) to WGS84 (EPSG:4326)."""
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    lons, lats = tf.transform(easting, northing)
    return np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64)


# --------------------------------------------------------------------------- #
# glTF / binary buffer parsing
# --------------------------------------------------------------------------- #


def _read_positions(gltf_path: Path) -> np.ndarray:
    """Return (N, 3) float32 array of local POSITION vertices from the glTF."""
    with gltf_path.open("r", encoding="utf-8") as fh:
        gltf = json.load(fh)

    # POSITION is accessor 0 per the spec file we read.
    acc = gltf["accessors"][0]
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = gltf["buffers"][0]
    bin_path = gltf_path.parent / buf["uri"]

    count = acc["count"]
    byte_offset = bv["byteOffset"] + acc.get("byteOffset", 0)
    byte_length = count * 3 * 4  # float32 × 3 components

    data = bin_path.read_bytes()
    flat = struct.unpack_from(f"<{count * 3}f", data, byte_offset)
    return np.array(flat, dtype=np.float32).reshape(count, 3)


def _read_node_matrix(gltf_path: Path) -> list[float]:
    with gltf_path.open("r", encoding="utf-8") as fh:
        gltf = json.load(fh)
    return gltf["nodes"][0]["matrix"]


# --------------------------------------------------------------------------- #
# Extension interface: outside-coverage fill
# --------------------------------------------------------------------------- #


def _fill_outside(
    grid_lons: np.ndarray,  # (rows, cols)
    grid_lats: np.ndarray,
    known_mask: np.ndarray,  # bool, True where real data exists
    known_elevations: np.ndarray,  # values already written at known cells
) -> np.ndarray:
    """Fill cells outside terrain coverage with a feathered blend to 0 m.

    Covered cells keep their elevation. Outside cells start at the elevation of
    the nearest covered cell and blend linearly to 0 m over FEATHER_CELLS grid
    cells (~50 m at 5 m resolution). This removes the hard cliff that appears in
    Cesium when 97 % of the grid is flat 0 m and a small real-terrain island sits
    in the middle.

    Replace this function to use a better fill source (e.g. another DEM tile)
    without touching the rest of the script.
    """
    from scipy.ndimage import distance_transform_edt

    FEATHER_CELLS = 10  # transition width in grid cells (~50 m at 5 m resolution)

    # For every cell, find the distance to the nearest covered cell *and* its
    # row/col index. return_indices=True gives the closest-covered-cell pointer.
    dist, (row_nn, col_nn) = distance_transform_edt(~known_mask, return_indices=True)

    # Elevation of the nearest covered cell at every grid position.
    nearest_elev = known_elevations[row_nn, col_nn]

    # Blend weight: 0 at the coverage edge, 1 at FEATHER_CELLS away (and beyond).
    weight = np.clip(dist / FEATHER_CELLS, 0.0, 1.0).astype(np.float32)

    # Covered cells: keep real elevation (weight == 0 there, but we use where).
    # Outside cells: blend from nearest_elev → 0 as distance grows.
    fill = np.where(known_mask, known_elevations, nearest_elev * (1.0 - weight))
    return fill.astype(np.float32)


# --------------------------------------------------------------------------- #
# Interpolation: scatter points → regular grid
# --------------------------------------------------------------------------- #


def _scatter_to_grid(
    lons_pts: np.ndarray,
    lats_pts: np.ndarray,
    elev_pts: np.ndarray,
    bounds: tuple[float, float, float, float],
    rows: int,
    cols: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour rasterisation without scipy.

    Returns (elevation_grid, coverage_mask).
    elevation_grid  : float32 (rows, cols), filled with 0 where no point is close
    coverage_mask   : bool    (rows, cols), True where at least one point fell
    """
    lon_min, lat_min, lon_max, lat_max = bounds

    # Grid cell centres (same origin convention as build_grid: north-up)
    col_idx = ((lons_pts - lon_min) / (lon_max - lon_min) * cols).astype(np.int32)
    row_idx = ((lat_max - lats_pts) / (lat_max - lat_min) * rows).astype(np.int32)

    # Clip to valid range
    valid = (
        (col_idx >= 0) & (col_idx < cols) &
        (row_idx >= 0) & (row_idx < rows)
    )
    col_idx = col_idx[valid]
    row_idx = row_idx[valid]
    elev_valid = elev_pts[valid]

    elev_grid = np.zeros((rows, cols), dtype=np.float32)
    count_grid = np.zeros((rows, cols), dtype=np.int32)

    # Accumulate and average
    np.add.at(elev_grid, (row_idx, col_idx), elev_valid)
    np.add.at(count_grid, (row_idx, col_idx), 1)

    coverage_mask = count_grid > 0
    elev_grid[coverage_mask] /= count_grid[coverage_mask]

    return elev_grid, coverage_mask


# --------------------------------------------------------------------------- #
# terrain-RGB encoding (same as fetch_dem.py)
# --------------------------------------------------------------------------- #


def encode_terrain_rgb(elevation: np.ndarray) -> np.ndarray:
    """Mapbox terrain-RGB: metres = ((R*65536 + G*256 + B) * 0.1) - 32768."""
    scaled = np.clip((elevation + 32768.0) * 10.0, 0, 16_777_215).astype(np.int64)
    rgb = np.zeros(elevation.shape + (3,), dtype=np.uint8)
    rgb[..., 0] = (scaled >> 16) & 0xFF
    rgb[..., 1] = (scaled >> 8) & 0xFF
    rgb[..., 2] = scaled & 0xFF
    return rgb


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    area = load_study_area(settings.data_dir / "scene_hsk.json")
    bounds = polygon_bounds(area["bbox"])
    lon_min, lat_min, lon_max, lat_max = bounds
    grid = build_grid(bounds, resolution_m=5.0)
    rows, cols = int(grid["rows"]), int(grid["cols"])
    print(f"[terrain] study area bounds: {[round(b, 6) for b in bounds]}")
    print(f"[terrain] target grid: {cols} x {rows} cells at 5 m")

    if not GLTF_FILE.exists():
        print(f"[terrain] ERROR: {GLTF_FILE} not found")
        return 1

    positions = _read_positions(GLTF_FILE)
    matrix16 = _read_node_matrix(GLTF_FILE)
    lx, ly, lz = positions[:, 0], positions[:, 1], positions[:, 2]
    print(f"[terrain] loaded {len(positions)} vertices from glTF")

    easting_origin, northing_origin, elev_origin = _parse_matrix(matrix16)
    print(f"[terrain] origin HK1980: E={easting_origin:.1f} N={northing_origin:.1f} h={elev_origin:.2f}")

    easting, northing, elevation = _local_to_hk1980(
        lx, ly, lz, easting_origin, northing_origin, elev_origin
    )
    print(f"[terrain] elevation range: {elevation.min():.2f} - {elevation.max():.2f} m")

    lons, lats = _hk1980_to_wgs84(easting, northing)
    print(f"[terrain] WGS84 lon: {lons.min():.5f} - {lons.max():.5f}")
    print(f"[terrain] WGS84 lat: {lats.min():.5f} - {lats.max():.5f}")

    if not (113.9 < float(lons.mean()) < 114.1 and 22.4 < float(lats.mean()) < 22.5):
        print(f"[terrain] WARNING: mean position ({lons.mean():.4f}, {lats.mean():.4f}) "
              "looks outside HSK/HT -- check the matrix")

    elev_grid, coverage_mask = _scatter_to_grid(lons, lats, elevation, bounds, rows, cols)
    covered = int(coverage_mask.sum())
    total = rows * cols
    print(f"[terrain] coverage: {covered}/{total} cells ({100*covered/total:.1f}%)")

    elev_final = _fill_outside(
        np.zeros((rows, cols), dtype=np.float32),
        np.zeros((rows, cols), dtype=np.float32),
        coverage_mask,
        elev_grid,
    )

    np.save(str(settings.data_dir / "dem_hsk.npy"), elev_final)
    print(f"[terrain] wrote dem_hsk.npy  min={elev_final.min():.1f}  max={elev_final.max():.1f} m")

    rgb = encode_terrain_rgb(elev_final)
    img = Image.fromarray(rgb, mode="RGB")
    png_path = settings.data_dir / "dem_terrain_rgb.png"
    img.save(str(png_path))
    print(f"[terrain] wrote dem_terrain_rgb.png ({cols}x{rows})")

    lon_data_min = float(lons.min())
    lon_data_max = float(lons.max())
    lat_data_min = float(lats.min())
    lat_data_max = float(lats.max())
    meta = {
        "source": "HK Lands Dept 6-NW-12D TERRAIN(TB) glTF",
        "sourceCrs": "EPSG:2326 HK1980 Grid -> EPSG:4326 WGS84 via pyproj",
        "tileId": "T17250324000106E10",
        "vertexCount": len(positions),
        "coverageBounds": {
            "lonMin": round(lon_data_min, 6),
            "lonMax": round(lon_data_max, 6),
            "latMin": round(lat_data_min, 6),
            "latMax": round(lat_data_max, 6),
        },
        "coverageFraction": round(covered / total, 4),
        "outsideCoverageFill": "0m_flat_ground",
        "elevationM": {
            "min": round(float(elev_final[coverage_mask].min()) if covered else 0.0, 2),
            "max": round(float(elev_final.max()), 2),
            "meanCovered": round(
                float(elev_final[coverage_mask].mean()) if covered else 0.0, 2
            ),
        },
        "gridRows": rows,
        "gridCols": cols,
        "resolutionM": 5.0,
        "format": "mapbox-terrain-rgb",
        "note": (
            "Real terrain from HK Lands Dept 6-NW-12D. Outside the glTF tile "
            "extent, cells are filled with 0 m (flat ground). To replace the "
            "fill, reimplement _fill_outside in ingest_terrain_gltf.py."
        ),
    }
    (settings.data_dir / "dem_hsk.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print("[terrain] wrote dem_hsk.json")

    try:
        import shutil

        from app import db
        from app.services.scene_service import attach_overlay

        # Copy the glTF texture alongside the other rasters so FastAPI can serve it.
        texture_src = TERRAIN_DIR / "T17250324000106E10_001.jpg"
        texture_dst = settings.rasters_dir / "terrain_texture_hsk.jpg"
        if texture_src.exists():
            shutil.copy2(texture_src, texture_dst)
            print(f"[terrain] copied terrain texture -> {texture_dst.name}")

        registered = 0
        for doc_path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(doc_path)
            if not doc or not doc.get("study_area"):
                continue
            attach_overlay(doc["id"], "terrain_url", "dem_terrain_rgb.png")
            attach_overlay(
                doc["id"],
                "terrain_source",
                "terrain-rgb / lands-dept-6nw12d / hk1980-epsg2326",
            )
            # Register the terrain texture as the base imagery layer.
            # imagery_bbox is the tight WGS84 coverage of the glTF tile, not the
            # full scene bbox — the frontend needs it to georeference the image.
            attach_overlay(doc["id"], "imagery_url", "rasters/terrain_texture_hsk.jpg")
            doc_fresh = db.read_doc(doc_path)
            if doc_fresh is not None:
                doc_fresh["imagery_bbox"] = [
                    round(lon_data_min, 6),
                    round(lat_data_min, 6),
                    round(lon_data_max, 6),
                    round(lat_data_max, 6),
                ]
                db.write_doc(doc_path, doc_fresh)
            registered += 1
        if registered:
            print(f"[terrain] registered terrain_url + imagery on {registered} scene(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"[terrain] could not register on scene: {exc}")

    print("[terrain] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
