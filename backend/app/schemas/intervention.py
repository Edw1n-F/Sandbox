"""Pydantic schemas for interventions (技术文档 §3.3.2, §4.2.3).

One normalised model serves both user-drawn and AI-proposed features, so both
travel the same validate -> store -> render pipeline.

ADR-030 (AI / deterministic boundary): an AI-originated feature is always
created ``status="draft"``, and the engine refuses to compute on non-validated
geometry.  ``InterventionIn`` refuses to let a caller assert ``status`` at all --
only the service assigns it.
"""

from typing import Any

from pydantic import Field, field_validator

from app.schemas.scene import (
    GeometryType,
    InterventionSource,
    InterventionStatus,
    WireModel,
)

_GEOMETRY_TYPES = {"Point", "LineString", "Polygon"}


def _flatten(coords: Any) -> list[tuple[float, float]]:
    """Yield every (lon, lat) position in an arbitrarily nested coordinate list."""
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(c, (int, float)) for c in coords[:2])
    ):
        return [(float(coords[0]), float(coords[1]))]
    out: list[tuple[float, float]] = []
    if isinstance(coords, (list, tuple)):
        for item in coords:
            out.extend(_flatten(item))
    return out


class Style(WireModel):
    """``style`` JSONB (§4.2.3): {color, opacity, extrusionHeight}."""

    color: str | None = None
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    # Discrete features may extrude; continuous fields never do (ADR-032).
    extrusion_height: float | None = None

    model_config = {
        "alias_generator": __import__(
            "pydantic.alias_generators", fromlist=["to_camel"]
        ).to_camel,
        "populate_by_name": True,
        "extra": "allow",
    }


class InterventionIn(WireModel):
    """A feature as supplied on create/update (§3.3.3).

    ``status``/``id``/timestamps are absent by design -- the service owns them.
    """

    geometry: dict[str, Any]
    intervention_type: str = Field(min_length=1, max_length=100)
    source: InterventionSource = "manual"
    params: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry")
    @classmethod
    def _check_geometry(cls, v: dict[str, Any]) -> dict[str, Any]:
        gtype = v.get("type")
        if gtype not in _GEOMETRY_TYPES:
            raise ValueError(
                f"geometry.type must be one of {sorted(_GEOMETRY_TYPES)}, got {gtype!r}"
            )
        if "coordinates" not in v:
            raise ValueError("geometry has no coordinates")

        positions = _flatten(v["coordinates"])
        if not positions:
            raise ValueError("geometry has no positions")

        # §4.4 / CLAUDE.md: EPSG:4326 everywhere.  Reject anything that cannot
        # be a lon/lat pair rather than silently swapping axes.
        for lon, lat in positions:
            if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
                raise ValueError(
                    f"coordinate out of EPSG:4326 range: [{lon}, {lat}]"
                )

        if gtype == "Polygon":
            ring = (v.get("coordinates") or [[]])[0]
            if len(ring) < 4:
                raise ValueError("polygon ring needs 4 positions (3 + closure)")
        return v

    @property
    def geometry_type(self) -> GeometryType:
        return self.geometry["type"]  # type: ignore[return-value]


class InterventionFeature(WireModel):
    """One feature inside the GeoJSON FeatureCollection (§3.3.2)."""

    type: str = "Feature"
    properties: "InterventionProperties"
    geometry: dict[str, Any]
    id: str | None = None


class InterventionProperties(WireModel):
    """``properties`` of a returned feature.

    §3.3.2's example shows only ``interventionType`` and ``style``; ``id``,
    ``status``, ``source`` and ``params`` are additive so the frontend can key,
    filter and style without a second request.
    """

    intervention_type: str
    style: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    source: InterventionSource = "manual"
    status: InterventionStatus = "draft"
    params: dict[str, Any] = Field(default_factory=dict)


class InterventionFeatureCollection(WireModel):
    """GET /api/plans/{planId}/interventions 200 body (§3.3.2).

    A plain GeoJSON FeatureCollection -- handed straight to Cesium's
    GeoJsonDataSource with no conversion.  ``features: []`` is a valid answer
    and the frontend must still render.
    """

    type: str = "FeatureCollection"
    features: list[InterventionFeature] = Field(default_factory=list)


InterventionFeature.model_rebuild()


class InterventionCreateResponse(WireModel):
    """201 body for POST /api/scenes/{sceneId}/plans (§3.3.3)."""

    plan_id: str
    scene_id: str
    intervention_count: int = 0
