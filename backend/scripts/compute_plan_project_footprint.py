"""Project the mtr_station.glb footprint into EPSG:4326 so the frontend can
hide 2.5D buildings beneath it when the Plan Project toggle is on.

The transform chain must match SandboxViewer.tsx lines 733-754 exactly:

    modelMatrix = Transforms.eastNorthUpToFixedFrame(
        Cartesian3.fromDegrees(MTR_LON=113.9930, MTR_LAT=22.4360, originH)
    )
    scale       = MTR_SCALE = 50

Cesium's glTF loader converts the GLB's Y-up frame to Z-up before applying the
model matrix, i.e. ENU-east = glb-x, ENU-north = -glb-z, ENU-up = glb-y. The
same remap is used by scripts/tiles_to_buildings.py:55 for the city-model GLB.

The XY projection in ENU metres is converted to lon/lat via a flat-earth
approximation (|E|,|N| < ~500 m, error < 1 cm at 22.44 N).

Output: backend/data/plan_project_footprint.json (GeoJSON Polygon, outer ring,
closed).

Run from backend/:
    python scripts/compute_plan_project_footprint.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[1]
GLB = ROOT / "model" / "mtr_station.glb"
OUT = ROOT / "data" / "plan_project_footprint.json"

# Keep these three in lockstep with SandboxViewer.tsx:733-735.
ORIGIN_LON = 113.9930
ORIGIN_LAT = 22.4360
SCALE = 50.0


def load_vertices_enu_metres() -> np.ndarray:
    """Return GLB vertices in ENU metres relative to (ORIGIN_LON, ORIGIN_LAT).

    glTF is Y-up; Cesium remaps to ENU as (east, north, up) = (x, -z, y).
    Then scale by SCALE to get metres.
    """
    scene = trimesh.load(GLB, force="scene")
    chunks = []
    for node in scene.graph.nodes_geometry:
        mat, name = scene.graph[node]
        geom = scene.geometry[name]
        v = trimesh.transformations.transform_points(geom.vertices, mat)
        chunks.append(np.column_stack([v[:, 0], -v[:, 2], v[:, 1]]))
    if not chunks:
        raise RuntimeError(f"no geometry in {GLB}")
    v = np.vstack(chunks).astype(np.float64)
    v *= SCALE
    return v


def enu_to_lonlat(east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / 111_132.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(ORIGIN_LAT)))
    return ORIGIN_LON + dlon, ORIGIN_LAT + dlat


def main() -> None:
    v = load_vertices_enu_metres()
    print(
        f"[plan-project] {len(v):,} vertices  "
        f"E {v[:,0].min():.1f}..{v[:,0].max():.1f} m  "
        f"N {v[:,1].min():.1f}..{v[:,1].max():.1f} m  "
        f"U {v[:,2].min():.1f}..{v[:,2].max():.1f} m"
    )
    xy = v[:, :2]
    hull = ConvexHull(xy)
    ring_m = xy[hull.vertices]
    ring_lonlat = [list(enu_to_lonlat(float(e), float(n))) for e, n in ring_m]
    ring_lonlat.append(ring_lonlat[0])

    doc = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring_lonlat]},
        "properties": {
            "source": "mtr_station.glb convex hull",
            "origin": [ORIGIN_LON, ORIGIN_LAT],
            "scale": SCALE,
            "vertex_count": int(len(v)),
            "hull_vertices": int(len(hull.vertices)),
            "extent_m": {
                "east": [float(v[:, 0].min()), float(v[:, 0].max())],
                "north": [float(v[:, 1].min()), float(v[:, 1].max())],
                "up": [float(v[:, 2].min()), float(v[:, 2].max())],
            },
        },
    }
    OUT.write_text(json.dumps(doc, indent=2))
    print(f"[plan-project] wrote {OUT} ({len(hull.vertices)}-vertex hull)")


if __name__ == "__main__":
    main()
