"""Project a planned project's GLB footprint into EPSG:4326 so the frontend can
hide the 2.5D buildings beneath it.

The transform chain must match SandboxViewer.tsx's model placement exactly:

    modelMatrix = Transforms.eastNorthUpToFixedFrame(
        Cartesian3.fromDegrees(origin_lon, origin_lat, originH)
    )
    scale       = the project's `scale`

Cesium's glTF loader converts the GLB's Y-up frame to Z-up before applying the
model matrix, i.e. ENU-east = glb-x, ENU-north = -glb-z, ENU-up = glb-y. The
same remap is used by scripts/tiles_to_buildings.py:55 for the city-model GLB.

The XY projection in ENU metres is converted to lon/lat via a flat-earth
approximation (|E|,|N| < ~500 m, error < 1 cm at 22.44 N).

Everything project-specific is read from `data/projects/<id>.json` -- the same
file that declares the project's effects -- so a second project needs no edit
here. `origin` and `scale` MUST be present in that file: they are the placement
the viewer uses, and a footprint computed from a different origin would hide the
wrong buildings.

Output: whatever the project file names in `footprint`.

Run from backend/:
    python scripts/compute_plan_project_footprint.py                  # every project
    python scripts/compute_plan_project_footprint.py --project mtr-station
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

try:
    import trimesh
except ModuleNotFoundError:  # pragma: no cover - environment, not logic
    # Deliberately a clear message rather than a traceback. This script is not in
    # the setup path (`preflight.py` does not need it): its output is committed,
    # so it only runs when a project's GLB changes. `trimesh` is in
    # requirements.txt, so this means the install was skipped, and saying that
    # beats "No module named 'trimesh'" four frames down.
    raise SystemExit(
        "trimesh is not installed, and this script needs it to read a GLB.\n"
        "  pip install -r requirements.txt\n"
        "The footprint it produces is committed, so you only need this when a\n"
        "project's model changes; the app itself does not require it."
    ) from None

from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "data" / "projects"


def project_files(only: str | None) -> list[Path]:
    files = sorted(p for p in PROJECTS_DIR.glob("*.json") if p.is_file())
    if only:
        files = [p for p in files if (json.loads(p.read_text(encoding="utf-8")).get("id") or p.stem) == only]
        if not files:
            raise SystemExit(f"no project named {only!r} in {PROJECTS_DIR}")
    return files


def load_vertices_enu_metres(glb: Path, scale: float) -> np.ndarray:
    """Return GLB vertices in ENU metres relative to the project's origin.

    glTF is Y-up; Cesium remaps to ENU as (east, north, up) = (x, -z, y).
    Then scale by `scale` to get metres.
    """
    scene = trimesh.load(glb, force="scene")
    chunks = []
    for node in scene.graph.nodes_geometry:
        mat, name = scene.graph[node]
        geom = scene.geometry[name]
        v = trimesh.transformations.transform_points(geom.vertices, mat)
        chunks.append(np.column_stack([v[:, 0], -v[:, 2], v[:, 1]]))
    if not chunks:
        raise RuntimeError(f"no geometry in {glb}")
    v = np.vstack(chunks).astype(np.float64)
    v *= scale
    return v


def enu_to_lonlat(east_m: float, north_m: float,
                  origin_lon: float, origin_lat: float) -> tuple[float, float]:
    dlat = north_m / 111_132.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(origin_lat)))
    return origin_lon + dlon, origin_lat + dlat


def build_one(doc: dict) -> Path:
    project_id = doc.get("id") or "?"
    origin = doc.get("origin")
    scale = doc.get("scale")
    model_rel = doc.get("model")
    footprint_rel = doc.get("footprint")
    for key, value in (("model", model_rel), ("footprint", footprint_rel),
                       ("origin", origin), ("scale", scale)):
        if not value:
            raise SystemExit(
                f"{project_id}: project file needs {key!r} to compute a footprint. "
                "`origin` and `scale` must match the placement the viewer uses."
            )
    origin_lon, origin_lat = float(origin[0]), float(origin[1])
    glb = ROOT / "model" / model_rel
    if not glb.exists():
        raise SystemExit(f"{project_id}: model not found: {glb}")

    v = load_vertices_enu_metres(glb, float(scale))
    print(
        f"[{project_id}] {len(v):,} vertices  "
        f"E {v[:,0].min():.1f}..{v[:,0].max():.1f} m  "
        f"N {v[:,1].min():.1f}..{v[:,1].max():.1f} m  "
        f"U {v[:,2].min():.1f}..{v[:,2].max():.1f} m"
    )
    xy = v[:, :2]
    hull = ConvexHull(xy)
    ring_m = xy[hull.vertices]
    ring_lonlat = [list(enu_to_lonlat(float(e), float(n), origin_lon, origin_lat))
                   for e, n in ring_m]
    ring_lonlat.append(ring_lonlat[0])

    out = ROOT / "data" / footprint_rel
    doc_out = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring_lonlat]},
        "properties": {
            "source": f"{model_rel} convex hull",
            "origin": [origin_lon, origin_lat],
            "scale": float(scale),
            "vertex_count": int(len(v)),
            "hull_vertices": int(len(hull.vertices)),
            "extent_m": {
                "east": [float(v[:, 0].min()), float(v[:, 0].max())],
                "north": [float(v[:, 1].min()), float(v[:, 1].max())],
                # The project's own height: this is where `station_massing`'s
                # `heightM` comes from, so it is recorded rather than retyped.
                "up": [float(v[:, 2].min()), float(v[:, 2].max())],
            },
        },
    }
    out.write_text(json.dumps(doc_out, indent=2), encoding="utf-8")
    print(f"[{project_id}] wrote {out} ({len(hull.vertices)}-vertex hull)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None,
                        help="one project id (default: every file in data/projects/)")
    args = parser.parse_args()
    for path in project_files(args.project):
        build_one(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
