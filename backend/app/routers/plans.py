from fastapi import APIRouter

router = APIRouter(tags=["plans"])


@router.put("/plans/{plan_id}", status_code=501)
def update_plan(plan_id: str):
    pass


@router.get("/plans/{plan_id}/interventions", status_code=501)
def list_interventions(plan_id: str):
    pass