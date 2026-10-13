"""Build the real building layer: Overture footprints + Open3Dhk tile heights.

Inputs (all produced by earlier scripts):
  data/dsm_hsk_tiles.npz               1 m surface from the city tiles   (tiles_to_buildings.py)
  data/dem_hsk.npy / terrain tiles     ground                            (tiles_to_rasters.py)
  data/reference/overture_buildings_raw.json   footprints

Rules (EPSG:2326 metres):
  * height = 90th percentile of (surface - ground) inside the footprint
  * an Overture footprint is kept only when the tiles agree something stands there (p90 >= 3 m)
  * a tile-only blob is added when it is >= 40 m2 and p90 >= 7 m (below that it is mostly trees/sheds)
Output: data/rasters/buildings_hsk.json (+ buildings_meta.json) in the existing schema, and
data/reference/tree_mask.npz (tall non-building blobs, used as green cover).
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features as rf
from pyproj import Transformer
from scipy import ndimage as ndi
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from tiles_to_rasters import tile_vertices, TERRAIN_DIR  # noqa: E402

DATA = ROOT / "data"
MIN_BLOB_M2, MIN_BLOB_H = 40, 7.0
KEEP_OVERTURE_H = 3.0


def ground_and_surface():
    z = np.load(DATA / "dsm_hsk_tiles.npz")
    dsm, e0, n0 = z["dsm"], float(z["e0"]), float(z["n0"])
    v = np.vstack([tile_vertices(p) for p in sorted(TERRAIN_DIR.glob("*.b3dm"))])
    rows, cols = dsm.shape
    ee, nn = np.meshgrid(e0 + np.arange(cols) + 0.5, n0 + np.arange(rows) + 0.5)
    dtm = LinearNDInterpolator(v[:, :2], v[:, 2])(ee, nn)
    dtm = np.where(np.isnan(dtm), NearestNDInterpolator(v[:, :2], v[:, 2])(ee, nn), dtm)
    return dsm - dtm, e0, n0


def main() -> None:
    nd, e0, n0 = ground_and_surface()
    rows, cols = nd.shape
    to_m = Transformer.from_crs(4326, 2326, always_xy=True).transform
    to_ll = Transformer.from_crs(2326, 4326, always_xy=True).transform
    # raster row 0 = south here; rasterio wants north-up, so flip when rasterising
    tf = rasterio.transform.from_origin(e0, n0 + rows, 1, 1)
    nd_n = nd[::-1]  # north-up copy

    ov = json.load(open(DATA / "reference" / "overture_buildings_raw.json"))
    feats, occupied = [], np.zeros((rows, cols), bool)
    for f in ov["features"]:
        g = shp_transform(to_m, shape(f["geometry"])).buffer(0)
        if g.is_empty or g.area < 8:
            continue
        m = rf.rasterize([(g, 1)], out_shape=(rows, cols), transform=tf, fill=0).astype(bool)
        vals = nd_n[m & ~np.isnan(nd_n)]
        if vals.size < 6:
            continue
        h = float(np.percentile(vals, 90))
        if h < KEEP_OVERTURE_H:
            continue
        occupied |= m
        feats.append((g, h, "overture+tiles"))

    mask = ndi.binary_opening(~np.isnan(nd_n) & (nd_n > 3.0))
    mask &= ~ndi.binary_dilation(occupied, iterations=2)
    lab, n = ndi.label(mask)
    tree = np.zeros((rows, cols), bool)
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        blob = lab[sl] == i
        area = int(blob.sum())
        h = float(np.percentile(nd_n[sl][blob], 90))
        if area >= MIN_BLOB_M2 and h >= MIN_BLOB_H:
            m = np.zeros((rows, cols), bool)
            m[sl] = blob
            for geom, _ in rf.shapes(m.astype("uint8"), mask=m, transform=tf):
                g = shape(geom).simplify(0.8)
                if g.area >= MIN_BLOB_M2 * 0.7:
                    feats.append((g, h, "tiles_only"))
        elif area >= 12:
            tree[sl] |= blob
    np.savez_compressed(DATA / "reference" / "tree_mask.npz", tree=tree, e0=e0, n0=n0)

    out = []
    for k, (g, h, src) in enumerate(feats):
        gl = shp_transform(to_ll, g)
        out.append({"type": "Feature", "geometry": mapping(gl), "properties": {
            "id": f"bldg-{k:04d}", "building_type": "surveyed_massing", "height_m": round(h, 1),
            "storeys": max(1, round(h / 3.2)), "height_source": "OPEN3DHK_TILE_SURFACE_P90",
            "footprint_source": src, "provisional": False}})
    fc = {"type": "FeatureCollection", "features": out}
    (DATA / "rasters" / "buildings_hsk.json").write_text(json.dumps(fc))
    hs = np.array([f["properties"]["height_m"] for f in out])
    meta = {"status": "DERIVED_FROM_OPEN3DHK_AND_OVERTURE", "features": len(out),
            "heightM": {"min": float(hs.min()), "max": float(hs.max()), "mean": float(hs.mean())},
            "overtureKept": sum(1 for f in feats if f[2] == "overture+tiles"),
            "tileOnlyAdded": sum(1 for f in feats if f[2] == "tiles_only"),
            "heightSource": "90th percentile of tile surface minus tile terrain",
            "footprintSource": f"Overture Maps {ov.get('release')} (ODbL/CDLA) fused with tile-derived blobs",
            "rules": {"keepOvertureIfP90AtLeast": KEEP_OVERTURE_H, "tileOnlyMinM2": MIN_BLOB_M2,
                      "tileOnlyMinHeight": MIN_BLOB_H},
            "generated": date.today().isoformat(), "editable": True}
    (DATA / "rasters" / "buildings_meta.json").write_text(json.dumps(meta, indent=2))
    print(meta)


if __name__ == "__main__":
    main()
