#!/usr/bin/env python
"""Derive intervention sites from the analysis, and create a scenario plan from them.

    python scripts/seed_interventions.py --reset

Interventions are the third level of the spec's scene -> plan -> intervention
model, and until now the level existed only on paper: the schemas were defined,
``POST /api/scenes/{sceneId}/plans`` could accept them, and the store held zero.

## Why derive them rather than draw them

Placing three arbitrary rectangles would be decoration. Instead every site here
is *derived from the analysis that already exists*, so each one answers a
question the model raised:

- **Flood storage** sits in the deepest connected basins of the computed wet
  grid -- i.e. "put storage where the water already goes".
- **Retention basins** sit on the largest contiguous vegetated cells that drain
  into those basins -- i.e. "hold the runoff before it arrives".
- **Drainage alignment** is a steepest-descent polyline from the basin's
  centroid, so it follows real topography rather than a straight line.
- **Heat mitigation** targets the biggest contiguous green blocks, which is
  where canopy has the most headroom against the paved surface.

Each feature records the evidence it came from in ``params``, so a reader can
see which raster produced it. Nothing here is a measured proposal; it is a
screening-level siting, and the metadata says so.

All features are created ``status="draft"`` (ADR-030): the engine refuses to
compute on draft geometry, and nothing here has been validated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.config import settings  # noqa: E402
from app.schemas.plan import PlanCreate  # noqa: E402
from app.services import scene_service  # noqa: E402
from _overlay_common import LAND_CLASSES, load_overlay  # noqa: E402

# Minimum depth (m) for a cell to count as a candidate storage basin.
BASIN_MIN_DEPTH_M = 0.35
# A basin must be at least this many cells to be worth a polygon.
BASIN_MIN_CELLS = 40
# Green patch size (cells) for a heat-mitigation target.
GREEN_MIN_CELLS = 120

# Marker written to nl_instruction so --reset can find (and only remove) the
# plans this script created, never a plan a person made.
SEED_MARKER = "seeded-by:scripts/seed_interventions.py"


# Largest polygon any seeded site may have, in cells. At 5 m (25 m2) that is
# 6,000 cells = 15 ha, roughly 390 m square -- the size of a real retention basin.
#
# This cap exists because of what happened without it. The wet grid's largest
# connected basin sprawls across nearly half the study area, so its *bounding box*
# covered 47% of the district (3.6 km2) and one seeded "retention basin" was a
# polygon that reclassified a third of Hung Shui Kiu as open water. The seeder was
# honest about it -- it recorded `boxToWetRatio` -- but the ledger downstream
# applies the polygon, not the ratio, so the scenario numbers were fiction.
MAX_SITE_CELLS = 6_000


def _shrink_to_cap(min_row: int, min_col: int, max_row: int, max_col: int,
                   centroid: tuple[int, int] | None,
                   max_cells: int) -> tuple[int, int, int, int]:
    """Shrink a bounding box about its centroid until it fits ``max_cells``.

    Shrunk symmetrically so the site stays on the part of the component that was
    actually identified, rather than sliding off it.
    """
    height = max_row - min_row + 1
    width = max_col - min_col + 1
    if height * width <= max_cells:
        return min_row, min_col, max_row, max_col

    target = max_cells
    new_h = max(1, min(height, int(target ** 0.5)))
    new_w = max(1, min(width, target // new_h))

    if centroid is None:
        cr = (min_row + max_row) // 2
        cc = (min_col + max_col) // 2
    else:
        cr, cc = centroid

    top = max(min_row, min(cr - new_h // 2, max_row - new_h + 1))
    left = max(min_col, min(cc - new_w // 2, max_col - new_w + 1))
    return top, left, top + new_h - 1, left + new_w - 1


def extent_polygon(bounds, cell_w_deg: float, cell_h_deg: float,
                   min_row: int, min_col: int, max_row: int, max_col: int,
                   centroid: tuple[int, int] | None = None,
                   max_cells: int | None = None) -> dict[str, Any]:
    """A grid-cell *extent* -> one closed GeoJSON polygon.

    Deliberately a bounding box rather than a traced outline: the component is
    8-connected and possibly concave, and a box is honest about that -- it is
    labelled as a box in ``params`` and the exact cell count is recorded
    alongside, so a reader knows the true wet area is at most the box.

    The box is additionally **capped** at ``max_cells`` about the component's
    centroid. Without the cap a sprawling concave component produced a polygon
    orders of magnitude larger than the feature it stood for, and anything
    downstream that consumed the geometry -- the storage ledger -- reclassified
    most of the district on the strength of one "retention basin".
    """
    if max_cells is not None:
        min_row, min_col, max_row, max_col = _shrink_to_cap(
            min_row, min_col, max_row, max_col, centroid, max_cells)

    lon_min, _, _, lat_max = bounds
    west = lon_min + min_col * cell_w_deg
    east = lon_min + (max_col + 1) * cell_w_deg
    north = lat_max - min_row * cell_h_deg
    south = lat_max - (max_row + 1) * cell_h_deg
    return {
        "type": "Polygon",
        "coordinates": [[
            [round(west, 7), round(south, 7)],
            [round(east, 7), round(south, 7)],
            [round(east, 7), round(north, 7)],
            [round(west, 7), round(north, 7)],
            [round(west, 7), round(south, 7)],
        ]],
    }


def cell_to_lonlat(bounds, rows, cols, cell_w_deg, cell_h_deg, row, col):
    lon_min, lat_min, lon_max, lat_max = bounds
    return (
        round(lon_min + (col + 0.5) * cell_w_deg, 6),
        round(lat_max - (row + 0.5) * cell_h_deg, 6),
    )


def _components(mask: np.ndarray) -> list[tuple[int, int, int, tuple[int, int, int, int]]]:
    """8-connected components, largest first.

    Returns ``(row, col, size, (min_r, min_c, max_r, max_c))`` -- the centroid
    for labelling, the **extent** for drawing.

    The extent is the important half. An earlier version returned only the
    centroid and the seeder drew a polygon for that single cell, so a feature
    whose ``params.areaHa`` said 155.4 ha was actually 0.003 ha of geometry and
    was invisible at map scale. A derived feature whose stated area disagrees
    with its own shape is worse than no feature.

    A small iterative flood fill rather than scipy, so the script keeps working
    with numpy alone.
    """
    seen = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, tuple[int, int, int, int]]] = []
    rows, cols = mask.shape
    for r0 in range(rows):
        for c0 in range(cols):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            stack = [(r0, c0)]
            seen[r0, c0] = True
            cells: list[tuple[int, int]] = []
            while stack:
                r, c = stack.pop()
                cells.append((r, c))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and not seen[rr, cc]:
                            seen[rr, cc] = True
                            stack.append((rr, cc))
            arr = np.array(cells)
            out.append((
                int(arr[:, 0].mean()),
                int(arr[:, 1].mean()),
                len(cells),
                (int(arr[:, 0].min()), int(arr[:, 1].min()),
                 int(arr[:, 0].max()), int(arr[:, 1].max())),
            ))
    out.sort(key=lambda t: -t[2])
    return out


def build_features() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive the intervention set from the wet grid, land cover and elevation."""
    ov = load_overlay()
    grid = ov.grid
    bounds = ov.bounds
    rows, cols = ov.rows, ov.cols
    cell_w = float(grid["cell_width_m"])
    cell_h = float(grid["cell_height_m"])
    lon_min, lat_min, _, lat_max = bounds
    cell_w_deg = cell_w / (111_320.0 * np.cos(np.radians((lat_min + lat_max) / 2)))
    cell_h_deg = cell_h / 110_574.0

    features: list[dict[str, Any]] = []

    # ---- 1. flood storage, in the deepest basins of the wet grid ---------- #
    # Read the float32 depth field, NOT the PNG. The PNG's alpha saturates at
    # MAX_VIS_DEPTH_M, and the mean depth in the wet area is roughly 1.5 m, so
    # almost every cell clips and the image cannot say where the water is
    # deepest. An earlier version of this script read the PNG, found no basin
    # deeper than the saturation limit, and silently produced zero flood-storage
    # sites -- a wrong answer with no error.
    depth_field = settings.rasters_dir / "wetgrid_hsk.npy"
    meta_path = settings.rasters_dir / "wetgrid_hsk.json"
    if depth_field.exists():
        depth = np.load(depth_field).astype(np.float64)
        if depth.shape != (rows, cols):
            raise SystemExit(
                f"wetgrid_hsk.npy is {depth.shape}, expected {(rows, cols)} — "
                f"re-run scripts/make_wetgrid.py"
            )
        rainfall = json.loads(meta_path.read_text()).get("inputs", {}).get("rainfall_mm") if meta_path.exists() else None
        depth_source = f"wetgrid_hsk.npy ({rainfall} mm)"
    else:
        print("[interventions] WARNING: wetgrid_hsk.npy is missing, so flood-storage "
              "siting is disabled. Run: python scripts/make_wetgrid.py --rainfall 450")
        depth = np.zeros((rows, cols), dtype=np.float64)
        depth_source = "unavailable"

    basins = _components(depth >= BASIN_MIN_DEPTH_M)
    basins = [b for b in basins if b[2] >= BASIN_MIN_CELLS]

    for row, col, size, extent in basins[:3]:
        min_r, min_c, max_r, max_c = _shrink_to_cap(*extent, (row, col),
                                                    MAX_SITE_CELLS)
        # Derived from the CAPPED extent, not the raw one. Computing it before the
        # cap made `areaHa` describe a polygon that no longer existed -- 447.7 ha
        # reported on a 15 ha shape -- which is the "stated size disagrees with its
        # own geometry" failure this file already warns about, reintroduced by the
        # fix for it.
        box_ha = (max_r - min_r + 1) * (max_c - min_c + 1) * cell_w * cell_h / 10_000.0
        true_ha = min(size * cell_w * cell_h / 10_000.0, box_ha)
        max_depth = float(depth[row, col])
        features.append({
            "geometry": extent_polygon(bounds, cell_w_deg, cell_h_deg,
                                       min_r, min_c, max_r, max_c),
            "intervention_type": "flood_storage",
            "style": {"color": "#1f6feb", "opacity": 0.55, "extrusionHeight": 12.0},
            "params": {
                "basis": "deepest connected basin in the computed wet grid",
                "evidenceRaster": depth_source,
                "geometry": "bounding box of the connected basin, capped at "
                             "MAX_SITE_CELLS about the component centroid",
                "areaHa": round(box_ha, 1),
                "wetCellsInBasin": size,
                "wetAreaHa": round(true_ha, 1),
                "boxToWetRatio": round(box_ha / max(true_ha, 1e-9), 2),
                "maxDepthInBasinM": round(float(depth[min_r:max_r + 1, min_c:max_c + 1].max()), 2),
                "provisional": True,
            },
        })

    # ---- 2. heat mitigation, on the largest contiguous green ------------- #
    green = ov.class_mask("green")
    patches = [p for p in _components(green) if p[2] >= GREEN_MIN_CELLS]
    for row, col, size, extent in patches[:3]:
        min_r, min_c, max_r, max_c = _shrink_to_cap(*extent, (row, col),
                                                    MAX_SITE_CELLS)
        box_ha = (max_r - min_r + 1) * (max_c - min_c + 1) * cell_w * cell_h / 10_000.0
        true_ha = min(size * cell_w * cell_h / 10_000.0, box_ha)
        features.append({
            "geometry": extent_polygon(bounds, cell_w_deg, cell_h_deg,
                                       min_r, min_c, max_r, max_c),
            "intervention_type": "urban_green",
            "style": {"color": "#2ea043", "opacity": 0.45, "extrusionHeight": 6.0},
            "params": {
                "basis": "largest contiguous vegetated block (ESA WorldCover class 'green')",
                "evidenceRaster": "landcover_hsk.npy",
                "geometry": "bounding box of the contiguous vegetated block, "
                             "capped at MAX_SITE_CELLS about the component centroid",
                "areaHa": round(box_ha, 1),
                "greenCellsInBlock": size,
                "greenAreaHa": round(true_ha, 1),
                "boxToGreenRatio": round(box_ha / max(true_ha, 1e-9), 2),
                "provisional": True,
            },
        })

    # ---- 3. drainage alignment: steepest descent from the deepest basin --- #
    if basins:
        row, col, _, _ = basins[0]
        line = [[*cell_to_lonlat(bounds, rows, cols, cell_w_deg, cell_h_deg, row, col)]]
        cur_r, cur_c = row, col
        for _ in range(40):
            best = None
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = cur_r + dr, cur_c + dc
                    if 0 <= rr < rows and 0 <= cc < cols:
                        if best is None or ov.elevation[rr, cc] < ov.elevation[best[0], best[1]]:
                            best = (rr, cc)
            if best is None:
                break
            drop = float(ov.elevation[cur_r, cur_c] - ov.elevation[best[0], best[1]])
            if drop <= 0:
                break
            cur_r, cur_c = best
            line.append(cell_to_lonlat(bounds, rows, cols, cell_w_deg, cell_h_deg, cur_r, cur_c))
            if cur_r in (0, rows - 1) or cur_c in (0, cols - 1):
                break
        if len(line) >= 2:
            features.append({
                "geometry": {"type": "LineString", "coordinates": line},
                "intervention_type": "drainage_alignment",
                "style": {"color": "#e3b341", "opacity": 0.95},
                "params": {
                    "basis": "steepest-descent trace from the deepest basin centroid",
                    "evidenceRaster": "dem_hsk.npy",
                    "vertices": len(line),
                    "totalDropM": round(float(ov.elevation[row, col] - ov.elevation[cur_r, cur_c]), 1),
                    "caveat": (
                        "Traced on a ~90 m DEM. A 90 m posting cannot resolve a "
                        "drainage alignment, so this is a sketch of the fall line, "
                        "not a design alignment."
                    ),
                },
            })

    summary = {
        "terrainSource": ov.terrain_source,
        "landcoverSource": ov.landcover_source,
        "depthSource": depth_source,
        "basinsFound": len(basins),
        "greenPatchesFound": len(patches),
        "gridRows": rows,
        "gridCols": cols,
    }
    return features, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-id", default=None, help="defaults to the seeded scene")
    parser.add_argument("--label", default="Screening interventions (derived)")
    parser.add_argument(
        "--reset", action="store_true",
        help="delete existing seeded scenario plans before creating a new one",
    )
    args = parser.parse_args()

    from app import db

    scene_id = args.scene_id
    if not scene_id:
        for path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(path)
            if doc and doc.get("study_area"):
                scene_id = doc["id"]
                break
    if not scene_id:
        print("[interventions] no seeded scene found — run scripts/seed_scene.py first")
        return 1
    if db.read_doc(db.scene_path(scene_id)) is None:
        print(f"[interventions] scene {scene_id} not found")
        return 1

    if args.reset:
        for path in db.list_docs(settings.plans_dir):
            doc = db.read_doc(path)
            if doc and doc.get("nl_instruction") == SEED_MARKER:
                # _delete_plan_files is the same cascade the scene delete uses,
                # so a re-run cannot leave orphaned intervention documents behind.
                scene_service._delete_plan_files(doc["id"])
                print(f"[interventions] removed previous seeded plan {doc['id']}")

    features, summary = build_features()
    print(f"[interventions] derived {len(features)} features")
    for f in features:
        p = f["params"]
        print(f"[interventions]   {f['intervention_type']:<22} "
              f"{p.get('areaHa', p.get('vertices', '-')):>8}  {p['basis'][:52]}")

    if not features:
        print("[interventions] nothing to create")
        return 1

    payload = PlanCreate.model_validate({
        "planType": "comprehensive",
        "label": args.label,
        "source": "manual",
        "nlInstruction": SEED_MARKER,
        "params": {
            "derivation": summary,
            "status": "SCREENING_SITING_NOT_A_DESIGN",
        },
        "interventions": features,
    })
    created = scene_service.create_plan(scene_id, payload)
    print(f"[interventions] created plan {created.plan_id} on scene {scene_id} "
          f"with {created.intervention_count} interventions")
    print(f"[interventions] terrain={summary['terrainSource']} "
          f"landcover={summary['landcoverSource']}")
    print("[interventions] every feature is status=draft; the engine will not "
          "compute on draft geometry (ADR-030)")
    print(f"[interventions] planId={created.plan_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
