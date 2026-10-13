"""Rasterise the self-hosted Open3Dhk terrain tiles into the analysis-grid DTM.

Reads every .b3dm under backend/model/terrain(TB), decodes the Draco glTF with
trimesh, and interpolates the mesh elevation onto the same 117 x 148 (5 m) grid
the rest of the pipeline uses. Writes data/dem_hsk.npy + data/dem_hsk.json.

Tile frame: EPSG:2326 (HK1980) metres, glTF Y-up, so
    easting = x, elevation = y, northing = -z.
"""
from __future__ import annotations

import io
import json
import struct
import sys
from datetime import date
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TERRAIN_DIR = ROOT / "model" / "terrain(TB)"
DATA = ROOT / "data"


def b3dm_glb(raw: bytes) -> bytes:
    _, _, _, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", raw[:28])
    return raw[28 + ftj + ftb + btj + btb:]


def tile_vertices(path: Path) -> np.ndarray:
    scene = trimesh.load(io.BytesIO(b3dm_glb(path.read_bytes())), file_type="glb")
    out = []
    for node in scene.graph.nodes_geometry:
        mat, name = scene.graph[node]
        v = trimesh.transformations.transform_points(scene.geometry[name].vertices, mat)
        out.append(np.column_stack([v[:, 0], -v[:, 2], v[:, 1]]))  # E, N, elev
    return np.vstack(out)


def main() -> None:
    scene = json.loads((DATA / "scene_hsk.json").read_text())
    west, south, east, north = 113.9922, 22.4310, 113.9994, 22.4363
    rows, cols = 117, 148

    verts = np.vstack([tile_vertices(p) for p in sorted(TERRAIN_DIR.glob("*.b3dm"))])
    print(f"[dtm] {len(verts):,} vertices, elev {verts[:,2].min():.2f}..{verts[:,2].max():.2f} m")

    lon = west + (np.arange(cols) + 0.5) / cols * (east - west)
    lat = north - (np.arange(rows) + 0.5) / rows * (north - south)  # row 0 = north
    lon_g, lat_g = np.meshgrid(lon, lat)
    e, n = Transformer.from_crs(4326, 2326, always_xy=True).transform(lon_g, lat_g)

    pts = verts[:, :2]
    lin = LinearNDInterpolator(pts, verts[:, 2])(e, n)
    holes = int(np.isnan(lin).sum())
    if holes:
        near = NearestNDInterpolator(pts, verts[:, 2])(e, n)
        lin = np.where(np.isnan(lin), near, lin)
    dem = lin.astype(np.float32)
    print(f"[dtm] grid {rows}x{cols}, filled {holes} edge cells, "
          f"elev {dem.min():.2f}..{dem.max():.2f} m, mean {dem.mean():.2f}")

    np.save(DATA / "dem_hsk.npy", dem)
    meta = {
        "artefact": "dem",
        "generated": date.today().isoformat(),
        "status": "REAL_TERRAIN_FROM_OPEN3DHK_TILES",
        "source": {
            "name": "Open3Dhk / LandsD 3D Digital Map terrain tiles (self-hosted)",
            "path": "backend/model/terrain(TB)",
            "crs": "EPSG:2326 (HK1980)",
            "method": "Draco glTF vertices linearly interpolated onto the analysis grid",
        },
        "grid": {
            "rows": rows, "cols": cols, "resolutionM": 5,
            "bounds": [west, south, east, north],
            "orientation": "north-up, row 0 = northernmost",
            "verticalDatum": "mPD (as supplied by the tile; not independently checked)",
        },
        "elevation": {
            "minM": float(dem.min()), "maxM": float(dem.max()),
            "meanM": float(dem.mean()), "reliefM": float(dem.max() - dem.min()),
        },
        "caveats": [
            "Surface of the terrain tile mesh, not a surveyed DTM release.",
            "Mesh spacing sets the true resolution; the 5 m grid is the analysis convention.",
            "The tiles carry texture over a draped mesh; bare-earth under structures is as modelled by LandsD.",
        ],
    }
    (DATA / "dem_hsk.json").write_text(json.dumps(meta, indent=2))
    print("[dtm] wrote dem_hsk.npy, dem_hsk.json")


if __name__ == "__main__":
    main()
