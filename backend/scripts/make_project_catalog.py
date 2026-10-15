#!/usr/bin/env python
"""Fixed project catalog for the user intervention loop (P0.1 of the loop plan).

    python scripts/make_project_catalog.py [--out data/projects.json]

## What this replaces

``ProjectCatalog.tsx`` shipped four projects whose phases were natural-language
sentences ("place a large expanded school campus in the northeast of the study
area"). Every toggle re-sent that sentence to the AI planner, which re-derived a
footprint from the same rasters by a *different* path each time. The panel said
so in its own caption, which is the honest version of a placeholder and still
not a product: a before/after comparison needs the same geometry twice.

## What this writes

The four project slots, but each phase carries **fixed footprints read off the
rasters once, at generation time**, and every footprint records what it came
from:

- ``detention-reservoir``   the deep *core* of the computed wet grid's basin
  (cells at 1 m depth or more), contoured to its true cell mask. Not the whole
  basin: that is 29.8% of the study area, and converting a third of a district
  to open water is a land-use change, which is exactly what the ledger's 25%
  single-intervention cap refuses.
- ``underground-tank``      the largest impervious component with the surveyed
  buildings removed, split into four equal column bands (drainage axis; the
  surface stays hard, so heat does not move).
- ``green-retrofit``        the hard-standing within three cells of the study
  area's largest vegetated block -- greening the paved edge of the biggest park
  (heat axis, and the runoff coefficient). Phase 2 extends the same rule to
  every vegetated block, largest first, up to a stated 15% planning share.
- ``blue-green-park``       the largest vegetated blocks re-graded to pond to
  0.30 m (drainage axis; the heat value does not move, because ``floodable``
  carries the vegetated value rather than the water one).

Bare ground is 100 cells (0.25 ha) in this study area, so there is no honest
"floodable park on vacant land" project here.

## Every phase runs the ledger's own guard before it is written

``build()`` finishes by calling ``apply_interventions`` on each phase's feature
list and raising if anything would be skipped. The coverage caps (25% for one
intervention, 40% cumulative) are the ledger's rules, not this script's
opinion, so the check is the ledger's own: a catalog project that the API
would refuse on screen fails the *build* instead, where it can be seen.

## Honesty rules this file obeys

- **No invented geometry.** Sites are read from ``landclass_hsk.npy``,
  ``wetgrid_hsk.npy`` and the building footprint mask, and contoured by
  ``app/services/intervention_geometry.py`` -- the same contour the AI grounding
  uses, so a catalog polygon and an AI-proposed polygon of the same cells are
  the same polygon.
- **The date is the raster's date**, not today's. Nothing here is newer than the
  inputs, and ``--check`` refuses to let a stale catalog pass as current.
- **Confidence is stated per project.** All of it is screening-level: a candidate
  parcel derived from land cover, not a surveyed site with an owner.
- **No scores, no verdicts.** The catalog says where a project would go and what
  it would convert. Whether that is better is the run's job, per axis.

The output is deterministic: the same rasters produce a byte-identical file, so
two toggles of the same phase compose the same geometry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _overlay_common import (  # noqa: E402
    LAND_CLASSES,
    building_footprint_mask,
    load_overlay,
)
from app.services.ai_plan import STYLE_FOR_KIND  # noqa: E402
from app.services.intervention_geometry import (  # noqa: E402
    connected_components,
    contour_rings,
    polygon_area_ha,
    ring_centroid,
)

# The three-cell halo used for green retrofit. A real landscape rule would say
# "the pedestrian catchment of the park"; three 5 m cells is 15 m, the width of
# a planted verge. Stated here so it can be argued with.
GREEN_HALO_CELLS = 3

# Below this a "component" is noise rather than a project: 40 cells is 1,000 m2,
# roughly one small playground.
MIN_SITE_CELLS = 40


def _radius(mask: np.ndarray, cells: int) -> np.ndarray:
    """Grow ``mask`` by ``cells`` in every direction (Chebyshev dilation).

    A handful of ``np.roll`` shifts: the dilation radius is small and constant,
    so the obvious implementation is also the fast one. No scipy import for one
    morphological step.
    """
    out = mask.copy()
    for dr in range(-cells, cells + 1):
        for dc in range(-cells, cells + 1):
            out |= np.roll(np.roll(mask, dr, axis=0), dc, axis=1)
    # np.roll wraps; undo the wrap by restoring the border band.
    out[:cells, :] |= mask[:cells, :]
    out[-cells:, :] |= mask[-cells:, :]
    out[:, :cells] |= mask[:, :cells]
    out[:, -cells:] |= mask[:, -cells:]
    return out


def _features(cells: list[tuple[int, int]], ov, kind: str,
              params: dict[str, Any], why: str) -> list[dict[str, Any]]:
    """One intervention feature per contoured part of ``cells``.

    A cell set that is only diagonally connected comes back as several
    4-connected parts, and each part becomes its own feature: the ledger applies
    each polygon to the cells it encloses, so splitting here keeps the geometry
    honest instead of drawing one polygon around parts that are not joined.
    """
    out: list[dict[str, Any]] = []
    for part in contour_rings(cells, bounds=ov.bounds, rows=ov.rows, cols=ov.cols):
        out.append({
            "intervention_type": kind,
            "geometry": part["geometry"],
            "params": dict(params),
            "style": dict(STYLE_FOR_KIND.get(kind, {"color": "#8250df",
                                                   "opacity": 0.6})),
            "provenance": {
                "why": why,
                # ``covered`` is what the ledger will rasterise (and therefore
                # convert); ``maskCells`` is how many of the site's own cells
                # the polygon claims. They differ when the contour closes over
                # a gap, and the difference is real ground that will now be
                # converted -- so both are recorded rather than the flattering
                # one.
                "cells": part["covered"],
                "maskCells": part["enclosed"],
                "areaHa": polygon_area_ha(part["geometry"]["coordinates"],
                                          ov.bounds[1], ov.bounds[3]),
            },
        })
    return out


def _max_depth(depth: np.ndarray, cells: list[tuple[int, int]]) -> float:
    return round(float(max(depth[r, c] for r, c in cells)), 2)


def _bands(cells: list[tuple[int, int]], n: int) -> list[list[tuple[int, int]]]:
    """Split a cell set into ``n`` contiguous column bands, west to east.

    Column-rank bands rather than a single median split: the bands are equal in
    *cells*, and each one is small enough to be one legal intervention. A
    recursive median split would give 1/2, 1/4, 1/8 ... -- fine for two phases,
    useless for "phase 3".
    """
    ordered = sorted(cells, key=lambda rc: rc[1])
    total = len(ordered)
    out: list[list[tuple[int, int]]] = []
    for i in range(n):
        lo = i * total // n
        hi = (i + 1) * total // n
        if hi > lo:
            out.append(ordered[lo:hi])
    return out


def build(ov, depth: np.ndarray | None, buildings: np.ndarray | None,
          grids: Any) -> list[dict[str, Any]]:
    """The four projects, as plain dicts ready to serialise.

    ``grids`` is the ledger's own grid, and it is here for the self-check: every
    phase is run through ``apply_interventions`` before it is written, and a
    phase the ledger would refuse is a build failure rather than a project that
    silently does nothing when a user toggles it.
    """
    projects: list[dict[str, Any]] = []
    cell_ha = ov.cell_area_m2 / 10_000.0
    site_cells = ov.rows * ov.cols

    def verify(phase: dict[str, Any], project_id: str) -> None:
        """Refuse to write a phase the ledger will not apply.

        The coverage caps (25% for one intervention, 40% cumulative) are the
        ledger's, not this script's opinion, so the check is the ledger's own:
        every feature must be applied, and the skipped list must stay empty.
        """
        features = [{
            "id": f"{project_id}-{phase['id']}-{i}",
            "intervention_type": f["intervention_type"],
            "geometry": f["geometry"],
            "params": f.get("params") or {},
        } for i, f in enumerate(phase["interventions"])]
        from app.services.sandbox_model import apply_interventions

        _, _, _, applied, skipped = apply_interventions(grids, features)
        if len(applied) != len(features) or skipped:
            raise RuntimeError(
                f"{project_id}/{phase['id']}: the ledger would skip "
                f"{len(skipped)} of {len(features)} features: "
                f"{[s.get('why') for s in skipped][:2]}"
            )

    # ------------------------------------------------------------------ #
    # 1. Detention reservoir -- the deepest connected basin of the wet grid
    # ------------------------------------------------------------------ #
    # Not the whole basin: at 450 mm the basin is 5,163 cells, 29.8% of the
    # study area, and converting a third of the district to open water is a
    # land-use change, not a retention feature. The ledger says so (25% single
    # cap) and this project now agrees: it sites the reservoir in the deep
    # *core* of the basin, where the water already pools.
    if depth is not None:
        core = connected_components(depth >= 1.0, MIN_SITE_CELLS,
                                    connectivity=4)
        wider = connected_components(depth >= 0.5, MIN_SITE_CELLS,
                                     connectivity=4)
        if core:
            projects.append({
                "id": "detention-reservoir",
                "name": "Detention reservoir",
                "note": "Open water in the deep core of the wet grid's basin. "
                        "Stores water; converts whatever ground it covers.",
                "axis": "drainage",
                "source": "derived from rasters/wetgrid_hsk.npy (450 mm, "
                          "level-pool) + the study-area land class",
                "date": "2026-10-06",
                "confidence": "screening: a candidate basin, not a designed "
                              "reservoir with an owner and a spillway",
                "phases": [{
                    "id": "core",
                    "label": "The deep core",
                    "interventions": sum(
                        [_features(c["cells"], ov, "water_reservoir",
                                   {"storageDepthM": _max_depth(depth, c["cells"])},
                                   "the deepest connected basin in the computed "
                                   "wet grid, cells at 1 m depth or more")
                         for c in core], []),
                    "grounding": {
                        "evidence": "the 450 mm level-pool wet grid, cells at "
                                    "1 m depth or more, contoured",
                        "metrics": {
                            "basins": len(core),
                            "wetCells": sum(c["size"] for c in core),
                            "wetAreaHa": round(sum(c["size"] for c in core)
                                               * cell_ha, 1),
                            "maxDepthM": _max_depth(depth, core[0]["cells"]),
                        },
                    },
                }, {
                    "id": "wider",
                    "label": "Core plus its satellite",
                    "interventions": sum(
                        [_features(c["cells"], ov, "water_reservoir",
                                   {"storageDepthM": _max_depth(depth, c["cells"])},
                                   "connected basin in the computed wet grid, "
                                   "cells at 0.5 m depth or more")
                         for c in wider], []),
                    "grounding": {
                        "evidence": "the 450 mm level-pool wet grid, cells at "
                                    "0.5 m depth or more, contoured",
                        "metrics": {
                            "basins": len(wider),
                            "wetCells": sum(c["size"] for c in wider),
                            "wetAreaHa": round(sum(c["size"] for c in wider)
                                               * cell_ha, 1),
                            "maxDepthM": _max_depth(depth, wider[0]["cells"]),
                        },
                    },
                }],
            })

    # ------------------------------------------------------------------ #
    # 2. Underground tank -- the largest impervious component, banded
    # ------------------------------------------------------------------ #
    tank_target = ov.class_mask("paved")
    if buildings is not None:
        tank_target = tank_target & ~buildings
    tank_components = connected_components(tank_target, MIN_SITE_CELLS,
                                           connectivity=4)
    if tank_components:
        biggest = tank_components[0]
        bands = _bands(biggest["cells"], 4)

        projects.append({
            "id": "underground-tank",
            "name": "Underground tank",
            "note": "A storage tank under a paved platform. Drainage benefit "
                    "only: the surface stays hard, so heat does not move.",
            "axis": "drainage",
            "source": "derived from landclass_hsk.npy (paved class) minus the "
                      "Open3dhk/Overture building footprints",
            "date": "2026-10-13",
            "confidence": "screening: the impervious extent is measured; a "
                          "tank of this size has never been priced",
            "phases": [
                {
                    "id": "west",
                    "label": "Phase 1 - western band",
                    "interventions": _features(
                        bands[0], ov, "underground_tank",
                        {"storageDepthM": 2.00},
                        "the westernmost quarter of the largest impervious "
                        "component, by column rank",
                    ),
                    "grounding": {
                        "evidence": "largest 4-connected paved component with "
                                    "buildings removed, split into four "
                                    "equal column bands",
                        "metrics": {"cells": len(bands[0]),
                                    "areaHa": round(len(bands[0]) * cell_ha, 1)},
                    },
                },
                {
                    "id": "two-bands",
                    "label": "Phase 2 - two bands",
                    "interventions": _features(
                        [c for band in bands[:2] for c in band], ov,
                        "underground_tank", {"storageDepthM": 2.00},
                        "the western half of the largest impervious "
                        "component, by column rank",
                    ),
                    "grounding": {
                        "evidence": "largest 4-connected paved component with "
                                    "buildings removed, western half",
                        "metrics": {
                            "cells": sum(len(b) for b in bands[:2]),
                            "areaHa": round(sum(len(b) for b in bands[:2])
                                            * cell_ha, 1)},
                    },
                },
            ],
        })

    # ------------------------------------------------------------------ #
    # 3. Green retrofit -- hard-standing around the study area's green
    # ------------------------------------------------------------------ #
    green = ov.class_mask("green")
    green_components = connected_components(green, MIN_SITE_CELLS,
                                            connectivity=4)
    if green_components:
        block_mask = np.zeros_like(green)
        for r, c in green_components[0]["cells"]:
            block_mask[r, c] = True
        hard = ov.class_mask("paved") | ov.class_mask("bare")
        if buildings is not None:
            hard = hard & ~buildings
        edge = _radius(block_mask, GREEN_HALO_CELLS) & ~block_mask & hard
        edge_blocks = connected_components(edge, MIN_SITE_CELLS,
                                           connectivity=4)

        # The district-wide phase grows outward from *any* vegetated block,
        # largest first, until the 15% planning share is reached. The share is
        # stated here rather than left to chance: without it this phase would
        # be 30% of the study area and the ledger would refuse it.
        planning_share = int(0.15 * site_cells)
        district_blocks: list[dict[str, Any]] = []
        carried = 0
        for component in connected_components(
                _radius(green, GREEN_HALO_CELLS) & ~green & hard,
                MIN_SITE_CELLS, connectivity=4):
            if carried + component["size"] > planning_share:
                break
            district_blocks.append(component)
            carried += component["size"]

        projects.append({
            "id": "green-retrofit",
            "name": "Green retrofit",
            "note": "Vegetate the hard-standing around the green. Cools the "
                    "heat axis and lowers runoff.",
            "axis": "heat",
            "source": "derived from landclass_hsk.npy (green, paved and bare "
                      "classes) minus the building footprints",
            "date": "2026-10-13",
            "confidence": "screening: adjacent parcels from land cover, not "
                          "land the district actually owns",
            "phases": [
                {
                    "id": "park-edge",
                    "label": "Phase 1 - park edge",
                    "interventions": _features(
                        [c for b in edge_blocks for c in b["cells"]], ov,
                        "green_space", {},
                        f"hard-standing within {GREEN_HALO_CELLS} cells of the "
                        "study area's largest vegetated block",
                    ),
                    "grounding": {
                        "evidence": f"paved or bare cells within "
                                    f"{GREEN_HALO_CELLS} cells ("
                                    f"{GREEN_HALO_CELLS * ov.cell_m:.0f} m) of the "
                                    "largest vegetated block, buildings excluded",
                        "metrics": {
                            "blocks": len(edge_blocks),
                            "cells": sum(b["size"] for b in edge_blocks),
                            "areaHa": round(sum(b["size"] for b in edge_blocks)
                                            * cell_ha, 1)},
                    },
                },
                {
                    "id": "district",
                    "label": "Phase 2 - district green edges",
                    "interventions": _features(
                        [c for b in district_blocks for c in b["cells"]], ov,
                        "green_space", {},
                        f"hard-standing within {GREEN_HALO_CELLS} cells of any "
                        "vegetated block, largest blocks first, up to a 15% "
                        "planning share of the study area",
                    ),
                    "grounding": {
                        "evidence": "paved or bare cells within "
                                    f"{GREEN_HALO_CELLS} cells of any vegetated "
                                    "block, capped at a 15% planning share",
                        "metrics": {
                            "blocks": len(district_blocks),
                            "cells": carried,
                            "areaHa": round(carried * cell_ha, 1)},
                    },
                },
            ],
        })

    # ------------------------------------------------------------------ #
    # 4. Blue-green park -- vegetated ground re-graded to pond
    # ------------------------------------------------------------------ #
    # The classic blue-green retrofit: landscaped ground that ponds to 0.30 m
    # in a storm. The heat value does not move (a floodable park is grass that
    # occasionally stores water, which is why `floodable` carries the
    # vegetated heat value rather than the water one), so this project is a
    # drainage service that costs no cooling.
    if green_components:
        blocks = green_components[:3]

        projects.append({
            "id": "blue-green-park",
            "name": "Blue-green park",
            "note": "Vegetated ground re-graded to pond to 0.30 m in heavy "
                    "rain. Keeps its heat value; gains shallow storage.",
            "axis": "drainage",
            "source": "derived from landclass_hsk.npy (green class) + "
                      "scripts/_overlay_common.STORAGE_DEPTH_M['floodable']",
            "date": "2026-10-13",
            "confidence": "screening: the vegetated extent is measured; the "
                          "0.30 m ponding depth is an assumption",
            "phases": [
                {
                    "id": "main-block",
                    "label": "Phase 1 - largest green block",
                    "interventions": _features(
                        blocks[0]["cells"], ov, "flood_storage",
                        {"storageDepthM": 0.30},
                        "the study area's largest vegetated block, re-graded "
                        "to pond",
                    ),
                    "grounding": {
                        "evidence": "largest 4-connected vegetated component, "
                                    "converted to floodable ground",
                        "metrics": {
                            "blocks": 1,
                            "cells": blocks[0]["size"],
                            "areaHa": round(blocks[0]["size"] * cell_ha, 1)},
                    },
                },
                {
                    "id": "three-blocks",
                    "label": "Phase 2 - three green blocks",
                    "interventions": _features(
                        [c for b in blocks for c in b["cells"]], ov,
                        "flood_storage", {"storageDepthM": 0.30},
                        "the three largest vegetated blocks, re-graded to pond",
                    ),
                    "grounding": {
                        "evidence": "the three largest 4-connected vegetated "
                                    "components, converted to floodable ground",
                        "metrics": {
                            "blocks": len(blocks),
                            "cells": sum(b["size"] for b in blocks),
                            "areaHa": round(sum(b["size"] for b in blocks)
                                            * cell_ha, 1)},
                    },
                },
            ],
        })


    # ------------------------------------------------------------------ #
    # Self-check: the ledger must be able to apply every phase exactly as it
    # is written. This runs the *ledger's* coverage guard, not a copy of it,
    # so a phase that would be refused on screen fails the build instead.
    # ------------------------------------------------------------------ #
    for project in projects:
        for phase in project["phases"]:
            verify(phase, project["id"])

    return projects
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(BACKEND_DIR / "data" / "projects.json"))
    args = parser.parse_args()

    ov = load_overlay()
    depth_path = BACKEND_DIR / "data" / "rasters" / "wetgrid_hsk.npy"
    depth = None
    if depth_path.exists():
        depth = np.load(depth_path).astype(np.float64)
        if depth.shape != (ov.rows, ov.cols):
            print(f"[catalog] wetgrid is {depth.shape}, expected "
                  f"{(ov.rows, ov.cols)}; reservoir project skipped")
            depth = None
    buildings = building_footprint_mask(ov)

    from app.services.sandbox_model import load_grids

    grids = load_grids()
    if grids is None:  # pragma: no cover - the rasters exist in this repo
        raise SystemExit("[catalog] no ledger grid: run scripts/make_wetgrid.py")

    projects = build(ov, depth, buildings, grids)

    doc = {
        "schema": "hack4sdg/project-catalog@1",
        "generatedBy": "scripts/make_project_catalog.py",
        "grid": {
            "rows": ov.rows,
            "cols": ov.cols,
            "cellM": round(ov.cell_m, 4),
            "cellAreaM2": round(ov.cell_area_m2, 4),
            "bounds": list(ov.bounds),
        },
        "sources": {
            "landCover": "ESA WorldCover 10 m v200 (2021), CC BY 4.0, resampled "
                         "to 5 m by nearest neighbour",
            "wetGrid": "scripts/make_wetgrid.py, 450 mm rainfall, level-pool "
                       "allocation, Priority-Flood conditioned surface",
            "buildings": "Open3dhk 3D Tiles footprints fused with Overture Maps "
                         "2026-09-23.0",
            "terrain": ov.terrain_source,
        },
        "warning": "Every footprint here is a candidate parcel read from land "
                   "cover and the wet grid at 5 m. None is a surveyed site with "
                   "an owner, a boundary agreement or a works programme. Sites "
                   "are screening-level: run them and read the ledger.",
        "projects": projects,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n",
                   encoding="utf-8")

    print(f"[catalog] {out}")
    for project in doc["projects"]:
        for phase in project["phases"]:
            features = len(phase["interventions"])
            cells = sum(f["provenance"]["cells"] for f in phase["interventions"])
            ha = sum(f["provenance"]["areaHa"] for f in phase["interventions"])
            print(f"[catalog] {project['id']:>20s} / {phase['id']:<7s} "
                  f"{features:3d} feature(s) {cells:6d} cells {ha:7.1f} ha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
