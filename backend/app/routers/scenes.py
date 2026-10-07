"""Scene CRUD routes (§3.2, §3.3.1, §3.3.3).

Status codes are the spec's: 201 create, 200 read/update, 204 delete, 404 with
``{"detail": "scene not found"}`` plus the unified ``error`` envelope.
"""

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
