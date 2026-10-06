"""Plan routes (§3.3.2, §3.3.4)."""

from fastapi import APIRouter

from app.schemas.intervention import InterventionFeatureCollection
from app.schemas.plan import PlanRead, PlanUpdate
from app.services import scene_service

router = APIRouter(tags=["plans"])


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, payload: PlanUpdate) -> PlanRead:
    return scene_service.update_plan(plan_id, payload)


@router.get("/plans/{plan_id}/interventions")
def list_interventions(plan_id: str) -> InterventionFeatureCollection:
    return scene_service.list_interventions(plan_id)
