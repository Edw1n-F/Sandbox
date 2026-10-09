"""Scene CRUD routes (§3.2, §3.3.1, §3.3.3).

Status codes are the spec's: 201 create, 200 read/update, 204 delete, 404 with
``{"detail": "scene not found"}`` plus the unified ``error`` envelope.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from app.schemas.intervention import InterventionCreateResponse
from app.schemas.plan import (
    AIPlanRequest,
    AIPlanResponse,
    PlanCreate,
    PlanListRead,
)
from app.schemas.scene import SceneCreate, SceneCreated, SceneRead, SceneUpdate
from app.services import scene_service
from app.services.massing_fit import height_options
from app.services.sandbox_model import DEFAULT_RAINFALL_MM

router = APIRouter(tags=["scenes"])


@router.post("/scenes", status_code=status.HTTP_201_CREATED)
def create_scene(payload: SceneCreate) -> SceneCreated:
    return scene_service.create_scene(payload)


@router.get("/scenes/{scene_id}")
def get_scene(scene_id: str) -> SceneRead:
    return scene_service.get_scene(scene_id)


@router.put("/scenes/{scene_id}")
def update_scene(scene_id: str, payload: SceneUpdate) -> SceneRead:
    return scene_service.update_scene(scene_id, payload)


@router.delete("/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scene(scene_id: str) -> Response:
    scene_service.delete_scene(scene_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/scenes/{scene_id}/plans")
def list_plans(scene_id: str) -> PlanListRead:
    return scene_service.list_plans(scene_id)


@router.post(
    "/scenes/{scene_id}/plans", status_code=status.HTTP_201_CREATED
)
def create_plan(scene_id: str, payload: PlanCreate) -> InterventionCreateResponse:
    return scene_service.create_plan(scene_id, payload)


@router.post("/scenes/{scene_id}/ai-plan")
def create_ai_plan(scene_id: str, payload: AIPlanRequest) -> AIPlanResponse:
    """POST /api/scenes/{sceneId}/ai-plan -- natural language to a draft plan.

    The GenAI entry point. The instruction is turned into intervention features
    grounded in the area's own terrain, land cover and flood model, then stored
    through the same path as a hand-drawn feature: `source="ai"`, every feature
    `status="draft"` (ADR-030), rendered by the same intervention layer.

    It never invents geometry. If the analysis has not been run there is nothing
    legitimate to site on, and the endpoint says so rather than guessing.
    """
    from app.services.ai_plan import PlanGenerationError, generate_plan

    try:
        result = generate_plan(
            scene_id,
            payload.instruction,
            prefer=payload.provider,
            max_features=payload.max_features,
        )
    except PlanGenerationError as exc:
        # 422 rather than 500: the request was well-formed, the machine simply
        # cannot honour it, and the client needs to know which.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AIPlanResponse.model_validate({
        "planId": result.get("planId"),
        "features": result.get("features", 0),
        "provider": result["provider"],
        "types": result.get("types", []),
        "groundingNotes": result.get("groundingNotes", []),
        "message": result.get("message"),
    })


@router.get("/scenes/{scene_id}/buildings/{building_id}/fit")
def get_building_fit(
    scene_id: str,
    building_id: str,
    objective: str = "balanced",
    rainfall_mm: float | None = None,
    no_net_green_loss: bool = True,
    min_green_share_100m: float = 0.0,
    allow_height_change: bool = False,
    min_height_m: float = 10.0,
    max_height_m: float = 120.0,
) -> dict:
    """GET /api/scenes/{sceneId}/buildings/{buildingId}/fit

    The context of one building, the candidate changes worth considering, and
    which one wins -- every delta produced by re-running the project's own storage
    ledger over a modified land-class grid.

    The caller states an objective and constraints. It never states a number to
    apply: this endpoint computes what the change would do, and applying it stays
    the existing `MassingOps` path in the viewer. That split is the whole design
    (see app/services/massing_fit.py) -- the language model says what it wants,
    the ledger says what it costs.
    """
    from app.services.massing_fit import OBJECTIVES, score_candidates
    from app.services.sandbox_model import context_for, load_grids

    scene = scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "no analysis on disk: run scripts/fetch_dem.py, "
                "scripts/fetch_landcover.py and scripts/make_wetgrid.py first"
            ),
        )

    model = scene_service.city_model_path(scene_id)
    if not model or not Path(model).exists():
        raise HTTPException(
            status_code=422,
            detail="this scene has no city model; run scripts/make_citymodel.py",
        )

    features = json.loads(Path(model).read_text()).get("features", [])
    feature = next(
        (f for f in features
         if str(f.get("properties", {}).get("id")) == building_id), None,
    )
    if feature is None:
        raise HTTPException(status_code=404, detail="building not found in this scene")

    ring = feature["geometry"]["coordinates"][0]
    district = [
        (sum(p[0] for p in f["geometry"]["coordinates"][0][:-1]) / max(1, len(ring) - 1),
         sum(p[1] for p in f["geometry"]["coordinates"][0][:-1]) / max(1, len(ring) - 1),
         float(f["properties"].get("height_m") or 0.0))
        for f in features
        for ring in [f["geometry"]["coordinates"][0]]
    ]

    ctx = context_for(grids, building_id, ring, feature.get("properties", {}), district)

    constraints = {
        "no_net_green_loss": no_net_green_loss,
        "min_green_share_100m": min_green_share_100m,
        "allow_height_change": allow_height_change,
        "min_height_m": min_height_m,
        "max_height_m": max_height_m,
    }
    result = score_candidates(
        grids, ctx, ring,
        objective=objective if objective in OBJECTIVES else "balanced",
        constraints=constraints,
        rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    result["context"] = ctx.to_dict()
    result["heightOptions"] = height_options(ctx, constraints)
    result["objectives"] = sorted(OBJECTIVES)
    result["sceneId"] = scene_id
    result["storageLedger"] = {
        "runoffM3": round(grids.runoff_m3, 1),
        "unstoredM3": round(grids.unstored_m3, 1),
        "wetAreaHa": grids.wet_area_ha,
        "waterLevelM": round(grids.water_level_m, 2),
        "terrainSource": grids.terrain_source,
        "landCoverSource": grids.landcover_source,
    }
    return result
