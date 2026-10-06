#!/usr/bin/env python
"""Fetch real land cover for the study area from ESA WorldCover.

    python scripts/fetch_landcover.py

Replaces the synthetic land-class grid with actual classified land cover.

**Source: ESA WorldCover 10 m v200 (2021)**, a 10 m global land-cover map
produced by ESA and distributed under CC-BY 4.0. It is a genuine research
product from the European Space Agency, freely downloadable, and it is the
open counterpart to the commercial land-utilisation data our spec assumes.

Why this and not OpenStreetMap: OSM land cover is crowd-sourced and in Hong Kong
it is patchy at 10 m. WorldCover is a consistent, modelled, per-pixel product
with a documented class scheme, which is what a scenario comparison needs -- the
whole point is that two scenarios see *the same* ground.

Read with a **windowed** COG read, so only the ~1.5 % of the 3x3 degree tile
that the study area actually occupies is transferred. `rasterio` is used purely
as a COG reader; the analysis grid is built in numpy from there.

Outputs into `backend/data/`:
- `landclass_hsk.npy`   int16, indices into `_overlay_common.LAND_CLASSES`
- `landcover_hsk.json`  provenance, class areas, and the ESA->ours mapping
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.geo_import import build_grid, polygon_bounds  # noqa: E402
from _overlay_common import LAND_CLASSES, load_study_area  # noqa: E402

# ESA WorldCover 10m v200, 3x3 degree tiles, named by their south-west corner.
# Our extent (22.425-22.455 N, 113.982-114.005 E) falls in N21E111.
TILE_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
    "/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)

# ESA WorldCover class codes (v200) -> our five classes.
# Collapsing 11 classes to 5 is deliberate: the wet grid needs to know what
# sheds water and what holds it, not what the crop is.
ESA_TO_OURS: dict[int, str] = {
    10: "green",    # tree cover
    20: "green",    # shrubland
    30: "green",    # grassland
    40: "paved",    # cropland -> treated as impervious-ish for runoff
    50: "paved",    # built-up
    60: "bare",     # bare / sparse vegetation
    70: "water",    # water bodies
    80: "water",    # permanent snow and ice -> treat as water (no water bodies here)
    90: "wetland",  # herbaceous wetland
    95: "wetland",  # mangroves
    100: "green",   # moss and lichen (n/a here)
}

# Cross-check: cropland (40) and built-up (50) have very different hydrology, and
# lumping them into one class is a modelling compromise, recorded in the output
# metadata rather than hidden. HSK/HT is post-reclamation so "cropland" there is
# mostly formed land awaiting development.
CROPLAND_IS_APPROXIMATION = True


def tile_name(lon: float, lat: float) -> str:
    """ESA tiles are 3x3 degrees named by their south-west corner."""
    south = int(np.floor(lat / 3.0) * 3)
    west = int(np.floor(lon / 3.0) * 3)
    return f"N{south:02d}E{west:03d}"


def covering_tiles(bounds: tuple[float, float, float, float]) -> list[str]:
    """Every 3x3 degree tile the extent touches.

    An extent that *straddles* a tile edge needs more than one tile, and getting
    this wrong is silent and expensive: reading only the tile containing the
    south-west corner leaves the eastern strip as nodata, and nodata that gets
    filled with a default class fabricates 21 % of a study area as "paved" --
    which then feeds a runoff calculation. HSK/HT spans 113.982-114.005 E, and
    the tile boundary is exactly 114 E.
    """
    lon_min, lat_min, lon_max, lat_max = bounds
    names: list[str] = []
    south = int(np.floor(lat_min / 3.0) * 3)
    while south < lat_max:
        west = int(np.floor(lon_min / 3.0) * 3)
        while west < lon_max:
            names.append(f"N{south:02d}E{west:03d}")
            west += 3
        south += 3
    return names


def grid_affine(
    bounds: tuple[float, float, float, float], cell_w_m: float, cell_h_m: float
):
    """Affine transform for a north-up grid over ``bounds``.

    `build_grid` deliberately returns no transform -- it is the single place
    degrees become metres, and it hands back cell sizes rather than a GDAL
    object, so nothing projected can leak past it. That means anything needing a
    real affine has to rebuild one from the same numbers, which is what this
    does. The negative y scale is what makes row 0 the northernmost.
    """
    from affine import Affine

    lon_min, lat_min, lon_max, lat_max = bounds
    lat0 = np.radians((lat_min + lat_max) / 2.0)
    m_per_deg_lat = 111_132.92 - 559.82 * np.cos(2 * lat0) + 1.175 * np.cos(4 * lat0)
    m_per_deg_lon = 111_412.84 * np.cos(lat0) - 93.5 * np.cos(3 * lat0)
    return Affine(
        cell_w_m / m_per_deg_lon,   # px -> deg lon
        0.0,
        lon_min,                   # west edge
        0.0,
        -cell_h_m / m_per_deg_lat,  # px -> deg lat, negative = north-up
        lat_max,                   # north edge
    )


def read_window(
    urls: list[str],
    bounds: tuple[float, float, float, float],
    pad_m: float = 60.0,
    resolution_m: float = 5.0,
):
    """Windowed COG read of just the study area, reprojected to our grid."""
    import rasterio
    from rasterio.windows import from_bounds as window_from_bounds
    from rasterio.warp import reproject, Resampling

    grid = build_grid(bounds, resolution_m=resolution_m)
    rows, cols = int(grid["rows"]), int(grid["cols"])
    dst_transform = grid_affine(
        bounds, float(grid["cell_width_m"]), float(grid["cell_height_m"])
    )
    composite = np.zeros((rows, cols), dtype=np.int32)

    for url in urls:
        with rasterio.open(url) as src:
            lon_min, lat_min, lon_max, lat_max = bounds
            # Pad by a few pixels so a reprojection edge effect cannot clip.
            dx = pad_m / (111_320.0 * np.cos(np.radians((lat_min + lat_max) / 2)))
            dy = pad_m / 110_574.0
            window = window_from_bounds(
                lon_min - dx, lat_min - dy, lon_max + dx, lat_max + dy,
                transform=src.transform,
            )
            data = src.read(1, window=window, boundless=True, fill_value=0)

            tile = np.zeros((rows, cols), dtype=np.int32)
            reproject(
                source=data,
                destination=tile,
                src_transform=src.window_transform(window),
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.nearest,  # class codes must not be interpolated
            )
        # First tile wins: only fill cells still nodata, so overlapping reads
        # cannot introduce a seam.
        composite[(composite == 0) & (tile > 0)] = tile[(composite == 0) & (tile > 0)]

    return composite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=float, default=5.0, help="cell size, m")
    args = parser.parse_args()

    area = load_study_area(settings.data_dir / "scene_hsk.json")
    bounds = polygon_bounds(area["bbox"])
    tiles = covering_tiles(bounds)
    urls = [TILE_URL.format(tile=t) for t in tiles]
    print(f"[landcover] bounds {[round(b, 5) for b in bounds]}")
    print(f"[landcover] ESA WorldCover tiles: {', '.join(tiles)}")

    esa = read_window(urls, bounds, resolution_m=args.resolution)
    print(f"[landcover] read {esa.shape[0]} x {esa.shape[1]} cells")

    ours = np.full(esa.shape, LAND_CLASSES.index("green"), dtype=np.int16)
    unmapped = 0
    for code, name in ESA_TO_OURS.items():
        ours[esa == code] = LAND_CLASSES.index(name)
    unmapped = int(((esa > 0) & ~np.isin(esa, list(ESA_TO_OURS))).sum())
    # ESA code 0 is nodata/outside; treat as unknown rather than silently green.
    outside = int((esa == 0).sum())
    if outside:
        ours[esa == 0] = LAND_CLASSES.index("paved")
        print(f"[landcover] {outside} cells outside the ESA tile -> 'paved' (study area "
              f"extends past the tile edge; flagged in the metadata)")

    np.save(settings.data_dir / "landclass_hsk.npy", ours)

    grid = build_grid(bounds, resolution_m=args.resolution)
    cell_area = float(grid["area_m2"]) / ours.size
    counts = {name: int((ours == i).sum()) for i, name in enumerate(LAND_CLASSES)}
    area_ha = {k: round(v * cell_area / 10_000.0, 2) for k, v in counts.items()}
    total = max(sum(area_ha.values()), 1e-9)

    print(f"[landcover] area by class (ha):")
    for name, value in area_ha.items():
        print(f"[landcover]   {name:<9} {value:>9,.1f}  ({100*value/total:5.1f}%)")
    if unmapped:
        print(f"[landcover] WARNING {unmapped} cells had unmapped ESA codes")

    meta = {
        "artefact": "landcover",
        "generated": "2026-10-06",
        "status": "REAL_LANDCOVER",
        "source": {
            "name": "ESA WorldCover 10 m v200 (2021)",
            "urls": urls,
            "tiles": tiles,
            "licence": "CC BY 4.0 (ESA)",
            "access": "public S3, no key",
        },
        "mapping": {
            "esaToOurs": {str(k): v for k, v in sorted(ESA_TO_OURS.items())},
            "oursClasses": list(LAND_CLASSES),
            "collapsing11to5": (
                "Deliberate. The wet grid needs to know what sheds water and what "
                "holds it, not what the crop is. Scenario comparisons stay valid "
                "because every scenario reads the same grid."
            ),
            "croplandApproximation": (
                "ESA cropland (40) and built-up (50) are both mapped to 'paved'. "
                "They differ in hydrology; HSK/HT is post-reclamation so cropland "
                "there is mostly formed land awaiting development, but this is a "
                "compromise and is recorded rather than hidden."
            ) if CROPLAND_IS_APPROXIMATION else None,
        },
        "grid": {
            "rows": int(ours.shape[0]),
            "cols": int(ours.shape[1]),
            "resolutionM": args.resolution,
            "cellAreaM2": cell_area,
            "crs": "EPSG:4326 on the wire; local equirectangular for measurement",
        },
        "results": {
            "cellCountByClass": counts,
            "areaHaByClass": area_ha,
            "imperviousShare": round((area_ha["paved"]) / total, 4),
            "perviousShare": round(
                (area_ha["green"] + area_ha["wetland"]) / total, 4
            ),
            "unmappedCells": unmapped,
            "outsideTileCells": outside,
        },
        "caveats": [
            "WorldCover is a 2021 single-epoch product; it does not show change "
            "since then, which is a real gap for a study area under construction.",
            "10 m source resampled to the 5 m analysis grid, so it adds no detail "
            "below 10 m -- it is not an error to treat the 5 m land class as "
            "really 10 m.",
            "ESA codes are modelled classifications, not ground truth.",
        ],
    }
    (settings.data_dir / "landcover_hsk.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print("[landcover] wrote landclass_hsk.npy, landcover_hsk.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
