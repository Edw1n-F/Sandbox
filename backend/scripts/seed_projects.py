#!/usr/bin/env python
"""Seed the planned projects as plans, one plan per project (idempotent).

A planned project is data, not code: ``data/projects/<id>.json`` declares the
project's footprint and the effects toggling it on should have. This script
turns each file into a fixed-id plan through the same ``create_plan`` the API
uses, so a selected project travels the ordinary path -- ``plan_ids`` on
``POST /run`` -- and needs no special case anywhere in the run.

    python scripts/seed_projects.py                    # seed every project
    python scripts/seed_projects.py --json             # machine-readable output

Run it after ``seed_scene.py``. Re-running is safe: a plan whose id already
exists is left alone and reported as ``reused``, so swapping in real figures
means editing the JSON and re-seeding (delete the plan to have it rebuilt).

``effects[].kind`` must be a key of ``sandbox_model.INTERVENTION_EFFECTS``; an
unknown kind is refused here rather than stored, because a stored one would be
reported at run time as an unscoreable feature and read as a bug.
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
from app.schemas.intervention import InterventionIn  # noqa: E402
from app.schemas.plan import PlanCreate  # noqa: E402
from app.services import scene_service  # noqa: E402
from app.services.sandbox_model import INTERVENTION_EFFECTS  # noqa: E402

PROJECTS_DIR = "projects"


def project_files() -> list[Path]:
    root = settings.data_dir / PROJECTS_DIR
    return sorted(p for p in root.glob("*.json") if p.is_file())


def load_project(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for key in ("id", "name", "footprint", "effects"):
        if not doc.get(key):
            raise ValueError(f"{path.name}: missing required key {key!r}")
    return doc


def footprint_geometry(doc: dict) -> dict:
    """The project's footprint as a GeoJSON geometry, from its sidecar file."""
    src = settings.data_dir / doc["footprint"]
    if not src.exists():
        raise FileNotFoundError(
            f"{doc['id']}: footprint file not found: {src}. "
            "Run scripts/compute_plan_project_footprint.py first."
        )
    feature = json.loads(src.read_text(encoding="utf-8"))
    geometry = feature.get("geometry") or feature
    if geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
        raise ValueError(f"{doc['id']}: footprint must be a GeoJSON Polygon")
    return geometry


def interventions_for(doc: dict, geometry: dict) -> list[InterventionIn]:
    """One intervention per declared effect, all on the project's footprint.

    Every effect shares the same ring: a project occupies one footprint and
    declares several consequences for it (storage, massing), rather than
    covering several plots.
    """
    out: list[InterventionIn] = []
    for effect in doc["effects"]:
        kind = effect.get("kind")
        if kind not in INTERVENTION_EFFECTS:
            raise ValueError(
                f"{doc['id']}: unknown effect kind {kind!r}. "
                f"Known kinds: {sorted(INTERVENTION_EFFECTS)}"
            )
        out.append(
            InterventionIn(
                geometry=geometry,
                intervention_type=kind,
                source="manual",
                params=effect.get("params") or {},
                style=effect.get("style") or {},
            )
        )
    return out


def plan_id_for(project_id: str) -> str:
    """The plan id a project always gets: ``project-<id>``.

    Deterministic on purpose. Projects are committed to the repo so every clone
    and every tester sees them, and a committed file needs the same name on every
    machine -- a random uuid would differ per clone, and could not be allowlisted
    in ``.gitignore`` (which ignores ``data/plans/*`` apart from named exceptions).
    The ``project-`` prefix is what that allowlist matches, so a new project needs
    no ``.gitignore`` edit.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_id)
    return f"project-{safe}"


def existing_project_ids(scene_id: str) -> dict[str, str]:
    """``projectId`` -> ``planId`` for the projects already seeded on a scene."""
    out: dict[str, str] = {}
    for plan in scene_service.list_plans(scene_id).plans:
        project_id = (plan.params or {}).get("projectId")
        if project_id:
            out[str(project_id)] = plan.plan_id
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--scene-id", default=None,
                        help="target scene (default: the seeded one)")
    args = parser.parse_args()

    from scripts.seed_scene import find_existing  # noqa: PLC0415

    scene_id = args.scene_id or find_existing("hsk-ht-nda")
    if not scene_id:
        print("[projects] no seeded scene found; run scripts/seed_scene.py first")
        return 1

    project_ids = existing_project_ids(scene_id)
    results: list[dict] = []
    for path in project_files():
        doc = load_project(path)
        geometry = footprint_geometry(doc)

        # Matched on the `projectId` param, not on the plan id, because a plan
        # seeded before ids were deterministic carries a uuid and must still be
        # recognised rather than seeded a second time. The frontend resolves a
        # project through this param for the same reason.
        if doc["id"] in project_ids:
            results.append({
                "id": doc["id"], "planId": project_ids[doc["id"]], "action": "reused",
            })
            continue

        response = scene_service.create_plan(
            scene_id,
            PlanCreate(
                plan_type="custom",
                label=doc["name"],
                source="manual",
                params={
                    "projectId": doc["id"],
                    "note": doc.get("note"),
                    "model": doc.get("model"),
                },
                interventions=interventions_for(doc, geometry),
            ),
            plan_id=plan_id_for(doc["id"]),
        )
        results.append({
            "id": doc["id"],
            "planId": response.plan_id,
            "action": "created",
            "interventions": response.intervention_count,
        })

    payload = {"sceneId": scene_id, "projects": results}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"[projects] scene {scene_id}")
        for r in results:
            extra = (f" ({r['interventions']} interventions)"
                     if r.get("interventions") else "")
            print(f"  {r['action']:8s} {r['id']} -> plan {r['planId']}{extra}")
        print("\n  A project's plan carries params.projectId, which is how the")
        print("  frontend finds it among the scene's plans. Re-seed after editing")
        print("  a project file; to rebuild one, delete its plan and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
