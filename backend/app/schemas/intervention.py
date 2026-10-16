"""Pydantic schemas for interventions (技术文档 §3.3.2, §4.2.3).

One normalised model serves both user-drawn and AI-proposed features, so both
travel the same validate -> store -> render pipeline.

ADR-030 (AI / deterministic boundary): an AI-originated feature is always
created ``status="draft"``, and the engine refuses to compute on non-validated
geometry.  ``InterventionIn`` refuses to let a caller assert ``status`` at all --
only the service assigns it.
"""

from typing import Any

from pydantic import ConfigDict, Field, field_validator

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

    ``pinned`` is additive for the same reason, and it is carried here because
    the pin state lives at the *top level* of the stored row (see
    ``app/services/pins.py``) and every reader of a plan goes through this
    collection -- dropping it left the run-composition fallback chain in
    ``routers/scenes.py::intervention_features`` landing on its ``True``
    default, so an unpinned row kept being scored. The default stays ``True``:
    rows created before pinning existed carry no flag, and they must compose
    exactly as they did then.
    """

    intervention_type: str
    style: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    source: InterventionSource = "manual"
    status: InterventionStatus = "draft"
    params: dict[str, Any] = Field(default_factory=dict)
    pinned: bool = True


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


# --------------------------------------------------------------------------- #
# The proposal wire shape (design §2.2, additive)
# --------------------------------------------------------------------------- #
#
# Four additive blocks turn a proposal from "a geometry and a rationale" into
# "a geometry, the size it actually covers, the raw ask it came from, the
# constraints it was checked against, and the parameters the run will read".
# Every field carries a default so a client written before this contract (and a
# stored feature that predates it) keeps parsing unchanged -- the design's
# additive-only rule.


class ProposalSize(WireModel):
    """The **served** size of a proposal (§2.2).

    ``cells`` is the rasterised cell count of the served geometry (holes
    subtracted), ``areaHa`` the shoelace area of that geometry, and
    ``coverageFraction`` its share of the 17,316-cell study area.
    """

    cells: int = 0
    area_ha: float = 0.0
    coverage_fraction: float = 0.0


class ProposalRequested(WireModel):
    """The model's raw centre + size ask, echoed verbatim (§2.2).

    ``extra="allow"`` on purpose: this block is provenance, so a key the model
    sent that this schema does not name is preserved rather than dropped. It is
    never used to derive geometry after validation -- ``geometry`` is what the
    server produced from it.
    """

    model_config = ConfigDict(extra="allow")

    centre: dict[str, Any] | None = None
    size_cells: int | None = None


class ParameterEntry(WireModel):
    """One parameter the run reads for a kind, with its honesty label (§2.2).

    ``status`` is the assumption label (e.g. ``ASSUMPTION``) and ``source``
    names where the number came from, so a proposal never carries a parameter
    that cannot say what it is.

    ``confidence`` says how well the number is *known* (a provenance enum, not
    a score): ``ASSUMED`` for a class default and for a planner's hand-set
    value alike, because a planner's estimate is not more knowable than a
    class default.

    When a planner sets the value by hand (Phase 2 R17), ``status`` becomes
    ``USER_ASSUMPTION``, ``edited`` becomes true, and ``defaultValue`` carries
    the class default that was replaced -- mandatory, because without it the
    card cannot show what was overridden and the served value becomes
    unrecoverable from the record.
    """

    value: float
    unit: str = ""
    status: str = "ASSUMPTION"
    source: str = ""
    date: str | None = None
    confidence: str = "ASSUMED"
    default_value: float | None = None
    edited: bool = False


class ProposalQuestion(WireModel):
    """One assumption a proposal rests on, and the planner's answer (Phase 2).

    ``scope`` discriminates the two kinds: ``PROPOSAL`` rows are properties of
    one proposal (a reservoir and a paving slab have different footprints and
    depths), and travel on ``InterventionProposal.questions``; ``SITE`` rows
    are properties of the site and travel once per batch on
    ``AIPlanResponse.questions``.

    ``answer`` is what the planner typed. ``answered_value`` is the
    **server's** parse of it, and is ``null`` when the answer is not numeric --
    a client that sends a mismatched pair gets the server's number back and can
    see the disagreement. The server's parse is the one that reaches ``params``.
    """

    id: str
    parameter: str
    scope: str = "PROPOSAL"
    prompt: str = ""
    unit: str = ""
    value: Any = None
    answer: str | None = None
    answered_value: float | None = None
    status: str = "OPEN"


class AnsweredQuestion(WireModel):
    """A planner's answer, echoed back on the next request (Phase 2 R13).

    Matched by ``id``, which is derived from ``parameter`` alone, so the match
    needs no server state, no sequence number and no timestamp.
    """

    id: str
    parameter: str = ""
    answer: str = ""


class ProposalConstraints(WireModel):
    """The constraint report carried by every proposal (§2.2).

    All eleven fields have defaults: a rejection travels in
    ``AIPlanResponse.rejected[]`` and a client that omits this block still
    parses.
    """

    buildings_clear: bool = True
    building_overlap_cells: int = 0
    building_ids: list[str] = Field(default_factory=list)
    overlap_cells: int = 0
    overlaps_with: list[str] = Field(default_factory=list)
    clipped_cells: int = 0
    clipped_fraction: float = 0.0
    within_single_cap: bool = True
    within_cumulative_cap: bool = True
    effect: str = "class_change"
    effect_note: str = ""


class ProposalRejection(WireModel):
    """One refused proposal, in ``AIPlanResponse.rejected[]`` (§2.2).

    A refusal is a first-class object, not an error: it carries the closed
    ``reasonCode`` so the UI can group refusals without parsing English, and a
    non-empty ``warning`` that names the predicate in words.
    """

    intervention_type: str
    site_id: str | None = None
    requested: ProposalRequested | None = None
    reason_code: str
    building_overlap_cells: int = 0
    overlap_cells: int = 0
    status: str = "PROPOSAL_REJECTED"
    warning: str = ""


class InterventionProposal(InterventionIn):
    """One AI-proposed feature, **not yet persisted** (plan v2 §5).

    The confirm gate (§5.1) presents these to the user; the client echoes the
    accepted subset back to `POST /api/scenes/{sceneId}/ai-plan/apply` which
    writes them through the normal plan-creation path.

    Exposes `site_id`/`rationale`/`basis` as first-class fields so the UI can
    render them without rummaging in `params`. Each is derived at construction
    from the feature's `params` to keep a single source of truth: if `params`
    is round-tripped untouched the derived view and the stored view stay in
    sync. Values can be overridden on the wire, but a client that does so is
    telling the server to trust its judgement -- the service only reads these
    as rendering hints, never as grounding.

    The four blocks below are the additive part of the design §2.2: the size
    the proposal actually covers, the raw ask it came from, the constraints it
    was checked against, and the parameters the run will read. ``status`` here
    is the **proposal** honesty label (UPPER_SNAKE), distinct from the stored
    intervention's ``draft``/``validated`` status.
    """

    site_id: str | None = None
    rationale: str | None = None
    basis: str | None = None
    size: ProposalSize = Field(default_factory=ProposalSize)
    requested: ProposalRequested | None = None
    constraints: ProposalConstraints = Field(default_factory=ProposalConstraints)
    parameters: dict[str, ParameterEntry] = Field(default_factory=dict)
    questions: list[ProposalQuestion] = Field(default_factory=list)
    status: str = "PROPOSAL_ACCEPTED"
    warning: str = ""

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> "InterventionProposal":
        """Lift a provider's feature dict into a proposal.

        Mirrors `_feature()` in `services/ai_plan.py`: `basis`/`rationale`
        live in `params`; `siteId` is set by massing features (§4.1) and is
        None otherwise.

        The additive §2.2 blocks are read if present and left at their
        defaults otherwise, so a feature dict produced before the constraint
        engine existed still lifts cleanly.
        """
        params = feature.get("params") or {}
        requested = feature.get("requested")
        if not isinstance(requested, dict):
            requested = None
        return cls(
            geometry=feature["geometry"],
            intervention_type=feature["intervention_type"],
            source=feature.get("source", "ai"),
            params=params,
            style=feature.get("style", {}),
            site_id=feature.get("site_id") or params.get("siteId"),
            rationale=feature.get("rationale") or params.get("rationale"),
            basis=feature.get("basis") or params.get("basis"),
            size=feature.get("size") or {},
            requested=requested,
            constraints=feature.get("constraints") or {},
            parameters=feature.get("parameters") or {},
            questions=feature.get("questions") or [],
            status=feature.get("status") or "PROPOSAL_ACCEPTED",
            warning=feature.get("warning") or "",
        )


class InterventionProposalList(WireModel):
    """`POST /api/scenes/{sceneId}/ai-plan` body when `apply=false`.

    Also the request body for `/ai-plan/apply`: the client echoes back the
    subset the user accepted, so proposals round-trip through the client
    without any server-held list between the two calls (plan v2 §5.1 keeps
    the proposal state out of the store).
    """

    proposals: list[InterventionProposal] = Field(default_factory=list)
