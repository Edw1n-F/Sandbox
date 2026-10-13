"""Derive building footprints + heights from the Open3Dhk city-model tiles.

Pipeline (all in EPSG:2326 metres, the tiles' own frame):
  1. decode every .b3dm (and the .b3dm tiles wrapped in .cmpt composites)
  2. rasterise a 1 m DSM (max surface height per cell)
  3. nDSM = DSM - DTM (the DTM comes from tiles_to_rasters.py)
  4. threshold + label -> candidate buildings, height = p90 of nDSM inside
  5. write data/reference/tiles_buildings_raw.json (WGS84 GeoJSON)

This is step A of the building rebuild. Fusion with Overture footprints and the
land-class rebuild happen in later scripts.
"""
from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
CITY_DIR = ROOT / "model" / "city_model"
DATA = ROOT / "data"
CELL = 1.0  # DSM cell, metres

# Study bbox in EPSG:2326 is derived from the tiles themselves.
B3DM_MAGIC = b"b3dm"


def iter_b3dm(raw: bytes):
    """Yield every b3dm payload in a b3dm or cmpt blob."""
    magic = raw[:4]
    if magic == b"b3dm":
        yield raw
    elif magic == b"cmpt":
        _, _, _, n = struct.unpack("<4sIII", raw[:16])
        off = 16
        for _ in range(n):
            (length,) = struct.unpack("<I", raw[off + 8:off + 12])
            yield from iter_b3dm(raw[off:off + length])
            off += length


def b3dm_points(raw: bytes) -> np.ndarray:
    _, _, _, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", raw[:28])
    glb = raw[28 + ftj + ftb + btj + btb:]
    scene = trimesh.load(io.BytesIO(glb), file_type="glb")
    out = []
    for node in scene.graph.nodes_geometry:
        mat, name = scene.graph[node]
        g = scene.geometry[name]
        v = trimesh.transformations.transform_points(g.vertices, mat)
        tri = v[g.faces]  # (F, 3, 3)
        cen = tri.mean(axis=1)
        mids = (tri + np.roll(tri, 1, axis=1)) / 2.0
        pts = np.vstack([v, cen, mids.reshape(-1, 3)])
        out.append(np.column_stack([pts[:, 0], -pts[:, 2], pts[:, 1]]))  # E, N, elev
    return np.vstack(out) if out else np.empty((0, 3))


def main() -> None:
    files = sorted(CITY_DIR.glob("*.b3dm")) + sorted(CITY_DIR.glob("*.cmpt"))
    chunks = []
    for i, p in enumerate(files):
        for blob in iter_b3dm(p.read_bytes()):
            pts = b3dm_points(blob)
            if len(pts):
                chunks.append(pts.astype(np.float32))
        if i % 200 == 0:
            print(f"[dsm] {i}/{len(files)} files", flush=True)
    pts = np.vstack(chunks)
    print(f"[dsm] {len(pts):,} sample points, E {pts[:,0].min():.0f}..{pts[:,0].max():.0f}, "
          f"N {pts[:,1].min():.0f}..{pts[:,1].max():.0f}, z {pts[:,2].min():.1f}..{pts[:,2].max():.1f}")

    e0, n0 = np.floor(pts[:, 0].min()), np.floor(pts[:, 1].min())
    cols = int(np.ceil(pts[:, 0].max() - e0 / 1.0)) if False else int(np.ceil((pts[:, 0].max() - e0) / CELL)) + 1
    rows = int(np.ceil((pts[:, 1].max() - n0) / CELL)) + 1
    ix = ((pts[:, 0] - e0) / CELL).astype(int)
    iy = ((pts[:, 1] - n0) / CELL).astype(int)
    dsm = np.full((rows, cols), -np.inf, dtype=np.float32)
    np.maximum.at(dsm, (iy, ix), pts[:, 2])
    dsm[np.isinf(dsm)] = np.nan
    np.savez_compressed(DATA / "dsm_hsk_tiles.npz", dsm=dsm, e0=e0, n0=n0, cell=CELL)
    print(f"[dsm] grid {rows}x{cols} @ {CELL} m (row 0 = south), "
          f"{np.isnan(dsm).mean()*100:.1f}% empty -> dsm_hsk_tiles.npz")


if __name__ == "__main__":
    main()
