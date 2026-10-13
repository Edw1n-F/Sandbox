"""Rebuild the 5 m land-class grid from real sources.

Layers, lowest to highest priority:
  1. ESA WorldCover 10 m classes (existing landclass_hsk.npy as the base)
  2. Sentinel-2 L2A NDVI / NDWI (Planetary Computer, median of the clearest scenes):
       NDVI >= 0.35 -> green;  NDWI >= 0.2 and NDVI < 0.1 -> water
  3. Tall non-building blobs from the tiles (tree_mask.npz) -> green
  4. Real building footprints (buildings_hsk.json) -> paved
Writes data/landclass_hsk.npy (+ landcover_hsk.json provenance). Grid is 117 x 148, row 0 = north.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features as rf
from pyproj import Transformer
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from _overlay_common import LAND_CLASSES  # noqa: E402

DATA = ROOT / "data"
BBOX = (113.9922, 22.4310, 113.9994, 22.4363)
ROWS, COLS = 117, 148
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
NDVI_GREEN, NDWI_WATER = 0.35, 0.2


def _open(req, tries=6):
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=90)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def band(href, e, n):
    """Read one S2 band at the grid cell centres (nearest 10 m pixel)."""
    url = json.load(_open(SIGN + urllib.parse.quote(href, safe="")))["href"]
    with rasterio.open(url) as ds:
        x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(e, n)
        w = from_bounds(x.min() - 30, y.min() - 30, x.max() + 30, y.max() + 30, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=w, boundless=True, fill_value=0)
        wt = ds.window_transform(w)
        c = ((x - wt.c) / wt.a).astype(int).clip(0, arr.shape[1] - 1)
        r = ((y - wt.f) / wt.e).astype(int).clip(0, arr.shape[0] - 1)
        return arr[r, c].astype("float32")


def sentinel_indices(lon, lat):
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(BBOX), "datetime": "2024-10-01/2026-09-30",
            "query": {"eo:cloud_cover": {"lt": 10}}, "limit": 30}
    req = urllib.request.Request(STAC, json.dumps(body).encode(), {"Content-Type": "application/json"})
    feats = json.load(_open(req))["features"]
    feats.sort(key=lambda f: f["properties"]["eo:cloud_cover"])
    ndvis, ndwis, used = [], [], []
    for f in feats[:5]:
        a = f["assets"]
        try:
            red, green, nir = (band(a[k]["href"], lon, lat) for k in ("B04", "B03", "B08"))
            scl = band(a["SCL"]["href"], lon, lat) if "SCL" in a else np.full_like(red, 4)
        except Exception as exc:  # noqa: BLE001
            print("[s2] skip", f["id"], repr(exc)[:80])
            continue
        ok = np.isin(scl, [4, 5, 6, 7, 11]) & (red > 0)  # vegetation, soil, water, unclassified, snow-free
        nd = (nir - red) / np.maximum(nir + red, 1)
        nw = (green - nir) / np.maximum(green + nir, 1)
        ndvis.append(np.where(ok, nd, np.nan))
        ndwis.append(np.where(ok, nw, np.nan))
        used.append(f["id"])
    print("[s2] scenes used:", used)
    return np.nanmedian(ndvis, axis=0), np.nanmedian(ndwis, axis=0), used


def main():
    west, south, east, north = BBOX
    lon = west + (np.arange(COLS) + 0.5) / COLS * (east - west)
    lat = north - (np.arange(ROWS) + 0.5) / ROWS * (north - south)
    lon_g, lat_g = np.meshgrid(lon, lat)

    base = np.load(DATA / "landclass_hsk.npy").astype(np.int16)
    before = {n: int((base == i).sum()) for i, n in enumerate(LAND_CLASSES)}
    ndvi, ndwi, used = sentinel_indices(lon_g, lat_g)
    cls = base.copy()
    G, W, P = (LAND_CLASSES.index(k) for k in ("green", "water", "paved"))
    cls[np.nan_to_num(ndvi, nan=-1) >= NDVI_GREEN] = G
    cls[(np.nan_to_num(ndwi, nan=-1) >= NDWI_WATER) & (np.nan_to_num(ndvi, nan=1) < 0.1)] = W

    # tree blobs (1 m, east/north from the tiles) -> grid cells
    tm = np.load(DATA / "reference" / "tree_mask.npz")
    tree, e0, n0 = tm["tree"], float(tm["e0"]), float(tm["n0"])
    e, n = Transformer.from_crs(4326, 2326, always_xy=True).transform(lon_g, lat_g)
    ci = (e - e0).astype(int); ri = tree.shape[0] - 1 - (n - n0).astype(int)  # tree is north-up
    inside = (ci >= 0) & (ci < tree.shape[1]) & (ri >= 0) & (ri < tree.shape[0])
    hit = np.zeros(cls.shape, bool)
    hit[inside] = tree[ri[inside], ci[inside]]
    cls[hit] = G

    # real buildings -> paved (rasterised in the grid's own lon/lat frame)
    fc = json.load(open(DATA / "rasters" / "buildings_hsk.json"))
    t = rasterio.transform.from_bounds(west, south, east, north, COLS, ROWS)
    b = rf.rasterize([(f["geometry"], 1) for f in fc["features"]], out_shape=(ROWS, COLS), transform=t,
                     fill=0, all_touched=True).astype(bool)
    cls[b] = P

    np.save(DATA / "landclass_hsk.npy", cls)
    after = {n_: int((cls == i).sum()) for i, n_ in enumerate(LAND_CLASSES)}
    print("before", before); print("after ", after)
    meta = json.loads((DATA / "landcover_hsk.json").read_text())
    meta.update({"generated": date.today().isoformat(), "status": "REAL_LANDCOVER_FUSED",
                 "fusion": {"base": "ESA WorldCover 10 m v200 (2021)",
                            "sentinel2": {"scenes": used, "ndviGreen": NDVI_GREEN, "ndwiWater": NDWI_WATER},
                            "trees": "tall non-building blobs from Open3Dhk tiles",
                            "buildings": "buildings_hsk.json footprints -> paved"},
                 "classCounts": {"before": before, "after": after}})
    (DATA / "landcover_hsk.json").write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
