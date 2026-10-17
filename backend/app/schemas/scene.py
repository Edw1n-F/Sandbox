"""Pydantic schemas for the scene contract (技术文档 §3.2, §4.2.1).

Contract decisions taken here (docs/ARCHITECTURE.md, ADR-027):

1. **camelCase on the wire, snake_case in the store.**  The spec's own examples
   are camelCase while the DDL is snake_case, so every schema uses an alias
   generator and accepts both spellings on input.
2. **Geometry is pass-through.**  ``center`` / ``bbox`` / ``geom`` stay raw
   GeoJSON dicts and are never camelised or re-projected -- GeoJSON -> PostGIS
   -> Cesium must stay byte-identical (CLAUDE.md principle 2).
3. **``bbox`` is a closed ring.**  §3.2.2's example carries only two
   coordinates, which is not a renderable polygon.  Ingress *repairs* short
   rings; egress always emits an explicitly closed ring, and closure is
   asserted in a validator.
4. **``defaultCamera`` is the union of the two shapes** the spec documents
   (§4.2.1 has longitude/latitude, §3.2.1 does not).  Longitude/latitude are
   optional and default to the scene centre, so both documented spellings work.
"""

from datetime import datetime
from typing import Any, ClassVar, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from app.config import DEFAULT_RAINFALL_MM
from typing_extensions import Self

# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #


class WireModel(BaseModel):
    """camelCase out, snake_case or camelCase in.

    ``extra="forbid"`` is deliberate on *request* bodies: a typo like
    ``terrainURL`` should be a 422, not a silently dropped field.  Response
    models loosen this to ``"ignore"`` (see ``RowModel``).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        ser_json_timedelta="float",
    )

    #: Model field that receives the storage ``id`` column, when the wire spells
    #: it differently.  ``None`` means the model has no such field.
    _id_field: ClassVar[str | None] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Self:
        """Build a response model from a snake_case storage row.

        This is where ADR-027 is actually enforced.  Storage is column-for-column
        with §4.2's DDL, including a primary key named ``id``; the wire is
        camelCase and calls that key ``sceneId`` / ``planId``.  Renaming the
        column would break the documented schema, so the translation happens
        here, once, on the way out.
        """
        data: dict[str, Any] = {
            name: row[name] for name in cls.model_fields if name in row
        }
        if cls._id_field and cls._id_field not in data and row.get("id") is not None:
            data[cls._id_field] = row["id"]
        return cls.model_validate(data)


class RowModel(WireModel):
    """A response model fed from a storage row.

    Unknown storage columns are ignored rather than rejected, so the store can
    grow (``study_area``, ``working_grid``) without every response model needing
    to know about it.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


# --------------------------------------------------------------------------- #
# Shared geometry helpers
# --------------------------------------------------------------------------- #

Point = dict[str, Any]
Polygon = dict[str, Any]


def _ring_of(geometry: dict[str, Any]) -> list[list[float]]:
    """Return the outer ring of a Polygon geometry."""
    coords = geometry.get("coordinates") or []
    if not coords:
        raise ValueError("bbox polygon has no coordinates")
    return coords[0]


def close_ring(geometry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a Polygon geometry whose outer ring is explicitly closed.

    GeoJSON Polygon ``coordinates`` is a list of *rings*, each ring a list of
    positions -- so the outer ring is ``coordinates[0]`` and closure means
    repeating its first position at its end.  Getting that nesting wrong
    compares the ring against itself and silently closes nothing, which is why
    this is a named function with a test rather than an inline expression.
    """
    out = dict(geometry)
    rings = [list(ring) for ring in (out.get("coordinates") or [])]
    if not rings or not rings[0]:
        raise ValueError("bbox polygon has no coordinates")
    outer = rings[0]
    if len(outer) >= 3 and outer[0] != outer[-1]:
        outer.append(list(outer[0]))
    out["coordinates"] = rings
    return out


class DefaultCamera(WireModel):
    """Union of §4.2.1's DDL comment and §3.2.1's API example.

    ``longitude``/``latitude`` are optional; the scene service fills them from
    ``center`` so the frontend always receives a complete camera.
    """

    longitude: float | None = None
    latitude: float | None = None
    height: float = 5_000.0
    heading: float = 0.0
    pitch: float = -45.0


class EnvParams(WireModel):
    """Scene-level *static* environment defaults (ADR-024).

    Per-run user adjustments belong on the run, not on the scene -- moving a
    rainfall slider must not mutate the object every plan hangs off.
    """

    temperature: float | None = None
    precipitation: float | None = None
    # Free-form extras are preserved rather than dropped, because the store
    # column is JSONB and the engine's assumption register is open-ended.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


# --------------------------------------------------------------------------- #
# Scene
# --------------------------------------------------------------------------- #


class SceneBase(WireModel):
    name: str = Field(min_length=1, max_length=200)

    center: Point
    bbox: Polygon | None = None

    terrain_url: str | None = None
    imagery_url: str | None = None

    default_camera: DefaultCamera = Field(default_factory=DefaultCamera)
    env_params: EnvParams = Field(default_factory=EnvParams)

    @field_validator("center")
    @classmethod
    def _check_center(cls, v: Point) -> Point:
        if v.get("type") != "Point":
            raise ValueError("center must be a GeoJSON Point")
        coords = v.get("coordinates") or []
        if len(coords) < 2:
            raise ValueError("center needs [longitude, latitude]")
        lon, lat = float(coords[0]), float(coords[1])
        if not (-180.0 <= lon <= 180.0):
            raise ValueError("center longitude out of range")
        if not (-90.0 <= lat <= 90.0):
            raise ValueError("center latitude out of range")
        return v

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: Polygon | None) -> Polygon | None:
        if v is None:
            return None
        if v.get("type") != "Polygon":
            raise ValueError("bbox must be a GeoJSON Polygon")
        ring = _ring_of(v)
        if len(ring) < 3:
            raise ValueError("bbox polygon needs at least 3 distinct vertices")
        # §3.2.2 ships a 2-coordinate "polygon"; repair rather than reject.
        return close_ring(v)

    @field_validator("imagery_url")
    @classmethod
    def _check_imagery(cls, v: str | None) -> str | None:
        # The spec's example is a {z}/{x}/{y} tile template.  A bare COG/PNG URL
        # is equally valid, so only emptiness is rejected.
        if v is not None and not v.strip():
            raise ValueError("imageryUrl must not be blank")
        return v


class SceneCreate(SceneBase):
    """POST /api/scenes body (§3.2.1)."""


class SceneUpdate(WireModel):
    """PUT /api/scenes/{sceneId} -- a *subset* of create fields (§3.2.3)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    center: Point | None = None
    bbox: Polygon | None = None
    terrain_url: str | None = None
    imagery_url: str | None = None
    default_camera: DefaultCamera | None = None
    env_params: EnvParams | None = None

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: Polygon | None) -> Polygon | None:
        return SceneBase._check_bbox(v)  # type: ignore[attr-defined]


class SceneCreated(WireModel):
    """201 body (§3.2.1)."""

    scene_id: str
    name: str
    created_at: datetime


class SceneRead(SceneBase):
    """200 body (§3.2.2) -- the 3D sandbox entry point."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    _id_field: ClassVar[str | None] = "scene_id"

    scene_id: str
    created_at: datetime
    updated_at: datetime

    # Resolved, store-relative overlay paths published alongside the opaque
    # provider URLs.  §4.1 reserves these as association fields for the physics
    # engine; they exist here so the field is real rather than aspirational.
    wet_grid_url: str | None = None
    heatmap_url: str | None = None
    # 3D massing envelopes, as a GeoJSON FeatureCollection. Served from the same
    # endpoint as the rasters because it is the same question -- "what should the
    # viewer draw" -- and the viewer should not need a fourth request to boot.
    buildings_url: str | None = None
    buildings_meta: dict[str, Any] | None = None
    # Real 3D Tiles tileset (textured meshes), distinct from the editable GeoJSON
    # massing above.
    buildings_3d_url: str | None = None

    @model_validator(mode="after")
    def _fill_camera_from_center(self) -> "SceneRead":
        """Guarantee the camera always reaches the frontend complete."""
        if self.default_camera.longitude is None or self.default_camera.latitude is None:
            lon, lat = self.center["coordinates"][0], self.center["coordinates"][1]
            if self.default_camera.longitude is None:
                self.default_camera.longitude = lon
            if self.default_camera.latitude is None:
                self.default_camera.latitude = lat
        return self


class TerrainRead(RowModel):
    """200 body of GET /api/scenes/{sceneId}/terrain (§3.4)."""

    terrain_url: str | None = None
    imagery_url: str | None = None
    bbox: Polygon | None = None
    center: Point | None = None
    # Not in the spec's three fields, but the frontend cannot build a
    # SingleTileImageryProvider rectangle without it, and re-deriving it from
    # the bbox invites a client-side bug.  Documented in ARCHITECTURE.md.
    terrain_format: Literal["quantized-mesh", "heightmap-tiles", "arcinfo-ascii"] | None = None
    terrain_source: str | None = None
    wet_grid_url: str | None = None
    heatmap_url: str | None = None
    # 3D massing envelopes, as a GeoJSON FeatureCollection. Served from the same
    # endpoint as the rasters because it is the same question -- "what should the
    # viewer draw" -- and the viewer should not need a fourth request to boot.
    buildings_url: str | None = None
    buildings_meta: dict[str, Any] | None = None
    # Real 3D Tiles tileset (textured meshes from 6-NW-12D), distinct from the
    # editable GeoJSON massing above.
    buildings_3d_url: str | None = None
    # Visual terrain mesh from 6-NW-12D terrain(TB) 3D Tiles.
    terrain_3d_url: str | None = None
    waterbody_url: str | None = None
    infrastructure_url: str | None = None
    projects: list["ProjectModel"] = Field(default_factory=list)
    # A seeded project whose model or footprint is not on disk (a build step not
    # run, a checkout without the LFS objects). Additive and defaulted: a client
    # that does not read it is unaffected, and one that does can say *why* the
    # project is absent. Reporting rather than rendering: a missing file is not
    # a project to draw a placeholder for, but a silently shorter `projects`
    # list is indistinguishable from a dataset that never had the project.
    projects_unavailable: list["UnavailableProject"] = Field(default_factory=list)


class UnavailableProject(WireModel):
    """A seeded project the viewer cannot draw, and why.

    Present so an absent model reads as *a missing file* rather than as a
    dataset that never held the project. Both facts are true statements about
    different things, and only one of them is worth acting on.
    """

    project_id: str
    name: str
    #: Which artefact is missing: ``model`` | ``footprint`` | both.
    missing: list[str] = Field(default_factory=list)
    reason: str = ""


class ProjectModel(WireModel):
    """One planned project's 3D presence on a scene.

    A list, not two singular fields. `plan_project_url` and
    `plan_project_footprint_url` described exactly one modeled project on the
    whole scene, so the viewer's model handle and its project id were both
    hardcoded and a second model could not appear at all -- the URLs, not the
    frontend, were the ceiling.

    Built server-side from `data/projects/*.json`, which is the one home for a
    project's non-geometric facts. The geometry and the effects live in the same
    file; this is the subset the viewer needs in order to draw it.
    """

    project_id: str
    name: str
    model_url: str | None = None
    footprint_url: str | None = None
    # Where the model's origin sits and how big to draw it. Must match what
    # `compute_plan_project_footprint.py` used, or the 2.5D boxes hidden beneath
    # the model will not line up with the model itself.
    origin: tuple[float, float] | None = None
    scale: float = 1.0
    label: str | None = None


PlanType = Literal[
    "baseline",
    "economic",
    "environmental",
    "comprehensive",
    "custom",
]
PlanSource = Literal["manual", "ai"]
InterventionSource = Literal["manual", "ai"]
InterventionStatus = Literal["draft", "validated"]
GeometryType = Literal["Point", "LineString", "Polygon"]


class RunRequest(WireModel):
    """POST /api/scenes/{sceneId}/run body -- one live simulation run.

    The per-run inputs ADR-024 refused to persist onto the scene: they belong
    to a run, not to the object every plan hangs off. ``plan_id`` selects the
    scenario; ``None`` is a baseline run (today's land cover, no
    interventions).

    ``plan_ids`` is the cumulative selection: every active project's plan, whose
    interventions are merged into one scenario. It exists because the brief's
    whole point is *cumulative* effect -- "rather than only assessing each
    project individually" -- and a single-plan run cannot express that. When
    both are sent ``plan_ids`` wins and ``plan_id`` is treated as its first
    entry, so a single-project client and a cumulative one share one code path.
    """

    # Defaulted from `DEFAULT_RAINFALL_MM`, not typed again: one design storm
    # for the whole API (see that constant). A caller may still send 450.
    rainfall_mm: float = Field(default=DEFAULT_RAINFALL_MM, ge=0.0, le=800.0)
    temperature_c: float = Field(default=30.0, ge=-10.0, le=50.0)
    # Screening tidal-backwater condition applied to the D8 outfall. One of
    # "low" / "mean" / "high" (see sandbox_model.TIDE_STATES); not a hydraulic
    # outfall model.
    tide_state: str = "low"
    plan_id: str | None = None
    plan_ids: list[str] = Field(default_factory=list)
