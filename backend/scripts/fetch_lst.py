"""Fetch Landsat 8/9 Collection 2 Level-2 surface temperature over the study bbox.

Planetary Computer STAC (public, anonymous SAS signing). Keeps clear summer
daytime scenes only and writes data/reference/lst_scenes.npz: one 2-D array per
scene on the Landsat 30 m grid cropped to the bbox, in degrees C, NaN where the
QA band flags cloud, shadow, cirrus or snow.
"""
from __future__ import annotations
import json, urllib.parse, urllib.request
from pathlib import Path
import numpy as np, rasterio
from rasterio.windows import from_bounds
from pyproj import Transformer

DATA = Path(__file__).resolve().parents[1] / "data"
BBOX = (113.9922, 22.4310, 113.9994, 22.4363)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
SCENES = ["LC09_L2SP_122044_20260906_02_T2", "LC08_L2SP_122044_20260712_02_T1",
          "LC08_L2SP_122044_20250826_02_T1", "LC08_L2SP_122044_20240807_02_T1",
          "LC09_L2SP_121045_20240707_02_T1", "LC08_L2SP_122044_20220903_02_T1"]


def _open(req, tries: int = 6):
    import time, urllib.error
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def signed(href: str) -> str:
    return json.load(_open(SIGN + urllib.parse.quote(href, safe="")))["href"]


def read_band(href: str):
    with rasterio.open(signed(href)) as ds:
        x0, y0 = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(BBOX[0], BBOX[1])
        x1, y1 = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(BBOX[2], BBOX[3])
        w = from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), ds.transform)
        w = w.round_offsets().round_lengths()
        arr = ds.read(1, window=w, boundless=True, fill_value=0)
        return arr, ds.window_transform(w), ds.crs


def main():
    out, meta = {}, []
    for sid in SCENES:
        body = {"collections": ["landsat-c2-l2"], "ids": [sid], "bbox": list(BBOX)}
        req = urllib.request.Request(STAC, json.dumps(body).encode(), {"Content-Type": "application/json"})
        feats = json.load(_open(req))["features"]
        if not feats:
            print("[lst] missing", sid); continue
        a = feats[0]["assets"]
        st, tr, crs = read_band(a["lwir11"]["href"])
        qa, _, _ = read_band(a["qa_pixel"]["href"])
        # QA_PIXEL bits: 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 shadow, 5 snow (7 is water: keep)
        bad = (qa & 0b00111111) > 0
        k = st.astype("float64") * 0.00341802 + 149.0 - 273.15
        k[(st == 0) | bad] = np.nan
        out[sid] = k.astype("float32")
        meta.append({"id": sid, "datetime": feats[0]["properties"]["datetime"],
                     "cloud": feats[0]["properties"]["eo:cloud_cover"],
                     "meanC": float(np.nanmean(k)) if np.isfinite(k).any() else None,
                     "shape": list(k.shape), "transform": list(tr)[:6], "crs": str(crs)})
        print("[lst]", meta[-1])
    np.savez_compressed(DATA / "reference" / "lst_scenes.npz", **out)
    (DATA / "reference" / "lst_scenes.json").write_text(json.dumps(
        {"crs": str(crs), "transform": list(tr)[:6], "bbox": BBOX, "scenes": meta}, indent=2))


if __name__ == "__main__":
    main()
