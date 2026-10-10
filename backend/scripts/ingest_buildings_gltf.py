#!/usr/bin/env python
"""Replace procedural building massing with real envelopes from HK Lands Dept glTF.

    python scripts/ingest_buildings_gltf.py

## Source
`backend/6-NW-12D/BUILDING/` -- 390 subdirectories, each containing a glTF
for one building. Coordinates are in HK1980 Grid (EPSG:2326), converted to
WGS84 (EPSG:4326) via pyproj.

## Footprint strategy
Only the AABB (from `accessors[0].min/.max`) is read -- no binary buffer
parsing.  The AABB gives a tight oriented box in local coordinates, which is
transformed to a WGS84 rectangle.  The result is a rectangular polygon centred
on the building, aligned with the coordinate axes after projection.

## Matrix convention (same as ingest_terrain_gltf.py)
Column-major 4x4:  [1,0,0,0, 0,0,-1,0, 0,1,0,0, tx, ty, tz, 1]
  glTF local X  -> HK1980 easting  offset (col0 = [1,0,0,0])
  glTF local Y  -> HK1980 northing offset (col2 = [0,1,0,0])
  glTF local Z  -> elevation        offset (col1 reversed: world_y = lz+ty)
Height = accessor.max[2] (local Z max in metres — elevation axis).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

BUILDING_DIR = BACKEND_DIR / "6-NW-12D" / "BUILDING"
STOREY_M = 3.1


# --------------------------------------------------------------------------- #
# Coordinate helpers
# --------------------------------------------------------------------------- #


def _parse_matrix(matrix16: list[float]) -> tuple[float, float, float]:
    """Return (easting, northing, elev_origin) from column-major 4x4 glTF matrix."""
    tx = matrix16[12]
    ty = matrix16[13]
    tz = matrix16[14]
    return tx, -tz, ty


def _hk1980_to_wgs84_point(easting: float, northing: float) -> tuple[float, float]:
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    lon, lat = tf.transform(easting, northing)
    return float(lon), float(lat)


def _hk1980_to_wgs84_batch(
    eastings: np.ndarray, northings: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    lons, lats = tf.transform(eastings, northings)
    return np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64)


# --------------------------------------------------------------------------- #
# Parse one building glTF (AABB only -- no .bin read needed)
# --------------------------------------------------------------------------- #


def _parse_building(gltf_path: Path) -> dict | None:
    """Return a GeoJSON Feature for one building, or None on error."""
    try:
        with gltf_path.open("r", encoding="utf-8") as fh:
            gltf = json.load(fh)
    except Exception:  # noqa: BLE001
        return None

    # Nodes: node 0 has the matrix, node 1 references the mesh.
    nodes = gltf.get("nodes", [])
    if not nodes or "matrix" not in nodes[0]:
        return None
    matrix16: list[float] = nodes[0]["matrix"]
    easting_origin, northing_origin, elev_origin = _parse_matrix(matrix16)

    # POSITION accessor is always index 0 (confirmed across all sample files).
    accessors = gltf.get("accessors", [])
    if not accessors:
        return None
    acc = accessors[0]
    aabb_min: list[float] = acc.get("min", [0.0, 0.0, 0.0])
    aabb_max: list[float] = acc.get("max", [0.0, 0.0, 0.0])

    # Local AABB corners:
    #   accessor[0].min/max = [lx_min, ly_min, lz_min] / [lx_max, ly_max, lz_max]
    #   lx → easting offset, ly → northing offset, lz → elevation offset
    # Footprint = (lx, ly) horizontal plane; height = lz_max
    lx_min, ly_min = aabb_min[0], aabb_min[1]
    lx_max, ly_max = aabb_max[0], aabb_max[1]
    height_m = float(aabb_max[2])  # local Z max = building height above ground

    if height_m <= 0:
        return None

    # Four corners in HK1980 (using the same rotation as terrain: X->E, Z->N).
    e_corners = np.array([
        easting_origin + lx_min,
        easting_origin + lx_max,
        easting_origin + lx_max,
        easting_origin + lx_min,
    ])
    n_corners = np.array([
        northing_origin + ly_min,
        northing_origin + ly_min,
        northing_origin + ly_max,
        northing_origin + ly_max,
    ])

    lons, lats = _hk1980_to_wgs84_batch(e_corners, n_corners)

    # Closed polygon ring (SW, SE, NE, NW, SW).
    ring = [
        [round(float(lons[0]), 7), round(float(lats[0]), 7)],
        [round(float(lons[1]), 7), round(float(lats[1]), 7)],
        [round(float(lons[2]), 7), round(float(lats[2]), 7)],
        [round(float(lons[3]), 7), round(float(lats[3]), 7)],
        [round(float(lons[0]), 7), round(float(lats[0]), 7)],
    ]

    # Height class: match make_citymodel.py conventions.
    storeys = max(1, round(height_m / STOREY_M))
    if storeys <= 4:
        height_class = "village_house"
    elif storeys <= 12:
        height_class = "new_town_block"
    elif storeys <= 26:
        height_class = "newer_tower"
    else:
        height_class = "landmark_tower"

    bldg_id = gltf_path.stem

    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "id": bldg_id,
            "building_type": "massing_envelope",
            "height_m": round(height_m, 2),
            "storeys": storeys,
            "height_class": height_class,
            "height_source": "measured_gltf_aabb",
            "footprint_source": "LANDS_DEPT_6NW12D_GLTF",
            "provisional": False,
        },
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    if not BUILDING_DIR.exists():
        print(f"[buildings] ERROR: {BUILDING_DIR} not found")
        return 1

    subdirs = sorted(p for p in BUILDING_DIR.iterdir() if p.is_dir())
    print(f"[buildings] found {len(subdirs)} building directories")

    features: list[dict] = []
    skipped = 0
    for subdir in subdirs:
        gltf_files = list(subdir.glob("*.gltf"))
        if not gltf_files:
            skipped += 1
            continue
        feat = _parse_building(gltf_files[0])
        if feat is None:
            skipped += 1
        else:
            features.append(feat)

    print(f"[buildings] parsed {len(features)} buildings, skipped {skipped}")
    if not features:
        print("[buildings] ERROR: no features produced -- check the glTF structure")
        return 1

    heights = np.array([f["properties"]["height_m"] for f in features])
    by_class: dict[str, int] = {}
    for f in features:
        k = f["properties"]["height_class"]
        by_class[k] = by_class.get(k, 0) + 1
    print(f"[buildings] height  min={heights.min():.1f}  max={heights.max():.1f}  mean={heights.mean():.1f} m")
    print(f"[buildings] classes {by_class}")

    # Renumber ids sequentially for consistency with the viewer's entity keys.
    for i, feat in enumerate(features):
        feat["properties"]["id"] = f"bldg-{i:04d}"

    collection = {"type": "FeatureCollection", "features": features}
    out = settings.rasters_dir / "citymodel_hsk.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    print(f"[buildings] wrote {out}")

    meta = {
        "status": "REAL_GLTF_FOOTPRINTS_LANDS_DEPT_6NW12D",
        "features": len(features),
        "source": "HK Lands Dept 6-NW-12D BUILDING glTF",
        "sourceCrs": "EPSG:2326 HK1980 Grid -> EPSG:4326 WGS84 via pyproj",
        "footprintMethod": "AABB from glTF accessors[0].min/.max",
        "heightMethod": "accessors[0].max[1] (local Y-axis maximum)",
        "heightM": {
            "min": round(float(heights.min()), 1),
            "max": round(float(heights.max()), 1),
            "mean": round(float(heights.mean()), 1),
        },
        "byClass": by_class,
        "editable": True,
        "note": (
            "Real building envelopes from HK Lands Dept 6-NW-12D. Footprints are "
            "AABB rectangles (not surveyed polygons); heights are the mesh Y-axis "
            "maximum, not a measured floor count. The geometry is suitable for "
            "visual massing and AI-driven height edits."
        ),
    }
    meta_path = settings.rasters_dir / "citymodel_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[buildings] wrote citymodel_meta.json")

    # Register on scene
    try:
        from app import db
        from app.services.scene_service import attach_overlay

        registered = 0
        for doc_path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(doc_path)
            if not doc or not doc.get("study_area"):
                continue
            attach_overlay(doc["id"], "city_model_path", "rasters/citymodel_hsk.json")
            registered += 1
        if registered:
            print(f"[buildings] registered city_model_path on {registered} scene(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"[buildings] could not register on scene: {exc}")

    print("[buildings] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
