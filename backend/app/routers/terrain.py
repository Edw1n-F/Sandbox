from fastapi import APIRouter

router = APIRouter(tags=["terrain"])


@router.get("/scenes/{scene_id}/terrain", status_code=501)
def get_terrain(scene_id: str):
    pass