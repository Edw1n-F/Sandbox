"""Storage-layer row definitions for ``scene`` (§4.2.1).

Under ``STORE_MODE=file`` these are TypedDicts, not SQLAlchemy models: they
document the storage contract (snake_case, column-for-column against the DDL)
without pulling in an ORM that has nothing to talk to until PostGIS exists.
When PostGIS lands, each of these becomes the shape of the SQLAlchemy model and
``scene_service`` stops changing.

The ``id`` -> ``sceneId`` rename is deliberately *not* done here.  Storage keeps
``id`` because the DDL says ``id``; the wire says ``sceneId`` because the spec's
JSON examples say ``sceneId``; ``app.schemas`` owns the translation (ADR-027).
"""

from typing import Any, NotRequired, TypedDict


class SceneRow(TypedDict):
    """One row of the ``scene`` table."""

    id: str
    name: str
    center: dict[str, Any]  # GEOMETRY(Point, 4326) NOT NULL
    bbox: NotRequired[dict[str, Any]]  # GEOMETRY(Polygon, 4326)
    terrain_url: NotRequired[str]  # Cesium Terrain / DEM COG address
    imagery_url: NotRequired[str]  # imagery base-map address
    default_camera: NotRequired[dict[str, Any]]  # {longitude, latitude, height, heading, pitch}
    env_params: NotRequired[dict[str, Any]]  # environment defaults (temperature / precipitation)

    # §4.1 reserves these for physics-engine output (wet grid / heatmap rasters).
    # Implemented as real, populated fields rather than placeholders, so the
    # reserved-field claim is testable.
    wet_grid_path: NotRequired[str | None]
    heatmap_path: NotRequired[str | None]
    terrain_format: NotRequired[str | None]
    terrain_source: NotRequired[str | None]

    plan_project_url: NotRequired[str | None]

    created_at: str
    updated_at: str


SCENE_COLUMNS: tuple[str, ...] = tuple(SceneRow.__annotations__)
