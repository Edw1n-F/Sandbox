#!/usr/bin/env python
"""Ingest WATERBODY and INFRASTRUCTURE glTF layers from 6-NW-12D.

    python scripts/ingest_layers_gltf.py

Produces:
  backend/data/rasters/waterbody_hsk.json   -- GeoJSON FeatureCollection
  backend/data/rasters/infrastructure_hsk.json

Same AABB + matrix convention as ingest_buildings_gltf.py (EPSG:2326 -> WGS84).
Each feature carries:
  properties.id, properties.layer, properties.height_m (0 for flat features)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

BASE_DIR = BACKEND_DIR / "6-NW-12D"

LAYERS: dict[str, dict] = {
    "WATERBODY": {
        "dir": BASE_DIR / "WATERBODY",
        "out": "waterbody_hsk.json",
        "scene_key": "waterbody_path",
        "scene_rel": "rasters/waterbody_hsk.json",
    },
    "INFRASTRUCTURE": {
        "dir": BASE_DIR / "INFRASTRUCTURE",
        "out": "infrastructure_hsk.json",
        "scene_key": "infrastructure_path",
        "scene_rel": "rasters/infrastructure_hsk.json",
    },
}


def _parse_matrix(matrix16: list[float]) -> tuple[float, float, float]:
    tx = matrix16[12]
    ty = matrix16[13]
    tz = matrix16[14]
    return tx, -tz, ty


def _hk1980_to_wgs84_batch(
    eastings: np.ndarray, northings: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    lons, lats = tf.transform(eastings, northings)
    return np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64)


def _parse_feature(gltf_path: Path, layer: str) -> dict | None:
    """Return a GeoJSON Feature for one glTF object, or None on error."""
    try:
        data = json.loads(gltf_path.read_text(encoding="utf-8"))
        nodes = data.get("nodes", [])
        accessors = data.get("accessors", [])
        if not nodes or not accessors:
            return None

        node = nodes[0]
        matrix = node.get("matrix")
        if not matrix or len(matrix) != 16:
            return None
        easting_o, northing_o, elev_o = _parse_matrix(matrix)

        acc = accessors[0]
        mn = acc.get("min", [0, 0, 0])
        mx = acc.get("max", [0, 0, 0])
        if len(mn) < 2 or len(mx) < 2:
            return None

        # local AABB corners in HK1980
        local_xs = np.array([mn[0], mn[0], mx[0], mx[0]])
        local_zs = np.array([mn[2], mx[2], mx[2], mn[2]])
        eastings = easting_o + local_xs
        northings = northing_o + local_zs
        lons, lats = _hk1980_to_wgs84_batch(eastings, northings)

        ring = [
            [float(lons[i]), float(lats[i])] for i in range(4)
        ]
        ring.append(ring[0])

        # Reject degenerate polygons (AABB collapsed to a line or a point).
        # Cesium's TerrainOffsetProperty.updateClamping computes a geodeticSurfaceNormal
        # on the centroid; a zero-area shape yields Cartesian(0,0,0) which triggers
        # "cartesian has a NaN component" and crashes the scene.
        lons_u = [p[0] for p in ring]
        lats_u = [p[1] for p in ring]
        if max(lons_u) - min(lons_u) < 1e-7 or max(lats_u) - min(lats_u) < 1e-7:
            return None

        # height: local Y-axis maximum (same axis convention as buildings)
        height_m = round(float(elev_o + mx[1]) if len(mx) > 1 else 0.0, 2)
        # For flat features (waterbody) height is meaningless; store 0
        local_height = round(float(mx[1]) if len(mx) > 1 else 0.0, 2)

        return {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "id": gltf_path.parent.name,
                "layer": layer,
                "height_m": local_height,
            },
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  [skip] {gltf_path.parent.name}: {exc}")
        return None


def _ingest_layer(layer_name: str, cfg: dict) -> int:
    layer_dir: Path = cfg["dir"]
    if not layer_dir.exists():
        print(f"[{layer_name}] directory not found: {layer_dir}")
        return 0

    subdirs = sorted(d for d in layer_dir.iterdir() if d.is_dir())
    features = []
    for subdir in subdirs:
        # find the primary gltf (same name as directory)
        gltf = subdir / f"{subdir.name}.gltf"
        if not gltf.exists():
            # some dirs use a suffix pattern; pick the first gltf
            candidates = sorted(subdir.glob("*.gltf"))
            if not candidates:
                continue
            gltf = candidates[0]
        feat = _parse_feature(gltf, layer_name)
        if feat:
            features.append(feat)

    collection = {"type": "FeatureCollection", "features": features}
    out_path = settings.rasters_dir / cfg["out"]
    out_path.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    print(f"[{layer_name}] wrote {cfg['out']} ({len(features)} features)")

    # Register on scene
    try:
        from app import db
        from app.services.scene_service import attach_overlay

        registered = 0
        for doc_path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(doc_path)
            if not doc or not doc.get("study_area"):
                continue
            attach_overlay(doc["id"], cfg["scene_key"], cfg["scene_rel"])
            registered += 1
        if registered:
            print(f"[{layer_name}] registered on {registered} scene(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"[{layer_name}] could not register on scene: {exc}")

    return len(features)


def main() -> int:
    for layer_name, cfg in LAYERS.items():
        _ingest_layer(layer_name, cfg)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
