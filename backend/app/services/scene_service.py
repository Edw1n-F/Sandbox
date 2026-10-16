"""Scene / plan / intervention service -- the single data authority (CLAUDE.md).

Every read and write in the system passes through here.  The frontend never
touches storage directly, and the AI/deterministic boundary is enforced in this
one place (ADR-026, ADR-030).

Storage is snake_case and mirrors §4.2's DDL column-for-column.  The wire is
camelCase, and the translation happens exclusively in ``app.schemas``.  That
split is what resolves the spec's own ``id`` vs ``sceneId`` contradiction
without renaming a column (ADR-027).

Cascade: ``scene -> plan -> intervention``.  Deleting a scene removes its plans
and their interventions, matching ``ON DELETE CASCADE`` in §4.2.  The file store
has no transactions, so the cascade deletes children first and the parent last.
If it is interrupted, the residue is orphaned *children* -- visible and
harmless -- rather than a parent scene pointing at nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import db
from app.config import settings
from app.schemas.intervention import (
    InterventionCreateResponse,
    InterventionFeature,
    InterventionFeatureCollection,
    InterventionProperties,
)
from app.schemas.plan import PlanCreate, PlanListRead, PlanRead, PlanUpdate
from app.schemas.scene import (
    ProjectModel,
    SceneCreate,
    SceneCreated,
    SceneRead,
    SceneUpdate,
    TerrainRead,
    UnavailableProject,
    close_ring,
)

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ResourceNotFound(LookupError):
    """Maps to 404. ``code`` feeds the unified error envelope (ADR-028)."""

    code = "NOT_FOUND"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class SceneNotFound(ResourceNotFound):
    code = "SCENE_NOT_FOUND"

    def __init__(self, scene_id: str) -> None:
        super().__init__("scene not found", self.code)
        self.scene_id = scene_id


class PlanNotFound(ResourceNotFound):
    code = "PLAN_NOT_FOUND"

    def __init__(self, plan_id: str) -> None:
        super().__init__("plan not found", self.code)
        self.plan_id = plan_id


class InterventionNotFound(ResourceNotFound):
    code = "INTERVENTION_NOT_FOUND"

    def __init__(self, intervention_id: str) -> None:
        super().__init__("intervention not found", self.code)
        self.intervention_id = intervention_id


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _now() -> str:
    return db.utcnow()


def _normalise_bbox(scene: dict[str, Any]) -> dict[str, Any]:
    """Repair a short bbox ring on the way in (§3.2.2 ships a 2-coordinate ring)."""
    if scene.get("bbox"):
        scene["bbox"] = close_ring(scene["bbox"])
    return scene


def _scene_to_read(doc: dict[str, Any]) -> SceneRead:
    """Storage row -> wire, resolving overlay URLs against what is on disk."""
    read = SceneRead.from_row(doc)
    read.wet_grid_url = _resolve_overlay_url(doc, "wet_grid_path")
    read.heatmap_url = _resolve_overlay_url(doc, "heatmap_path")
    # The city model + its provenance are resolved here too, not only in
    # `get_terrain`: the frontend's `loadScene` reads `SceneRead`, and a
    # `cityModelUrl` that is null on the scene but set on the terrain
    # endpoint is the kind of half-truth that breaks a different consumer
    # later. Same existence check, same meta helper.
    read.buildings_url = _resolve_overlay_url(doc, "buildings_path")
    read.buildings_meta = _buildings_meta()
    read.buildings_3d_url = _resolve_model_3d_url(doc, "buildings_3d_path")
    return read


def _resolve_overlay_url(doc: dict[str, Any], key: str) -> str | None:
    """Turn a stored overlay path into a URL, or ``None`` if the file is absent.

    Existence is checked rather than assumed: advertising a wet-grid URL whose
    PNG was never generated produces a broken layer in the viewer, which is
    worse than reporting the layer as absent.

    The URL carries an ``?v=<mtime>`` cache-buster. Without it the browser keeps
    the first copy of a re-rendered PNG, so a regenerated overlay (e.g. the
    heatmap after a ramp change) shows stale pixels until a hard refresh -- the
    exact "I changed it and nothing happened" trap.
    """
    rel = doc.get(key)
    if not rel:
        return None
    absolute = settings.data_dir / rel
    if not absolute.exists():
        return None
    url = settings.public_url(rel)
    version = int(absolute.stat().st_mtime)
    return f"{url}{'&' if '?' in url else '?'}v={version}"


def _resolve_model_3d_url(doc: dict[str, Any], key: str) -> str | None:
    """Turn a model-relative path (``city_model/tileset.json``) into a URL.

    Strips a leading ``model/`` prefix if present so that the stored value can
    be either ``model/city_model/tileset.json`` or ``city_model/tileset.json``
    and both resolve correctly under ``/static/model/``.
    """
    rel = doc.get(key)
    if not rel:
        return None
    rel = rel.lstrip("/")
    # Accept legacy full path "model/city_model/..." or bare "city_model/..."
    if rel.startswith("model/"):
        rel = rel[len("model/"):]
    absolute = settings.model_dir / rel
    if not absolute.exists():
        return None
    return settings.public_model_url(rel)

# --------------------------------------------------------------------------- #
# Scene CRUD
# --------------------------------------------------------------------------- #


def create_scene(payload: SceneCreate) -> SceneCreated:
    db.assert_store_mode()
    settings.ensure_dirs()

    scene_id = db.new_id()
    now = _now()

    doc: dict[str, Any] = {
        # --- storage (§4.2.1, snake_case) ---------------------------------
        "id": scene_id,
        "name": payload.name,
        "center": payload.center,
        "bbox": payload.bbox,
        "terrain_url": payload.terrain_url,
        "imagery_url": payload.imagery_url,
        "default_camera": payload.default_camera.model_dump(exclude_none=True),
        "env_params": payload.env_params.model_dump(exclude_none=True),
        "wet_grid_path": None,
        "heatmap_path": None,
        "created_at": now,
        "updated_at": now,
    }
    _normalise_bbox(doc)
    db.write_doc(db.scene_path(scene_id), doc)

    return SceneCreated(scene_id=scene_id, name=payload.name, created_at=now)


def get_scene(scene_id: str) -> SceneRead:
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)
    return _scene_to_read(doc)


def update_scene(scene_id: str, payload: SceneUpdate) -> SceneRead:
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)

    # exclude_unset, not exclude_none: a PUT that omits a field must not erase it,
    # but a PUT that explicitly sends null must.
    changes = payload.model_dump(exclude_unset=True)
    doc.update(changes)
    if "default_camera" in changes and changes["default_camera"] is not None:
        doc["default_camera"] = {
            k: v for k, v in changes["default_camera"].items() if v is not None
        }
    if "env_params" in changes and changes["env_params"] is not None:
        doc["env_params"] = {
            k: v for k, v in changes["env_params"].items() if v is not None
        }

    doc["updated_at"] = _now()
    _normalise_bbox(doc)
    db.write_doc(db.scene_path(scene_id), doc)
    return _scene_to_read(doc)


def delete_scene(scene_id: str) -> None:
    """Cascade scene -> plan -> intervention (§3.2.4)."""
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)

    for plan in list_plans(scene_id).plans:
        _delete_plan_files(plan.plan_id)

    db.delete_doc(db.scene_path(scene_id))


def _delete_plan_files(plan_id: str) -> None:
    plan_doc = db.read_doc(db.plan_path(plan_id))
    if plan_doc:
        for intervention in plan_doc.get("interventions", []):
            db.delete_doc(db.intervention_path(intervention["id"]))
    db.delete_doc(db.plan_path(plan_id))


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


def list_plans(scene_id: str) -> PlanListRead:
    """GET /api/scenes/{sceneId}/plans.  404s if the scene is unknown."""
    if db.read_doc(db.scene_path(scene_id)) is None:
        raise SceneNotFound(scene_id)

    plans: list[PlanRead] = []
    for path in db.list_docs(settings.plans_dir):
        doc = db.read_doc(path)
        if not doc or doc.get("scene_id") != scene_id:
            continue
        plans.append(PlanRead.from_row(doc))
    return PlanListRead(plans=plans)


def create_plan(scene_id: str, payload: PlanCreate, *,
                plan_id: str | None = None) -> InterventionCreateResponse:
    """Persist a plan and its interventions.

    ``plan_id`` is for the seeder only: a planned project is committed to the repo
    so every clone has it, and a committed file needs a name that is the same on
    every machine. A random uuid cannot be allowlisted in ``.gitignore`` and would
    differ per clone. The HTTP route never passes it, so API-created plans keep
    their minted uuid. Interventions then take ``<plan_id>-<n>``.
    """
    if db.read_doc(db.scene_path(scene_id)) is None:
        raise SceneNotFound(scene_id)

    settings.ensure_dirs()
    fixed = plan_id is not None
    plan_id = plan_id or db.new_id()
    now = _now()

    stored: list[dict[str, Any]] = []
    for index, item in enumerate(payload.interventions):
        intervention_id = f"{plan_id}-{index}" if fixed else db.new_id()
        record = {
            "id": intervention_id,
            "plan_id": plan_id,
            "geometry_type": item.geometry_type,
            "geometry": item.geometry,
            "intervention_type": item.intervention_type,
            "source": item.source,
            "params": item.params,
            "style": item.style,
            # ADR-030: an AI-originated feature is always a draft, whatever the
            # caller asked for.  The engine refuses to compute on drafts.
            "status": "draft",
            "created_at": now,
            "updated_at": now,
        }
        db.write_doc(db.intervention_path(intervention_id), record)
        stored.append(record)

    plan_doc = {
        "id": plan_id,
        "scene_id": scene_id,
        "plan_type": payload.plan_type,
        "label": payload.label,
        "source": payload.source,
        "nl_instruction": payload.nl_instruction,
        "params": payload.params,
        "interventions": stored,
        "created_at": now,
        "updated_at": now,
    }
    db.write_doc(db.plan_path(plan_id), plan_doc)

    return InterventionCreateResponse(
        plan_id=plan_id, scene_id=scene_id, intervention_count=len(stored)
    )


def update_plan(plan_id: str, payload: PlanUpdate) -> PlanRead:
    doc = db.read_doc(db.plan_path(plan_id))
    if doc is None:
        raise PlanNotFound(plan_id)

    changes = payload.model_dump(exclude_unset=True, exclude={"interventions"})
    doc.update(changes)

    if payload.interventions is not None:
        for old in doc.get("interventions", []):
            db.delete_doc(db.intervention_path(old["id"]))
        now = _now()
        stored: list[dict[str, Any]] = []
        for item in payload.interventions:
            intervention_id = db.new_id()
            record = {
                "id": intervention_id,
                "plan_id": plan_id,
                "geometry_type": item.geometry_type,
                "geometry": item.geometry,
                "intervention_type": item.intervention_type,
                "source": item.source,
                "params": item.params,
                "style": item.style,
                "status": "draft",
                "created_at": now,
                "updated_at": now,
            }
            db.write_doc(db.intervention_path(intervention_id), record)
            stored.append(record)
        doc["interventions"] = stored

    doc["updated_at"] = _now()
    db.write_doc(db.plan_path(plan_id), doc)
    return PlanRead.from_row(doc)


# --------------------------------------------------------------------------- #
# Interventions
# --------------------------------------------------------------------------- #


def list_interventions(plan_id: str) -> InterventionFeatureCollection:
    """GET /api/plans/{planId}/interventions -> a real GeoJSON FeatureCollection.

    An empty ``features: []`` is a valid, renderable answer.  Geometry is passed
    through byte-identically so Cesium's GeoJsonDataSource needs no conversion
    (CLAUDE.md principle 2).
    """
    doc = db.read_doc(db.plan_path(plan_id))
    if doc is None:
        raise PlanNotFound(plan_id)

    features = [
        InterventionFeature(
            id=item["id"],
            geometry=item["geometry"],
            properties=InterventionProperties(
                id=item["id"],
                intervention_type=item["intervention_type"],
                style=item.get("style", {}),
                source=item.get("source", "manual"),
                status=item.get("status", "draft"),
                params=item.get("params", {}),
                # The pin state is a top-level field of the stored row; carry
                # it so the run composition sees it (default True keeps
                # pre-pinning rows composed as before).
                pinned=bool(item.get("pinned", True)),
            ),
        )
        for item in doc.get("interventions", [])
    ]
    return InterventionFeatureCollection(features=features)


def delete_intervention(plan_id: str, intervention_id: str) -> None:
    """DELETE /api/plans/{planId}/interventions/{interventionId}.

    Hard delete (plan v2 §6.2 Decision 2): no soft-delete, no undo. Keeps the
    plan doc's embedded `interventions` list in sync with the standalone
    intervention file so `list_interventions` and the raster/run paths agree.
    """
    doc = db.read_doc(db.plan_path(plan_id))
    if doc is None:
        raise PlanNotFound(plan_id)

    items = doc.get("interventions", [])
    remaining = [item for item in items if item.get("id") != intervention_id]
    if len(remaining) == len(items):
        raise InterventionNotFound(intervention_id)

    doc["interventions"] = remaining
    doc["updated_at"] = _now()
    db.write_doc(db.plan_path(plan_id), doc)
    db.delete_doc(db.intervention_path(intervention_id))


# --------------------------------------------------------------------------- #
# Terrain
# --------------------------------------------------------------------------- #


def get_terrain(scene_id: str) -> TerrainRead:
    """GET /api/scenes/{sceneId}/terrain (§3.4)."""
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)

    # Drawn projects and undrawable ones, from one read of data/projects/ -- so
    # the two lists cannot disagree about which files exist.
    _projects = project_models()
    return TerrainRead(
        # Resolved, existence-checked, exactly like the overlays: a stored
        # relative DEM path becomes a URL the viewer can actually fetch, and a
        # path with no file behind it becomes None rather than a broken layer.
        terrain_url=_resolve_overlay_url(doc, "terrain_url"),
        imagery_url=doc.get("imagery_url"),
        bbox=doc.get("bbox"),
        center=doc.get("center"),
        terrain_format=doc.get("terrain_format"),
        terrain_source=doc.get("terrain_source"),
        wet_grid_url=_resolve_overlay_url(doc, "wet_grid_path"),
        heatmap_url=_resolve_overlay_url(doc, "heatmap_path"),
        buildings_url=_resolve_overlay_url(doc, "buildings_path"),
        buildings_meta=_buildings_meta(),
        buildings_3d_url=_resolve_model_3d_url(doc, "buildings_3d_path"),
        terrain_3d_url=_resolve_model_3d_url(doc, "terrain_3d_path"),
        waterbody_url=_resolve_overlay_url(doc, "waterbody_path"),
        infrastructure_url=_resolve_overlay_url(doc, "infrastructure_path"),
        projects=_projects[0],
        projects_unavailable=_projects[1],
    )


def project_models() -> tuple[list[ProjectModel], list[Any]]:
    """Every seeded planned project that has a model, and every one that does not.

    Reads `data/projects/*.json` -- the same files `seed_projects.py` turns into
    plans -- so a project's URL, origin and scale have one home and adding a
    second one needs no code.

    A project whose GLB or footprint is not on disk is **not drawn** -- the
    viewer should draw what exists, and a missing file is a build step not run,
    not a broken project to render a placeholder for. But it is **not silently
    dropped either**: it comes back in the second half of the pair, with the
    artefact named, because a shorter `projects` list is otherwise
    indistinguishable from a dataset that never held the project. Those are
    different facts and only one of them is worth acting on.

    The pair is returned rather than a single list with an `available` flag on
    each entry: the viewer iterates `projects` to draw, and a flag there would
    make "should I draw this" a per-item decision the caller can get wrong.

    Sorted by id so the response is stable between calls; an unstable list would
    make the viewer tear down and rebuild its models on every scene fetch.
    """
    out: list[ProjectModel] = []
    unavailable: list[Any] = []
    root = settings.data_dir / "projects"
    if not root.is_dir():
        return out, unavailable
    for path in sorted(root.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        project_id = doc.get("id") or path.stem
        name = doc.get("name") or project_id
        model_rel = doc.get("model")
        footprint_rel = doc.get("footprint")
        missing: list[str] = []
        if not model_rel:
            missing.append("model")
        if not footprint_rel:
            missing.append("footprint")
        if not missing:
            model_path = settings.model_dir / model_rel
            footprint_path = settings.data_dir / footprint_rel
            if not model_path.exists():
                missing.append("model")
            if not footprint_path.exists():
                missing.append("footprint")
        if missing:
            unavailable.append(UnavailableProject(
                project_id=project_id,
                name=name,
                missing=missing,
                reason=(
                    "the project is seeded in data/projects/ but its "
                    + " and its ".join(missing) + " is not on disk, so it is "
                    "not drawn. Run the build step that produces it (a GLB is "
                    "tracked with git-lfs; a checkout without the LFS objects "
                    "has a 133-byte pointer instead of the model)."),
            ))
            continue
        origin = doc.get("origin")
        out.append(ProjectModel(
            project_id=project_id,
            name=name,
            model_url=settings.public_model_url(model_rel),
            footprint_url=settings.public_url(footprint_rel),
            origin=(float(origin[0]), float(origin[1])) if origin else None,
            scale=float(doc.get("scale") or 1.0),
            label=doc.get("label"),
        ))
    return out, unavailable


def buildings_path(scene_id: str) -> Path | None:
    """Absolute path to a scene's massing model, or None if it has no file.

    Distinct from the URL helper on purpose. The viewer wants a URL; a service
    that has to read the GeoJSON wants the file, and handing it a "/static/..."
    string would be a small lie that only fails later.
    """
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        return None
    rel = doc.get("buildings_path")
    if not rel:
        return None
    absolute = settings.data_dir / rel
    return absolute if absolute.exists() else None


def _buildings_meta() -> dict[str, Any] | None:
    """Provenance for the 3D massing model, or None when it has not been built.

    The status string is deliberately blunt. These are massing envelopes derived
    from land cover, with an assumed height distribution -- not surveyed
    buildings -- and the viewer shows this rather than presenting a skyline as
    if it were measured.
    """
    path = settings.rasters_dir / "buildings_meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def attach_overlay(scene_id: str, key: str, relative_path: str) -> None:
    """Register a generated overlay PNG against a scene (§4.1 reserved fields)."""
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)
    doc[key] = relative_path
    doc["updated_at"] = _now()
    db.write_doc(db.scene_path(scene_id), doc)
