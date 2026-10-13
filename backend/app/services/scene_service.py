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
    InterventionIn,
    InterventionProperties,
)
from app.schemas.plan import PlanCreate, PlanListRead, PlanRead, PlanUpdate
from app.schemas.scene import (
    SceneCreate,
    SceneCreated,
    SceneRead,
    SceneUpdate,
    TerrainRead,
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
    read.city_model_url = _resolve_overlay_url(doc, "city_model_path")
    read.city_model_meta = _city_model_meta()
    read.city_model_3d_url = _resolve_model_3d_url(doc, "city_model_3d_path")
    return read


def _resolve_overlay_url(doc: dict[str, Any], key: str) -> str | None:
    """Turn a stored overlay path into a URL, or ``None`` if the file is absent.

    Existence is checked rather than assumed: advertising a wet-grid URL whose
    PNG was never generated produces a broken layer in the viewer, which is
    worse than reporting the layer as absent.
    """
    rel = doc.get(key)
    if not rel:
        return None
    absolute = settings.data_dir / rel
    if not absolute.exists():
        return None
    return settings.public_url(rel)


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


def create_plan(scene_id: str, payload: PlanCreate) -> InterventionCreateResponse:
    if db.read_doc(db.scene_path(scene_id)) is None:
        raise SceneNotFound(scene_id)

    settings.ensure_dirs()
    plan_id = db.new_id()
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


def get_plan(plan_id: str) -> PlanRead:
    doc = db.read_doc(db.plan_path(plan_id))
    if doc is None:
        raise PlanNotFound(plan_id)
    return PlanRead.from_row(doc)


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
            ),
        )
        for item in doc.get("interventions", [])
    ]
    return InterventionFeatureCollection(features=features)


# --------------------------------------------------------------------------- #
# Terrain
# --------------------------------------------------------------------------- #


def get_terrain(scene_id: str) -> TerrainRead:
    """GET /api/scenes/{sceneId}/terrain (§3.4)."""
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        raise SceneNotFound(scene_id)

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
        city_model_url=_resolve_overlay_url(doc, "city_model_path"),
        city_model_meta=_city_model_meta(),
        city_model_3d_url=_resolve_model_3d_url(doc, "city_model_3d_path"),
        terrain_3d_url=_resolve_model_3d_url(doc, "terrain_3d_path"),
        waterbody_url=_resolve_overlay_url(doc, "waterbody_path"),
        infrastructure_url=_resolve_overlay_url(doc, "infrastructure_path"),
    )


def city_model_path(scene_id: str) -> Path | None:
    """Absolute path to a scene's massing model, or None if it has no file.

    Distinct from the URL helper on purpose. The viewer wants a URL; a service
    that has to read the GeoJSON wants the file, and handing it a "/static/..."
    string would be a small lie that only fails later.
    """
    doc = db.read_doc(db.scene_path(scene_id))
    if doc is None:
        return None
    rel = doc.get("city_model_path")
    if not rel:
        return None
    absolute = settings.data_dir / rel
    return absolute if absolute.exists() else None


def _city_model_meta() -> dict[str, Any] | None:
    """Provenance for the 3D massing model, or None when it has not been built.

    The status string is deliberately blunt. These are massing envelopes derived
    from land cover, with an assumed height distribution -- not surveyed
    buildings -- and the viewer shows this rather than presenting a skyline as
    if it were measured.
    """
    path = settings.rasters_dir / "citymodel_meta.json"
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
