"""Per-building environmental context, and a deterministic ledger to score it.

## Why this module exists

The AI planner could say *what* to change but never *what it was changing*. A
clicked building carried eight properties -- ``height_m``, ``storeys``,
``height_class`` and five provenance flags -- and nothing about the ground it
stands on. So "make this block better for the environment" had nowhere to be
answered from.

Everything needed is already on disk, at two very different resolutions:

=============================== =========== ===============================
Grids                           Resolution  What it gives
=============================== =========== ===============================
``landclass_hsk.npy``           5 mland class, and therefore curve
                                            number, retention and heat
``wetgrid_hsk.npy``             5 m         the solved water depth field
``flow_accum_hsk.npy``          5 m         where water concentrates
``dem_hsk.npy``                 ~90 m       ground elevation, and only this
=============================== =========== ===============================

So this module joins them onto each building and answers three questions the
panel could not before: *what is this building standing on*, *how bad is it
here*, and *what would actually be better*.

## The division of labour, which is the whole point

The language model does **not** compute the answer. It states an **objective**
and a set of **constraints**; the candidate scorer re-runs the project's own
storage ledger over a modified land-class grid. Every number returned is a delta
the real model produced.

That is deliberate, and it is not a limitation dressed up. This project has
already caught ``qwen2.5:3b-instruct`` producing four confident wrong answers:
transposing ``dx``/``dy`` on a compass move, sending a placeholder ``site_id``
that overrode an explicitly named region, returning the operation without the
schema's array, and filling a massing move with a meaningless ``height_m: 0``. A
3.1B model asked to "work out the optimal height" returns a plausible number
with no arithmetic behind it. So the model states intent and the code does the
arithmetic.

## What this is not

Screening, at 5 m, on a level-pool allocation rather than routed hydraulics, with
an assumed HSG D curve-number table and an unverified hand-authored heat index.
The scorer finds the best option *within this model*. It does not find the best
development, and every record says so.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.config import settings  # noqa: E402

# Reused rather than reimplemented. These are the project's physics, and a second
# copy of the curve-number table or the ADR-015 storage partition would drift
# from the one the overlays were generated with -- which is exactly the double
# count, waiting to happen again in a new place.
from _overlay_common import (  # noqa: E402
    COUNTED_AS,
    CURVE_NUMBER_HSG_D,
    DEPRESSION_STORAGE_M,
    HEAT_INDEX,
    LAND_CLASSES,
    STORAGE_DEPTH_M,
    load_overlay,
    runoff_depth,
)

# A building's effect on the cells it does not occupy: shade, and the surface it
# presents to sun and rain. Both are linear in height, which is the crudest
# defensible assumption available and is labelled wherever it surfaces.
#
# What is deliberately *absent* is any claim that a taller building floods less.
# It does not. A height change moves the heat and shade terms and nothing else,
# and a model reporting a flood benefit from raising a building would be lying in
# the one place this project cannot afford to.
BUILDING_SHADE_M_PER_M = 0.35
BUILDING_EXPOSURE_M2_PER_M = 1.8

DEFAULT_RAINFALL_MM = 450.0
NEIGHBOURHOOD_M = 100.0
M_PER_DEG_LAT = 110_540.0


@dataclass(frozen=True)
class Grids:
    """Every raster the context needs, loaded once and shared."""

    land_class: np.ndarray
    depth_m: np.ndarray
    accumulation: np.ndarray
    elevation_coarse: np.ndarray
    bounds: tuple[float, float, float, float]
    rows: int
    cols: int
    cell_area_m2: float
    terrain_source: str
    landcover_source: str
    water_level_m: float
    runoff_m3: float
    unstored_m3: float
    wet_area_ha: float
    dem_shape: tuple[int, int]

    def cell_of(self, lon: float, lat: float) -> tuple[int, int]:
        x0, y0, x1, y1 = self.bounds
        col = int((lon - x0) / (x1 - x0) * self.cols)
        row = int((y1 - lat) / (y1 - y0) * self.rows)
        return (max(0, min(self.rows - 1, row)),
                max(0, min(self.cols - 1, col)))

    def cells_of_ring(self, ring: list[list[float]]) -> list[tuple[int, int]]:
        """Every cell the footprint covers -- not the cell at its centroid.

        The first prototype sampled the centroid cell and reported one building
        as ``green``: a 30 m envelope straddles several 5 m cells and the middle
        one is not always paved. Siting decisions need the whole footprint.
        """
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        r0, c0 = self.cell_of(min(xs), max(ys))
        r1, c1 = self.cell_of(max(xs), min(ys))
        return [(r, c)
                for r in range(min(r0, r1), max(r0, r1) + 1)
                for c in range(min(c0, c1), max(c0, c1) + 1)]

    def depth_at(self, row: int, col: int) -> float:
        return float(self.depth_m[row, col])

    def ground_elevation_m(self, lon: float, lat: float) -> float | None:
        """Nearest-neighbour from the coarse DEM. Deliberately not interpolated.

        The committed DEM is 37x28 over the study area -- about 90 m cells. A
        bilinear upsample would produce a smooth surface at 5 m that the source
        cannot support, and every number downstream would carry precision it does
        not have. Nearest-neighbour plus an explicit resolution label is the
        honest option.
        """
        r, c = self.cell_of(lon, lat)
        dr, dc = self.dem_shape
        rr = min(dr - 1, int(r / self.rows * dr))
        cc = min(dc - 1, int(c / self.cols * dc))
        return float(self.elevation_coarse[rr, cc])

    def radius_cells(self, metres: float) -> int:
        return max(1, int(round(metres / (self.cell_area_m2 ** 0.5))))


@lru_cache(maxsize=1)
def load_grids() -> Grids | None:
    """Load every raster once. None when the scripts have not been run.

    None rather than an exception: the API has to boot and serve scenes on a
    machine where the overlays have not been generated, and a 500 at startup is a
    worse failure than an absent feature.
    """
    rasters = settings.rasters_dir
    # landclass_hsk.npy lives in data/, not data/rasters/, and load_overlay()
    # already resolves it (or falls back to a synthetic surface and says so). The
    # depth field is the only hard requirement: nothing here works without it.
    if not (rasters / "wetgrid_hsk.npy").exists():
        return None
    if not (settings.data_dir / "dem_hsk.npy").exists():
        return None

    ov = load_overlay()

    meta: dict[str, Any] = {}
    sidecar = rasters / "wetgrid_hsk.json"
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            meta = {}
    results = meta.get("results", {}) or {}

    accum_path = rasters / "flow_accum_hsk.npy"
    accumulation = (np.load(accum_path) if accum_path.exists()
                    else np.zeros(ov.elevation.shape, dtype=np.float32))

    dem = np.load(settings.data_dir / "dem_hsk.npy")
    depth = np.load(rasters / "wetgrid_hsk.npy")

    return Grids(
        land_class=ov.land_class,
        depth_m=depth,
        accumulation=accumulation,
        elevation_coarse=dem,
        bounds=ov.bounds,
        rows=ov.rows,
        cols=ov.cols,
        cell_area_m2=ov.cell_area_m2(),
        terrain_source=ov.terrain_source,
        landcover_source=ov.landcover_source,
        # Derived, not read: the sidecar records volumes and areas but not the
        # water-surface elevation, and it is recoverable exactly -- the level pool
        # is one horizontal plane, so the highest ground-plus-depth under water is
        # that plane.
        water_level_m=_water_level(ov.elevation, depth),
        runoff_m3=float(results.get("runoffVolumeM3") or 0.0),
        unstored_m3=float(results.get("unstoredVolumeM3") or 0.0),
        wet_area_ha=float(results.get("wetAreaHa") or 0.0),
        dem_shape=(int(dem.shape[0]), int(dem.shape[1])),
    )


def _water_level(elevation: np.ndarray, depth: np.ndarray) -> float:
    """The level-pool water-surface elevation, recovered from the depth field.

    The solver fills to a single horizontal plane, so the maximum of
    ``elevation + depth`` over wet cells *is* that plane. Returned as 0.0 when
    nothing is wet, which is a real answer rather than a missing one.
    """
    wet = depth > 0
    if not wet.any():
        return 0.0
    return float((elevation + depth)[wet].max())


@dataclass
class BuildingContext:
    """Everything known about one building and the ground it stands on."""

    building_id: str
    lon: float
    lat: float
    height_m: float
    storeys: int | None
    height_class: str
    land_class: str
    curve_number: float
    heat_index: float
    flood_depth_m: float
    ground_elevation_m: float | None
    footprint_cells: int
    footprint_area_m2: float
    green_share_100m: float
    impervious_share_100m: float
    neighbours_100m: int
    neighbour_mean_height_m: float | None
    neighbour_max_height_m: float | None
    shade_m: float
    exposure_m2: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "buildingId": self.building_id,
            "lon": round(self.lon, 6),
            "lat": round(self.lat, 6),
            "heightM": self.height_m,
            "storeys": self.storeys,
            "heightClass": self.height_class,
            "landClass": self.land_class,
            "curveNumber": self.curve_number,
            "heatIndex": self.heat_index,
            "floodDepthM": round(self.flood_depth_m, 3),
            "groundElevationM": self.ground_elevation_m,
            "footprintCells": self.footprint_cells,
            "footprintAreaM2": self.footprint_area_m2,
            "greenShare100m": round(self.green_share_100m, 4),
            "imperviousShare100m": round(self.impervious_share_100m, 4),
            "neighbours100m": self.neighbours_100m,
            "neighbourMeanHeightM": (None if self.neighbour_mean_height_m is None
                                     else round(self.neighbour_mean_height_m, 2)),
            "neighbourMaxHeightM": (None if self.neighbour_max_height_m is None
                                    else round(self.neighbour_max_height_m, 2)),
            "shadeM": round(self.shade_m, 2),
            "exposureM2": round(self.exposure_m2, 2),
            "notes": self.notes,
        }


def _dominant_class(grids: Grids, cells: list[tuple[int, int]]) -> str:
    counts: dict[int, int] = {}
    for r, c in cells:
        k = int(grids.land_class[r, c])
        counts[k] = counts.get(k, 0) + 1
    # Ties break toward the lower class index, so the answer does not depend on
    # dict ordering.
    return LAND_CLASSES[max(counts, key=lambda k: (counts[k], -k))]


def context_for(
    grids: Grids,
    building_id: str,
    ring: list[list[float]],
    properties: dict[str, Any],
    all_buildings: list[tuple[float, float, float]] | None = None,
) -> BuildingContext:
    """Join every raster onto one building.

    ``all_buildings`` is ``(lon, lat, height_m)`` for the district; pass it for
    the neighbourhood statistics, omit it when they are not needed.
    """
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    n = max(1, len(xs) - 1)
    lon = sum(xs[:-1]) / n
    lat = sum(ys[:-1]) / n

    cells = grids.cells_of_ring(ring)
    land_class = _dominant_class(grids, cells)
    row, col = grids.cell_of(lon, lat)

    radius = grids.radius_cells(NEIGHBOURHOOD_M)
    r0, r1 = max(0, row - radius), min(grids.rows, row + radius + 1)
    c0, c1 = max(0, col - radius), min(grids.cols, col + radius + 1)
    window = grids.land_class[r0:r1, c0:c1]
    total = max(1, window.size)
    green_idx = LAND_CLASSES.index("green")
    paved_idx = LAND_CLASSES.index("paved")

    height = float(properties.get("height_m") or 0.0)

    n_neighbours = 0
    mean_h: float | None = None
    max_h: float | None = None
    if all_buildings is not None:
        dlon = NEIGHBOURHOOD_M / (111_320.0 * float(np.cos(np.radians(lat))))
        dlat = NEIGHBOURHOOD_M / M_PER_DEG_LAT
        near = [h for (blon, blat, h) in all_buildings
                if abs(blon - lon) <= dlon and abs(blat - lat) <= dlat]
        n_neighbours = len(near)
        if near:
            mean_h = sum(near) / len(near)
            max_h = max(near)

    notes = [
        "Screening only: 5 m land class, a level-pool water allocation, an "
        "assumed HSG D curve-number table, and an unverified hand-authored heat "
        "index. Best-within-this-model, not an engineering optimum.",
        "Flood depth is read from the solved depth field. It is not routed "
        "hydraulics and does not model pipes, culverts, backflow or barriers.",
        f"Ground elevation is nearest-neighbour from a {grids.dem_shape[0]}"
        f"x{grids.dem_shape[1]} DEM (about 90 m cells), so it is a neighbourhood "
        f"value, not a spot height.",
    ]
    if grids.terrain_source != "open-meteo":
        notes.append(f"Terrain source is '{grids.terrain_source}', not real elevation.")

    return BuildingContext(
        building_id=building_id,
        lon=lon,
        lat=lat,
        height_m=height,
        storeys=properties.get("storeys"),
        height_class=str(properties.get("height_class") or "unknown"),
        land_class=land_class,
        curve_number=float(CURVE_NUMBER_HSG_D.get(land_class, 0.0)),
        heat_index=float(HEAT_INDEX.get(land_class, 0.0)),
        flood_depth_m=grids.depth_at(row, col),
        ground_elevation_m=grids.ground_elevation_m(lon, lat),
        footprint_cells=len(cells),
        footprint_area_m2=len(cells) * grids.cell_area_m2,
        green_share_100m=float((window == green_idx).sum()) / total,
        impervious_share_100m=float((window == paved_idx).sum()) / total,
        neighbours_100m=n_neighbours,
        neighbour_mean_height_m=mean_h,
        neighbour_max_height_m=max_h,
        shade_m=height * BUILDING_SHADE_M_PER_M,
        exposure_m2=height * BUILDING_EXPOSURE_M2_PER_M,
        notes=notes,
    )


def storage_ledger(
    grids: Grids,
    land_class: np.ndarray,
    rainfall_mm: float = DEFAULT_RAINFALL_MM,
) -> dict[str, float]:
    """Runoff, retention and the unstored volume for one land-class grid.

    The same arithmetic the wet-grid script performs, driven by the same TR-55
    table and the same ``COUNTED_AS`` partition, so a delta computed here is a
    delta the project would report if the overlay were regenerated.

    The level-pool fill is deliberately not repeated: it is the expensive part,
    it consumes the volume rather than producing it, and comparing candidates
    needs volumes. So this returns volumes and the caller compares those. Depths
    come from the already-solved field.
    """
    cn = np.zeros(land_class.shape, dtype=np.float32)
    for name, value in CURVE_NUMBER_HSG_D.items():
        cn[land_class == LAND_CLASSES.index(name)] = min(100.0, value)

    runoff_m = runoff_depth(rainfall_mm, cn)

    storage_m = np.full(land_class.shape, DEPRESSION_STORAGE_M, dtype=np.float32)
    for name, depth in STORAGE_DEPTH_M.items():
        storage_m[land_class == LAND_CLASSES.index(name)] = depth

    # A retention cell generates nothing: its contribution is already inside its
    # storage term, and counting it twice is the ADR-015 double count. Driven off
    # COUNTED_AS rather than a literal list so it cannot drift from the
    # declaration.
    for name, side in COUNTED_AS.items():
        if side == "retention":
            runoff_m[land_class == LAND_CLASSES.index(name)] = 0.0

    unstored_m = np.maximum(runoff_m - storage_m, 0.0)
    cell_area = grids.cell_area_m2

    heat = np.zeros(land_class.shape, dtype=np.float32)
    for name, value in HEAT_INDEX.items():
        heat[land_class == LAND_CLASSES.index(name)] = value

    green_idx = LAND_CLASSES.index("green")
    paved_idx = LAND_CLASSES.index("paved")
    return {
        "runoffM3": float(runoff_m.sum() * cell_area),
        "unstoredM3": float(unstored_m.sum() * cell_area),
        "heatMean": float(heat.mean()),
        "greenAreaM2": float((land_class == green_idx).sum() * cell_area),
        "pavedAreaM2": float((land_class == paved_idx).sum() * cell_area),
    }