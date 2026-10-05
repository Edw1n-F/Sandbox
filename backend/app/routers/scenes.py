from fastapi import APIRouter

router = APIRouter(tags=["scenes"])


@router.post("/scenes", status_code=501)
def create_scene():
    pass


@router.get("/scenes/{scene_id}", status_code=501)
def get_scene(scene_id: str):
    pass


@router.put("/scenes/{scene_id}", status_code=501)
def update_scene(scene_id: str):
    pass


@router.delete("/scenes/{scene_id}", status_code=501)
def delete_scene(scene_id: str):
    pass


@router.get("/scenes/{scene_id}/plans", status_code=501)
def list_plans(scene_id: str):
    pass


@router.post("/scenes/{scene_id}/plans", status_code=501)
def create_plan(scene_id: str):
    pass