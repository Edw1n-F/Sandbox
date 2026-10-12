"""Terrain / imagery metadata route (§3.4).

Returns the opaque ``terrainUrl`` / ``imageryUrl`` references plus the bbox, so
the frontend can build its providers without knowing where the data lives.  It
also carries the resolved overlay URLs and a terrain hint, which the spec's
three-field example omits but the viewer needs (documented in
docs/ARCHITECTURE.md).
"""

from fastapi import APIRouter

from app.schemas.scene import TerrainRead
from app.services import scene_service

router = APIRouter(tags=["terrain"])


@router.get("/scenes/{scene_id}/terrain")
def get_terrain(scene_id: str) -> TerrainRead:
    return scene_service.get_terrain(scene_id)
