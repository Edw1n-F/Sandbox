#!/usr/bin/env python
"""Seed the HSK/HT baseline scene and its ``baseline`` plan (B9).

Idempotent: re-running updates the existing baseline scene in place rather than
piling up duplicates, so it is safe to call after regenerating overlays.

    python scripts/seed_scene.py            # seed (or refresh) and print the sceneId
    python scripts/seed_scene.py --json     # machine-readable output

Run it from anywhere; ``backend/`` is put on ``sys.path`` explicitly so the
script does not depend on the caller's working directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.schemas.plan import PlanCreate  # noqa: E402
from app.schemas.scene import (  # noqa: E402
    DefaultCamera,
    EnvParams,
    SceneCreate,
    SceneUpdate,
)
from app import db  # noqa: E402
from app.services import scene_service  # noqa: E402
from app.services.geo_import import (  # noqa: E402
    build_grid,
    load_study_area,
    polygon_bounds,
)

# Imagery has no Ion token (ADR-033).  An OSM tile template keeps the field
# populated and honest: the frontend still treats it as an opaque reference, so
# swapping in a real COG later is a one-line change here.
OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def find_existing(area_id: str) -> str | None:
    """Return the sceneId of an already-seeded scene for this study area, if any.

    Scenarios are matched on the ``study_area`` tag written at the end of a
    previous run rather than on the scene name, so renaming the study area does
    not orphan the old scene.
    """
    for path in db.list_docs(settings.scenes_dir):
        doc = db.read_doc(path)
        if doc and doc.get("study_area") == area_id:
            return doc["id"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--area",
        default="scene_hsk.json",
        help="study-area file under backend/data (default: scene_hsk.json)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the existing seeded scene (and its plans) before seeding",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    settings.ensure_dirs()
    area = load_study_area(settings.data_dir / args.area)
    area_id = area["id"]

    existing = find_existing(area_id)
    if existing and args.reset:
        scene_service.delete_scene(existing)
        print(f"[seed] removed previous scene {existing}", file=sys.stderr)
        existing = None

    env_params = area.get("envParams") or {}
    camera = area.get("defaultCamera") or {}
    bbox = area.get("bbox")

    # The terrain-RGB raster is written by scripts/fetch_dem.py, not by this
    # script, so its presence is a fact on disk rather than something we can
    # assume. Setting terrain_url to a file that does not exist would give the
    # viewer a URL that 404s on every tile request; leaving it null makes the
    # frontend fall back to EllipsoidTerrainProvider, which is visibly flat and
    # therefore honestly so. Either way the scene records what is actually
    # there. (This used to be hard-coded to null with a comment saying no real
    # DEM existed -- it does, and the committed scene carries its URL.)
    terrain_png = settings.data_dir / "dem_terrain_rgb.png"
    terrain_url = "dem_terrain_rgb.png" if terrain_png.exists() else None
    if terrain_url is None:
        print("[seed] no dem_terrain_rgb.png -- run scripts/fetch_dem.py first; "
              "the scene will boot on a flat ellipsoid", file=sys.stderr)

    if existing:
        scene_id = existing
        scene_service.update_scene(
            scene_id,
            SceneUpdate(
                name=area["name"],
                center=area["center"],
                bbox=bbox,
                terrain_url=terrain_url,
                imagery_url=OSM_TILE_TEMPLATE,
                default_camera=DefaultCamera(**camera) if camera else None,
                env_params=EnvParams(**env_params) if env_params else None,
            ),
        )
        action = "refreshed"
    else:
        created = scene_service.create_scene(
            SceneCreate(
                name=area["name"],
                center=area["center"],
                bbox=bbox,
                terrain_url=terrain_url,
                imagery_url=OSM_TILE_TEMPLATE,
                default_camera=DefaultCamera(**camera) if camera else DefaultCamera(),
                env_params=EnvParams(**env_params) if env_params else EnvParams(),
            )
        )
        scene_id = created.scene_id
        scene = scene_service.get_scene(scene_id)
        action = "created"

    # Tag the scene so --reset and re-seeding can find it again, and record the
    # metric working grid the overlay scripts will use (ADR-009).
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is not None and bbox:
        doc["study_area"] = area_id
        doc["working_grid"] = build_grid(
            polygon_bounds(bbox),
            resolution_m=((area.get("provenance", {}).get("grid", {})) or {}).get(
                "resolution_m", 5
            ),
        )
        db.write_doc(db.scene_path(scene_id), doc)

    # One baseline plan.  Interventions are intentionally empty today: the spec
    # defers the physics engine, and §2.3.1 keeps wet grid / heatmap as reserved
    # fields.  An empty FeatureCollection is a valid, renderable answer.
    plans = scene_service.list_plans(scene_id)
    baseline = next((p for p in plans.plans if p.plan_type == "baseline"), None)
    if baseline is None:
        response = scene_service.create_plan(
            scene_id,
            PlanCreate(
                plan_type="baseline",
                label="基线 Baseline (existing land use)",
                source="manual",
                params={"note": "Today's land use. No planned projects applied."},
                interventions=[],
            ),
        )
        plan_id = response.plan_id
        plan_action = "created"
    else:
        plan_id = baseline.plan_id
        plan_action = "reused"

    payload = {
        "sceneId": scene_id,
        "planId": plan_id,
        "scene": action,
        "plan": plan_action,
        "name": area["name"],
        "bbox": bbox,
        "center": area["center"],
        "imageryUrl": OSM_TILE_TEMPLATE,
        "terrainUrl": None,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[seed] scene {action}: {scene_id}")
        print(f"[seed] plan  {plan_action}: {plan_id}")
        print(f"[seed] name  : {area['name']}")
        print()
        print("Next:")
        print(f"  curl -s localhost:8000/api/scenes/{scene_id}")
        print(f"  curl -s localhost:8000/api/scenes/{scene_id}/terrain")
        print(f"  curl -s localhost:8000/api/scenes/{scene_id}/plans")
        print(f"  curl -s localhost:8000/api/plans/{plan_id}/interventions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
