"""Scene CRUD routes (§3.2, §3.3.1, §3.3.3).

Status codes are the spec's: 201 create, 200 read/update, 204 delete, 404 with
``{"detail": "scene not found"}`` plus the unified ``error`` envelope.
"""

import json
import math
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Response, status

from app.schemas.intervention import InterventionCreateResponse
from app.schemas.plan import (
    AIPlanApplyRequest,
    AIPlanRequest,
    AIPlanResponse,
    PlanCreate,
    PlanListRead,
)
from app.schemas.intervention import InterventionProposal
from app.schemas.scene import (
    RunRequest,
    SceneCreate,
    SceneCreated,
    SceneRead,
    SceneUpdate,
)
from app.config import settings
from app.services import scene_service
from app.services.design_metrics import (
    HEAT_SUMMER_DAY_OF_YEAR,
    SITE_LAT_DEG,
    SITE_LON_DEG,
    outer_ring,
)
from app.services.massing_fit import height_options
from app.services.sandbox_model import (
    DEFAULT_RAINFALL_MM,
    load_grids,
    routed_ledger,
)

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
    grounded in the area's own terrain, land cover and flood model, then (when
    `apply=true`) stored through the same path as a hand-drawn feature:
    `source="ai"`, every feature `status="draft"` (ADR-030), rendered by the
    same intervention layer.

    When `apply=false` (plan v2 §5 confirm gate), proposals are returned
    **without** being written to the store. The client renders a checklist and
    sends the accepted subset back to `/ai-plan/apply`.

    It never invents geometry. If the analysis has not been run there is nothing
    legitimate to site on, and the endpoint says so rather than guessing.
    """
    from app.services.ai_plan import (
        PlanGenerationError,
        apply_proposals,
        generate_proposals,
    )

    try:
        proposed = generate_proposals(
            scene_id,
            payload.instruction,
            prefer=payload.provider,
            max_features=payload.max_features,
            allowed_types=payload.allowed_types,
        )
    except PlanGenerationError as exc:
        # 422 rather than 500: the request was well-formed, the machine simply
        # cannot honour it, and the client needs to know which.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    features = proposed["features"]
    if not payload.apply:
        proposals = [InterventionProposal.from_feature(f) for f in features]
        return AIPlanResponse.model_validate({
            "planId": None,
            "features": len(proposals),
            "provider": proposed["provider"],
            "types": sorted({f["intervention_type"] for f in features}),
            "groundingNotes": proposed.get("groundingNotes", []),
            "message": proposed.get("message"),
            "proposals": [p.model_dump(by_alias=True) for p in proposals],
            "candidateCounts": proposed.get("candidateCounts", {}),
            "fallbackNote": proposed.get("fallbackNote"),
        })

    if not features:
        return AIPlanResponse.model_validate({
            "planId": None,
            "features": 0,
            "provider": proposed["provider"],
            "groundingNotes": proposed.get("groundingNotes", []),
            "message": proposed.get("message"),
        })

    try:
        applied = apply_proposals(
            scene_id,
            payload.instruction,
            features,
            proposed["provider"],
            proposed.get("groundingNotes", []),
            proposed.get("candidateCounts", {}),
        )
    except PlanGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AIPlanResponse.model_validate({
        "planId": applied.get("planId"),
        "features": applied.get("features", 0),
        "provider": applied["provider"],
        "types": applied.get("types", []),
        "groundingNotes": applied.get("groundingNotes", []),
        "fallbackNote": proposed.get("fallbackNote"),
    })


@router.post("/scenes/{scene_id}/ai-plan/apply")
def apply_ai_plan(scene_id: str, payload: AIPlanApplyRequest) -> AIPlanResponse:
    """POST /api/scenes/{sceneId}/ai-plan/apply -- persist a confirmed subset.

    Second half of the confirm gate (plan v2 §5). Features travel through the
    same `scene_service.create_plan` path as a hand-drawn plan, so the stored
    record is indistinguishable from one made through the legacy single-shot
    endpoint.
    """
    from app.services.ai_plan import PlanGenerationError, apply_proposals

    # Strip the derived display fields: they are read from `params` by the UI,
    # and `InterventionIn` (which PlanCreate validates each item through) is
    # `extra="forbid"`, so leaving them in rejects the whole payload.
    features = [
        p.model_dump(exclude={"site_id", "rationale", "basis"}) for p in payload.proposals
    ]
    try:
        applied = apply_proposals(
            scene_id,
            payload.instruction,
            features,
            payload.provider,
            payload.grounding_notes,
            payload.candidate_counts,
        )
    except PlanGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AIPlanResponse.model_validate({
        "planId": applied.get("planId"),
        "features": applied.get("features", 0),
        "provider": applied["provider"],
        "types": applied.get("types", []),
        "groundingNotes": applied.get("groundingNotes", []),
    })


def request_plan_id(scene_id: str) -> str | None:
    """The most recently created plan for a scene, or None if it has none."""
    listed = scene_service.list_plans(scene_id)
    plans = listed.plans if hasattr(listed, "plans") else listed
    if not plans:
        return None
    ordered = sorted(plans, key=lambda p: str(getattr(p, "created_at", "") or ""))
    return ordered[-1].plan_id


def intervention_features(plan_id: str | None) -> list[dict]:
    """Stored intervention features as plain dicts, ready for the ledger.

    ``list_interventions`` takes a *plan* id and returns a Pydantic collection;
    this unwraps it to the dicts the ledger reads, so the ledger never has to know
    about the wire types.
    """
    if not plan_id:
        return []
    collection = scene_service.list_interventions(plan_id)
    features = getattr(collection, "features", collection)
    out: list[dict] = []
    for f in features or []:
        item = f if isinstance(f, dict) else f.model_dump(by_alias=True)
        # A wire feature wraps the payload in geometry/properties; the ledger wants
        # intervention_type and geometry at the top level, like the stored row.
        props = item.get("properties") or {}
        # The wire is camelCase (`interventionType`); the stored row is
        # snake_case. Both are accepted because both shapes exist in this
        # codebase and guessing one of them wrong returns "unknown" for every
        # intervention -- which is exactly what happened the first time.
        kind = (item.get("interventionType") or item.get("intervention_type")
                or props.get("interventionType") or props.get("intervention_type")
                or item.get("kind") or props.get("kind"))
        out.append({
            "id": item.get("id") or props.get("id"),
            "intervention_type": kind,
            "geometry": item.get("geometry"),
            "params": item.get("params") or props.get("params") or {},
        })
    return out


def intervention_features_many(plan_ids: list[str]) -> list[dict]:
    """Merge several plans' interventions into one scenario's feature list.

    This is what makes a cumulative run possible: the brief's goal is comparing
    combined projects, and the ledger already takes a list, so composing the
    list here is the whole change on the backend.

    Duplicates are dropped by id. Two projects can legitimately share an
    intervention row (an earlier version of the catalog re-used seeded ids), and
    applying the same polygon twice would double its coverage in the guard while
    changing nothing on the grid -- a silent way to trip the cumulative cap with
    one physical feature.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for plan_id in plan_ids:
        for feature in intervention_features(plan_id):
            key = str(feature.get("id"))
            if feature.get("id") and key in seen:
                continue
            if feature.get("id"):
                seen.add(key)
            merged.append(feature)
    return merged


@router.get("/scenes/{scene_id}/buildings/{building_id}/fit")
def get_building_fit(
    scene_id: str,
    building_id: str,
    objective: str = "balanced",
    rainfall_mm: float | None = None,
    no_net_green_loss: bool = True,
    min_green_share_100m: float = 0.0,
    allow_height_change: bool = False,
    min_height_m: float = 10.0,
    max_height_m: float = 120.0,
) -> dict:
    """GET /api/scenes/{sceneId}/buildings/{buildingId}/fit

    The context of one building, the candidate changes worth considering, and
    which one wins -- every delta produced by re-running the project's own storage
    ledger over a modified land-class grid.

    The caller states an objective and constraints. It never states a number to
    apply: this endpoint computes what the change would do, and applying it stays
    the existing `MassingOps` path in the viewer. That split is the whole design
    (see app/services/massing_fit.py) -- the language model says what it wants,
    the ledger says what it costs.
    """
    from app.services.massing_fit import OBJECTIVES, score_candidates
    from app.services.sandbox_model import context_for, load_grids

    scene = scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "no analysis on disk: run scripts/fetch_dem.py, "
                "scripts/fetch_landcover.py and scripts/make_wetgrid.py first"
            ),
        )

    model = scene_service.buildings_path(scene_id)
    if not model or not Path(model).exists():
        raise HTTPException(
            status_code=422,
            detail="this scene has no building footprints; run scripts/build_buildings.py",
        )

    features = json.loads(Path(model).read_text()).get("features", [])
    feature = next(
        (f for f in features
         if str(f.get("properties", {}).get("id")) == building_id), None,
    )
    if feature is None:
        raise HTTPException(status_code=404, detail="building not found in this scene")

    # Both reads go through the project's outer-ring helper: a MultiPolygon's
    # `coordinates[0]` is a list of rings, not a ring, so reading it by hand
    # returns a district centroid computed from ring-shaped "points" and a
    # footprint ring that is not a ring. One feature in the real model is a
    # MultiPolygon (bldg-0104), which is exactly enough to hit this.
    ring = outer_ring(feature.get("geometry"))
    if not ring:
        raise HTTPException(status_code=422,
                            detail="building geometry has no usable outer ring")
    district = [
        (sum(p[0] for p in r[:-1]) / max(1, len(r) - 1),
         sum(p[1] for p in r[:-1]) / max(1, len(r) - 1),
         float(f["properties"].get("height_m") or 0.0))
        for f in features
        for r in [outer_ring(f.get("geometry"))]
        if r
    ]

    ctx = context_for(grids, building_id, ring, feature.get("properties", {}), district)

    constraints = {
        "no_net_green_loss": no_net_green_loss,
        "min_green_share_100m": min_green_share_100m,
        "allow_height_change": allow_height_change,
        "min_height_m": min_height_m,
        "max_height_m": max_height_m,
    }
    result = score_candidates(
        grids, ctx, ring,
        objective=objective if objective in OBJECTIVES else "balanced",
        constraints=constraints,
        rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    result["context"] = ctx.to_dict()
    result["heightOptions"] = height_options(ctx, constraints)
    result["objectives"] = sorted(OBJECTIVES)
    result["sceneId"] = scene_id
    result["storageLedger"] = {
        "runoffM3": round(grids.runoff_m3, 1),
        "unstoredM3": round(grids.unstored_m3, 1),
        "wetAreaHa": grids.wet_area_ha,
        "waterLevelM": round(grids.water_level_m, 2),
        "terrainSource": grids.terrain_source,
        "landCoverSource": grids.landcover_source,
    }
    return result


@router.get("/scenes/{scene_id}/ledger")
def get_ledger(scene_id: str, rainfall_mm: float | None = None,
               plan_id: str | None = None) -> dict:
    """GET /api/scenes/{sceneId}/ledger

    The headline number, and what the stored interventions do to it.

    `bufferDeficitM3` is today's unstored water -- runoff with nowhere to go,
    which is the figure the project's own glossary calls the single headline
    number. `deltaUnstoredM3` is the change the active interventions make to it:
    positive means worse than today, negative means better, under the same
    assumed rainfall.

    Pass `plan_id` to score a specific scenario; the default is the most recently
    created plan. A scenario is a plan, so interventions from other plans are not
    mixed in -- comparing two plans means calling this twice.

    Interventions are translated into the land-class change each actually
    implies and the project's own runoff/retention arithmetic is re-run over the
    modified grid. Two kinds are refused with a stated reason rather than
    approximated: `massing_height` is a 3D operation that changes no land cover,
    and `drainage_alignment` needs pipe and outfall capacity this screening model
    does not have.
    """
    from app.services.sandbox_model import (
        MAX_INTERVENTION_COVERAGE,
        load_grids,
        ledger_for_scenario,
    )

    scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"),
        )

    chosen = plan_id or request_plan_id(scene_id)
    features = intervention_features(chosen)

    result = ledger_for_scenario(
        grids, features,
        rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM,
    )
    result["sceneId"] = scene_id
    result["planId"] = chosen
    result["maxInterventionCoverage"] = MAX_INTERVENTION_COVERAGE
    result["note"] = (
        "Screening figures from a level-pool allocation, not routed hydraulics. "
        "The Curve Number table is the largest single lever on every number here; "
        "run scripts/make_wetgrid.py --sensitivity for the band it implies."
    )
    return result


@router.get("/scenes/{scene_id}/sea-level")
def get_sea_level_scenarios(scene_id: str, rainfall_mm: float | None = None) -> dict:
    """GET /api/scenes/{sceneId}/sea-level

    The brief's task 21: run the same layout with the sea raised by the official
    Hong Kong projections for 2100, and report what changes.

    The numbers come from Hong Kong Observatory's *Mean sea level projection for
    Hong Kong* (revised 21 Aug 2024): 37-82 cm under SSP2-4.5 and 57-108 cm under
    SSP5-8.5, relative to the 1995-2014 mean. The brief's own "+0.4 m / +0.8 m"
    sit inside those ranges rather than replacing them, so both are run.

    **The result is that none of them changes the deficit at this site**, and the
    reason is reported rather than papered over: the study area's ground runs from
    2m to 141 m and the ponds are landward, so no retention surface is submerged.
    The mechanism that would actually bite here is a submerged drainage outfall --
    the jacking effect -- which needs a pipe network this screening model does not
    have.
    """
    from app.services.sandbox_model import load_grids, sea_level_report

    scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"),
        )

    report = sea_level_report(
        grids, rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM)
    report["sceneId"] = scene_id
    return report


@router.get("/scenes/{scene_id}/scenarios")
def get_published_scenarios(scene_id: str, rainfall_mm: float | None = None) -> dict:
    """GET /api/scenes/{sceneId}/scenarios

    Brief task 9: three *real published* development programmes, encoded and run.

    The figures are CEDD's own published works areas -- First Phase about 19.6 ha,
    Second Phase Stage 2 about 200 ha, and all six Second Phase main works
    contracts totalling about 253.5 ha. CEDD publishes no land-use allocation and
    no layout, so the split of that area between bare ground and green, and its
    location on the grid, are this project's assumptions and are returned with the
    result rather than buried.

    `areaShortfallHa` matters: where it is non-zero the district did not have
    enough land of those classes to convert, so the delta is capped and
    understates the real effect. For the Second Phase that shortfall is most of
    the programme.
    """
    from app.services.sandbox_model import (
        apply_published_scenario,
        load_grids,
        load_published_scenarios,
    )

    scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"),
        )

    published = load_published_scenarios()
    if published is None:
        raise HTTPException(status_code=422,
                            detail="data/scenarios/hskht_published.json is missing")

    runs = []
    for scenario in published["scenarios"]:
        outcome = apply_published_scenario(
            grids, scenario,
            rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM,
        )
        if "error" not in outcome:
            runs.append(outcome)

    return {
        "sceneId": scene_id,
        "source": published["source"],
        "status": published["status"],
        "why": published["why"],
        "notPublished": published["notPublished"],
        "milestones": published["milestones"],
        "scenarios": runs,
        "studyAreaHa": round(
            grids.rows * grids.cols * grids.cell_area_m2 / 10_000.0, 1),
        "note": ("Published works areas run through this project's storage "
                 "ledger. A positive delta means worse than today under the "
                 "same rainfall."),
    }


@router.get("/scenes/{scene_id}/ventilation")
def get_ventilation(scene_id: str) -> dict:
    """GET /api/scenes/{sceneId}/ventilation

    Air-ventilation screening by frontal area density, per wind direction.

    **This is not a wind tunnel and not a CFD solve.** It is geometry: the total
    building face presented perpendicular to each wind, over the site area. Low
    means the wind penetrates, high means the site is sheltered from that
    direction. A velocity ratio is a different quantity from a different method,
    and the two are reported side by side rather than mixed.

    The published reference is the AVRG73 air ventilation assessment for Hung
    Shui Kiu Area 13, which states the dominant wind directions for this site are
    NE, ENE and E -- together 40.8% of wind frequency. No frequency is invented
    for any other direction.

    No pass/fail threshold is asserted. The bands live in Technical Circular 1/06
    and its Annex A Technical Guide, which this project has not extracted, and a
    screening that graded itself against thresholds it had not read would be
    exactly the kind of confident wrong answer this project keeps refusing to
    produce.
    """
    from app.services.ventilation import load_reference, ventilation_screening

    scene_service.get_scene(scene_id)

    model = scene_service.buildings_path(scene_id)
    if not model:
        raise HTTPException(status_code=422,
                            detail="this scene has no building footprints; run scripts/build_buildings.py")

    doc = json.loads(model.read_text())
    features = doc.get("features", [])
    if not features:
        raise HTTPException(status_code=422, detail="the city model has no features")

    lon0, lat0, lon1, lat1 = _study_bounds()
    site_area = abs((lon1 - lon0) * 111_320.0
                    * math.cos(math.radians((lat0 + lat1) / 2))
                    * (lat1 - lat0) * 110_540.0)

    def _exterior_points(geom: dict) -> list[tuple[float, float]]:
        """All exterior-ring points, flat, for Polygon or MultiPolygon."""
        coords = geom["coordinates"]
        if geom["type"] == "Polygon":
            rings = coords
        elif geom["type"] == "MultiPolygon":
            rings = [poly[0] for poly in coords]
        else:
            return []
        pts: list[tuple[float, float]] = []
        for ring in rings:
            pts.extend((p[0], p[1]) for p in ring)
        return pts

    footprints = []
    for f in features:
        pts = _exterior_points(f["geometry"])
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mid_lat = (min(ys) + max(ys)) / 2
        footprints.append({
            "widthM": (max(xs) - min(xs)) * 111_320.0 * math.cos(math.radians(mid_lat)),
            "depthM": (max(ys) - min(ys)) * 110_540.0,
            "heightM": float(f["properties"].get("height_m") or 0.0),
        })

    reference = load_reference()
    wind = ((reference or {}).get("windRegime") or {})
    result = ventilation_screening(
        footprints, site_area,
        prevailing=wind.get("prevailing"),
        combined_frequency_percent=wind.get("combinedFrequencyPercent"),
    )
    result["sceneId"] = scene_id
    result["publishedReference"] = {
        "source": (reference or {}).get("source"),
        "definition": (reference or {}).get("definition"),
        "publishedVelocityRatios": (reference or {}).get("publishedVelocityRatios"),
        "effectiveDesignMeasures": (reference or {}).get("effectiveDesignMeasures"),
        "whatThisIsNot": (reference or {}).get("whatThisIsNot"),
    }
    if reference is None:
        result["publishedReference"] = None
        result["note"] += (" No AVRG73 reference file is present, so no wind "
                           "frequencies are applied.")

    # Least-cost corridors and a per-direction FAD ratio, when the analysis is on
    # disk. The corridor needs the *positioned* footprints (the rings carry
    # lon/lat) and the grid, neither of which the dict-based screening above has;
    # when the rasters are absent the existing response is returned unchanged
    # rather than a corridor being guessed. `least_cost_corridors` fails loudly
    # (RuntimeError) when scipy is unavailable -- a degraded corridor is a wrong
    # one, so it is not swallowed here.
    grids = load_grids()
    if grids is not None:
        from app.services.design_metrics import footprints_from_geojson
        from app.services.ventilation import least_cost_corridors

        typed = footprints_from_geojson(features)
        corridors = least_cost_corridors(typed, grids)
        result["corridors"] = corridors
        result["fadRatio"] = _corridor_fad_ratio(
            result["frontalAreaDensity"]["byDirection"], corridors)
    return result


def _corridor_fad_ratio(by_direction: dict, corridors: dict) -> dict:
    """Per-direction corridor-vs-district FAD ratio.

    ``fad_ratio(baseline, scenario, direction)`` wants a baseline and a scenario.
    This endpoint holds one scheme, so the ratio reported is the corridor's own
    mean per-cell FAD (the openest through-route, ``meanCost - 1``) against the
    district's FAD for that direction: a value below 1 means the corridor is more
    open than the district average along that wind. It is a comparison of two
    FADs, never a velocity ratio, and carries ``fad_ratio``'s own status/warning.
    """
    from app.services.ventilation import fad_ratio

    out: dict = {}
    for name, block in (corridors.get("directions") or {}).items():
        baseline = by_direction.get(name)
        if baseline is None:
            continue
        mean_cost = block.get("meanCost")
        corridor_fad = max(0.0, float(mean_cost) - 1.0) if mean_cost is not None else 0.0
        out[name] = fad_ratio(baseline, corridor_fad, name)
    return out


def _study_bounds() -> tuple[float, float, float, float]:
    """The locked study-area bounding box, from the terrain sidecar."""
    meta = json.loads(
        (settings.rasters_dir / "heatmap_hsk.json").read_text())
    bounds = meta["inputs"]["bounds"]
    return bounds[0], bounds[1], bounds[2], bounds[3]


@router.get("/scenes/{scene_id}/design-metrics")
def get_design_metrics(scene_id: str, day_of_year: int = 355,
                       hours: str = "9,14",
                       dayOfYear: int | None = None) -> dict:  # noqa: N803
    """GET /api/scenes/{sceneId}/design-metrics

    What the current massing does to shade and ventilation, measured from the
    geometry, with every metric tied to a rule Hong Kong's own air ventilation
    guidance states.

    **This prints no verdict, and that is the design.** The AVA policy document
    behind Hong Kong's guidance says a higher velocity ratio is the better design
    but that "whether the better design option meets a standard cannot be known".
    A pass/fail here would invent a standard that does not exist. The numbers are
    for comparing this scheme against another one, which is Stage 3.

    Every metric names its source rule. Nothing here is CFD, a comfort index, or
    a daily energy balance, and each caveat says which of those it is not.

    ``dayOfYear`` is accepted as a spelling of ``day_of_year``, and the day is
    range-checked -- see the note on the check below.
    """
    from app.services.design_metrics import design_report, footprints_from_geojson

    scene_service.get_scene(scene_id)

    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"),
        )

    model = scene_service.buildings_path(scene_id)
    if not model:
        raise HTTPException(
            status_code=422,
            detail="this scene has no building footprints; run scripts/build_buildings.py",
        )

    features = json.loads(model.read_text()).get("features", [])
    if not features:
        raise HTTPException(status_code=422, detail="the city model has no features")

    try:
        parsed = [int(h) for h in hours.split(",") if h.strip()]
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"hours must be comma-separated integers, got {hours!r}")
    if not parsed or any(h < 0 or h > 23 for h in parsed):
        raise HTTPException(status_code=422, detail=f"hours out of range: {hours!r}")

    # ``dayOfYear`` is accepted as a spelling of ``day_of_year``, and the day is
    # range-checked. Both were missing here until 9 Oct 2026, and both failed
    # silently rather than loudly: a camelCase caller was served the 355 default
    # without being told their parameter had been ignored, and a day of 999
    # reached ``solar_position`` and returned a plausible-looking sun position for
    # a date that does not exist. Mirrors ``post_scenario_evaluate`` and
    # ``get_heat_field``; the fix is applied to both routes rather than one,
    # because a fix that leaves the same hole next door is how the hole survived.
    if dayOfYear is not None:
        day_of_year = dayOfYear
    try:
        doy = int(day_of_year)
        if not 1 <= doy <= 366:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"dayOfYear must be 1-366, got {day_of_year!r}")
    day_of_year = doy

    report = design_report(
        footprints_from_geojson(features), grids,
        sun_hours=tuple(sorted(set(parsed))), day_of_year=day_of_year,
    )
    report["sceneId"] = scene_id
    report["solarInputs"] = {"dayOfYear": day_of_year, "hoursLocal": sorted(set(parsed))}
    report["source"] = {
        "policy": ("Ng & Cheng, Policies and technical guidelines for urban "
                   "planning of high-density cities - air ventilation assessment "
                   "(AVA) of Hong Kong, Building and Environment 44 (2009) "
                   "1478-1488"),
        "siteSpecific": ("AVRG73 Air Ventilation Assessment for Hung Shui Kiu "
                         "Area 13 (Planning Department / CEDD), for the HSK/HT "
                         "velocity ratios and the prevailing wind directions"),
        "geometry": (f"{len(features)} massing envelopes from the project's own "
                     "seeded generator, on the 5 m land-cover grid"),
    }
    return report


def _parse_massing_ops(raw_ops: object) -> list:
    """Validated ``MassingOp`` objects from a request's ``massingOps`` list.

    Factored out of :func:`post_scenario_evaluate` so the compare endpoint builds
    ops the *identical* way -- an unrecognised op or scope is refused with 422
    rather than silently widened, and there is one construction site, not two.
    """
    from app.services.scenario import MassingOp

    if not isinstance(raw_ops, list):
        raise HTTPException(status_code=422, detail="massingOps must be a list")
    ops: list[MassingOp] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422,
                                detail=f"each massingOp must be an object, got {type(raw).__name__}")
        try:
            ops.append(MassingOp(
                op=str(raw.get("op") or ""),
                multiplier=raw.get("multiplier"),
                height_m=raw.get("heightM", raw.get("height_m")),
                delta_m=raw.get("deltaM", raw.get("delta_m")),
                degrees=raw.get("degrees"),
                dx_m=raw.get("dxM", raw.get("dx_m")),
                dy_m=raw.get("dyM", raw.get("dy_m")),
                scope=raw.get("scope"),
                count=raw.get("count"),
                height_m_new=raw.get("heightMNew", raw.get("height_m_new")),
                target_lon=raw.get("targetLon", raw.get("target_lon")),
                target_lat=raw.get("targetLat", raw.get("target_lat")),
                width_scale=raw.get("widthScale", raw.get("width_scale")),
                depth_scale=raw.get("depthScale", raw.get("depth_scale")),
            ))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    return ops


@router.post("/scenes/{scene_id}/scenario/evaluate")
def post_scenario_evaluate(scene_id: str, body: dict) -> dict:
    """POST /api/scenes/{sceneId}/scenario/evaluate

    Score a candidate scheme against the baseline on **all three axes at once**:
    shade, ventilation and drainage.

    **There is no combined score in the response, and that is the design.** The
    units are incompatible (a fraction of ground, a dimensionless blockage, cubic
    metres), the ventilation axis has no threshold to normalise against, and the
    axes genuinely conflict -- raising heights shades more ground and blocks more
    wind. A single number would settle that trade-off silently. The three axes
    come back separately with their own units, and `tradeoffs` says in words what
    moved.

    Request body, all optional::

        {
          "massingOps": [{"op": "scale_all", "multiplier": 1.4, "scope": "west"}],
          "interventions": [{"kind": "green", "ring": [[lon, lat], ...]}],
          "rainfallMm": 450,
          "sunHours": [9, 14],
          "dayOfYear": 355
        }

    An unrecognised scope is refused with 422 rather than widened to the whole
    district: "make the western blocks taller" answered with an edit everywhere is
    the failure this project has already made once.
    """
    from app.services.scenario import evaluate

    scene_service.get_scene(scene_id)
    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"))

    model = scene_service.buildings_path(scene_id)
    if not model:
        raise HTTPException(status_code=422,
                            detail="this scene has no building footprints; run scripts/build_buildings.py")
    features = json.loads(model.read_text()).get("features", [])

    raw_ops = body.get("massingOps") or []
    ops = _parse_massing_ops(raw_ops)

    raw_iv = body.get("interventions") or []
    if not isinstance(raw_iv, list):
        raise HTTPException(status_code=422, detail="interventions must be a list")

    hours = body.get("sunHours") or [9, 14]
    try:
        parsed_hours = tuple(sorted({int(h) for h in hours}))
        if any(h < 0 or h > 23 for h in parsed_hours):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"sunHours must be hours 0-23, got {hours!r}")
    try:
        doy = int(body.get("dayOfYear", 355))
        if not 1 <= doy <= 366:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"dayOfYear must be 1-366, got {body.get('dayOfYear')!r}")
    rain = body.get("rainfallMm")
    try:
        rain_f = None if rain is None else float(rain)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"rainfallMm must be a number, got {rain!r}")

    try:
        report = evaluate(
            grids, features, ops=ops, interventions=raw_iv, rainfall_mm=rain_f,
            sun_hours=parsed_hours, day_of_year=doy)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if "error" in report:
        raise HTTPException(status_code=422, detail=report["error"])

    report["sceneId"] = scene_id
    return report


def _require_grids():
    """The shared rasters, or the project's standard 422 when they are absent."""
    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"),
        )
    return grids


def _require_buildings(scene_id: str) -> list[dict]:
    """The scene's city-model features, or the standard 422/404 refusal."""
    model = scene_service.buildings_path(scene_id)
    if not model:
        raise HTTPException(
            status_code=422,
            detail="this scene has no building footprints; run scripts/build_buildings.py",
        )
    features = json.loads(Path(model).read_text()).get("features", [])
    if not features:
        raise HTTPException(status_code=422, detail="the city model has no features")
    return features


@router.post("/scenes/{scene_id}/scenario/compare")
def post_scenario_compare(scene_id: str, body: dict) -> dict:
    """POST /api/scenes/{sceneId}/scenario/compare

    Rank several candidate schemes against each other under weights the *caller*
    states, or refuse to rank when none are given.

    Each scenario is evaluated against the **same** baseline city model -- the
    shared-baseline precondition :func:`compare` relies on -- so the numbers being
    ordered are genuinely comparable. The ordering is a weighted favourability
    built from each axis min-max normalised *within the compared set*; it is never
    a score, `combinedScore` stays null, and there is no pass/fail. The three raw
    axes stay visible on every row.

    Request body::

        {
          "scenarios": [
            {"scenarioId": "A", "massingOps": [...], "interventions": [...]},
            {"scenarioId": "B", "massingOps": [...], "interventions": [...]}
          ],
          "weights": {"shade": 0.5, "ventilation": 0.3, "drainage": 0.2}
        }

    ``weights`` may be omitted, in which case the response refuses to order. An
    unknown axis, a negative weight or an all-zero vector is refused with 422, as
    is an unrecognised massing scope -- never widened.
    """
    from app.services.scenario import compare, evaluate

    scene_service.get_scene(scene_id)
    grids = _require_grids()
    features = _require_buildings(scene_id)

    raw_scenarios = body.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise HTTPException(status_code=422,
                            detail="scenarios must be a non-empty list")

    # Optional shared forcing. Every scenario is measured under the same solar
    # instants, day and rainfall, so a difference between them is the scheme's.
    hours = body.get("sunHours") or [9, 14]
    try:
        parsed_hours = tuple(sorted({int(h) for h in hours}))
        if any(h < 0 or h > 23 for h in parsed_hours):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"sunHours must be hours 0-23, got {hours!r}")
    try:
        doy = int(body.get("dayOfYear", 355))
        if not 1 <= doy <= 366:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"dayOfYear must be 1-366, got {body.get('dayOfYear')!r}")
    rain = body.get("rainfallMm")
    try:
        rain_f = None if rain is None else float(rain)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"rainfallMm must be a number, got {rain!r}")

    results: list[dict] = []
    seen_ids: set[str] = set()
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422,
                                detail=f"each scenario must be an object, got {type(raw).__name__}")
        sid = raw.get("scenarioId") or raw.get("id")
        if not sid:
            raise HTTPException(status_code=422, detail="every scenario needs a scenarioId")
        sid = str(sid)
        # compare() keys byAxis/perAxisRank by id, so two scenarios sharing an id
        # would collapse into an ambiguous ordering that still returned 200. Refuse
        # rather than rank something the caller cannot disambiguate.
        if sid in seen_ids:
            raise HTTPException(
                status_code=422,
                detail=f"duplicate scenarioId {sid!r}; scenario ids must be unique")
        seen_ids.add(sid)
        ops = _parse_massing_ops(raw.get("massingOps") or [])
        raw_iv = raw.get("interventions") or []
        if not isinstance(raw_iv, list):
            raise HTTPException(status_code=422, detail="interventions must be a list")
        try:
            report = evaluate(
                grids, features, ops=ops, interventions=raw_iv, rainfall_mm=rain_f,
                sun_hours=parsed_hours, day_of_year=doy)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if "error" in report:
            raise HTTPException(status_code=422, detail=report["error"])
        report["scenarioId"] = sid
        results.append(report)

    try:
        compared = compare(results, weights=body.get("weights"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    compared["sceneId"] = scene_id
    return compared


@router.get("/scenes/{scene_id}/heat-field")
def get_heat_field(scene_id: str, day_of_year: int = HEAT_SUMMER_DAY_OF_YEAR,
                   hours: str = "9,14",
                   dayOfYear: int | None = None) -> dict:  # noqa: N803
    """GET /api/scenes/{sceneId}/heat-field?hours=9,14&day_of_year=196

    The blended heat-exposure field for the current massing: the hand-authored
    land-class index combined with sky view (from the massing geometry) and the
    cast shadow at each requested hour.

    **Heat is not a temperature.** The field is a relative 0-1 screening index,
    never degrees Celsius. It is anchored to a cited air-temperature band (HKO
    Lau Fau Shan station, July 2015-2024) that the 0-1 scale is read against; the
    anchor is a committed file, never fetched at request time. The land-class
    table is mapped through the single definition in ``_overlay_common`` -- not
    copied.

    The default solar day is 196 (15 July) so the shadow geometry matches the
    July anchor band; the design-metrics/shade surface keeps its own 355 default.

    Several hours are collapsed to a per-cell shaded *fraction* (the mean of the
    binary masks), so the shadow term reads as "shaded this fraction of the
    requested instants" rather than one instant's snapshot.

    ``dayOfYear`` is accepted as a spelling of ``day_of_year``: the codebase's
    existing query parameters are snake_case while the design writes camelCase, and
    accepting both means neither caller is silently served the default.
    """
    from app.services.design_metrics import (
        footprints_from_geojson,
        heat_field_report,
        load_heat_calibration,
        rasterise_height,
        shade_mask,
        sky_view_factor_raster,
    )
    from _overlay_common import HEAT_INDEX, LAND_CLASSES

    if dayOfYear is not None:
        day_of_year = dayOfYear
    # The day is range-checked here: a day of 999 would otherwise reach
    # solar_position and produce a plausible-looking sun for a day that does not
    # exist. Mirrors post_scenario_evaluate's dayOfYear check. (get_design_metrics
    # carries the same check as of 9 Oct 2026 -- both routes, not just this one.)
    try:
        doy = int(day_of_year)
        if not 1 <= doy <= 366:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"dayOfYear must be 1-366, got {day_of_year!r}")
    day_of_year = doy

    scene_service.get_scene(scene_id)
    grids = _require_grids()
    features = _require_buildings(scene_id)

    try:
        parsed = [int(h) for h in hours.split(",") if h.strip()]
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"hours must be comma-separated integers, got {hours!r}")
    if not parsed or any(h < 0 or h > 23 for h in parsed):
        raise HTTPException(status_code=422, detail=f"hours out of range: {hours!r}")
    hours_local = sorted(set(parsed))

    cell_m = float(grids.cell_area_m2) ** 0.5
    lut = np.array([HEAT_INDEX[name] for name in LAND_CLASSES], dtype=np.float32)
    land_index = lut[grids.land_class].astype(np.float32)

    fps = footprints_from_geojson(features)
    height = rasterise_height(fps, grids)
    svf = sky_view_factor_raster(height, cell_m)
    masks = [shade_mask(fps, grids, SITE_LAT_DEG, SITE_LON_DEG, day_of_year, h).astype(np.float32)
             for h in hours_local]
    shaded = np.mean(masks, axis=0).astype(np.float32)

    report = heat_field_report(land_index, svf, shaded,
                               calibration=load_heat_calibration(), cell_m=cell_m)
    report["sceneId"] = scene_id
    report["solarInputs"] = {"dayOfYear": day_of_year, "hoursLocal": hours_local}
    return report


@router.get("/scenes/{scene_id}/ventilation/corridors")
def get_ventilation_corridors(scene_id: str, directions: str | None = None) -> dict:
    """GET /api/scenes/{sceneId}/ventilation/corridors?directions=NE,SW

    The least-cost through-route across the frontal-area cost surface, per wind
    direction -- the openest path the wind can take, computed from the geometry.

    ``directions`` defaults to the eight principal directions; the wind-direction
    table is the canonical one in ``design_metrics`` (never re-derived here, which
    is how NE and SW get swapped). The corridor is **geometry, not flow**: it is
    not a measured wind speed and FAD is not a velocity ratio, which the response
    states.
    """
    from app.services.design_metrics import CARDINALS, footprints_from_geojson
    from app.services.ventilation import PRINCIPAL_DIRECTIONS, least_cost_corridors

    scene_service.get_scene(scene_id)
    grids = _require_grids()
    features = _require_buildings(scene_id)

    names = [d.strip().upper() for d in (directions or "").split(",") if d.strip()]
    if not names:
        requested = {name: CARDINALS[name] for name in PRINCIPAL_DIRECTIONS}
    else:
        unknown = [n for n in names if n not in CARDINALS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown wind direction(s) {unknown}; known are {sorted(CARDINALS)}")
        requested = {n: CARDINALS[n] for n in names}

    typed = footprints_from_geojson(features)
    try:
        result = least_cost_corridors(typed, grids, directions=requested)
    except ValueError as e:
        # e.g. a 16-point name (ENE) that has no travel vector in the canonical
        # table -- refused rather than approximated.
        raise HTTPException(status_code=422, detail=str(e)) from e
    result["sceneId"] = scene_id
    result["requestedDirections"] = list(requested)
    return result


@router.get("/scenes/{scene_id}/drainage/routed")
def get_drainage_routed(scene_id: str, rainfall_mm: float | None = None,
                        rainfallMm: float | None = None) -> dict:  # noqa: N803
    """GET /api/scenes/{sceneId}/drainage/routed?rainfall_mm=450

    The fill-and-spill routing of each cell's unstored volume down the D8 receiver
    chain, with the mass-balance identity, the depression statistics and an
    independent HAND cross-check reported beside the routed depth.

    **Not a flood prediction.** Screening-level fill-and-spill on a
    Priority-Flood conditioned surface; the level-pool ledger stays the headline
    number and this is additive. When the receiver and raw surface have not been
    generated the response says ``ROUTING_UNAVAILABLE`` with the command to run,
    rather than failing.

    ``rainfallMm`` is accepted as a spelling of ``rainfall_mm`` for the same reason
    as ``dayOfYear`` above: the design writes camelCase, the rest of this router
    uses snake_case, and both are honoured.
    """
    if rainfallMm is not None:
        rainfall_mm = rainfallMm

    scene_service.get_scene(scene_id)
    grids = _require_grids()

    result = routed_ledger(
        grids, grids.land_class,
        rainfall_mm=rainfall_mm if rainfall_mm else DEFAULT_RAINFALL_MM)
    result["sceneId"] = scene_id
    return result


@router.get("/scenes/{scene_id}/drainage/hourly")
def get_drainage_hourly(
    scene_id: str,
    hyetograph_name: str | None = None,
    hyetographName: str | None = None,  # noqa: N803
    hyetograph: str | None = None,  # comma-separated mm/h, e.g. "10,25,60,120"
    initial_retention_fill: float | None = None,
    initialRetentionFill: float | None = None,  # noqa: N803
    drained_m3_per_hour: float | None = None,
    drainedM3PerHour: str | None = None,  # noqa: N803 - "0" for no drainage
) -> dict:
    """GET /api/scenes/{sceneId}/drainage/hourly?hyetograph_name=hko_2023_09_07

    The hourly storm simulation: rain level and drainage, hour by hour.

    Each hour's rain generates runoff through the project's own CN table, the
    per-cell retention pools fill (a pool filled at hour 2 has less room at
    hour 5), and the system's drainage capability removes standing water up to
    the DSD published design standard for this catchment type (134 mm/h,
    50-year rural, Stormwater Drainage Manual 2024 update) -- or up to a
    caller-supplied rate, with ``drained_m3_per_hour=0`` as the no-drainage
    counterfactual. The whole-simulation mass balance
    (``rain_in == stored_gain + drained + standing_end``) is **enforced**, not
    reported, and the final standing volume is routed once through the D8
    cascade as a view of where it pools.

    **Not a flood prediction.** The drainage capability is a published design
    standard used as a proxy -- no pipe asset data exists for this site and
    none is invented; the response says so every time.

    ``hyetograph`` is a comma-separated list of mm/h ("10,25,60,120");
    ``hyetograph_name`` names one of the cited reference storms
    (``hko_2023_09_07`` | ``dsd_50y_design`` | ``black_rain_70``). camelCase
    spellings of every parameter are accepted as everywhere else in this
    router.
    """
    if hyetographName is not None:
        hyetograph_name = hyetographName
    if initialRetentionFill is not None:
        initial_retention_fill = initialRetentionFill
    if drainedM3PerHour is not None:
        drained_m3_per_hour = float(drainedM3PerHour)

    scene_service.get_scene(scene_id)
    grids = _require_grids()

    hyeto: list[float] | None = None
    if hyetograph is not None:
        try:
            hyeto = [float(v) for v in hyetograph.split(",") if v.strip()]
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="hyetograph must be comma-separated numbers, e.g. '10,25,60'",
            ) from None
        if not hyeto:
            raise HTTPException(status_code=422,
                                detail="hyetograph must contain at least one value")

    from app.services.sandbox_model import hourly_storm_simulation

    result = hourly_storm_simulation(
        grids,
        hyetograph=hyeto,
        hyetograph_name=hyetograph_name,
        initial_retention_fill=(float(initial_retention_fill)
                                if initial_retention_fill is not None else 0.0),
        drained_m3_per_hour=drained_m3_per_hour,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    result["sceneId"] = scene_id
    return result


@router.post("/scenes/{scene_id}/run")
def post_run(scene_id: str, body: RunRequest) -> dict:
    """POST /api/scenes/{sceneId}/run -- one live simulation run.

    The run loop the sliders were always meant to drive (ADR-024 kept them
    local-only until this existed): apply the chosen plan's interventions to
    the land-class grid, re-run the project's own SCS-CN generation/retention
    arithmetic at the requested rainfall, re-solve the level-pool fill, and
    re-render both overlays. The response carries fresh overlay URLs (cache
    busted by run id), the baseline-vs-scenario ledger, and a plain-language
    explanation -- an LLM paragraph when a provider is configured, a labelled
    deterministic template otherwise.

    ``planId: null`` is a baseline run: today's land cover, no interventions.
    Send ``planIds`` to compose several projects into one cumulative scenario --
    the brief's headline goal. The heavy terrain conditioning and the
    sky-view/shade rasters are cached (they change with the offline scripts and
    the massing respectively, never with a slider), so a run is seconds, not
    minutes.

    Screening-level, always: the response's ``status``/``warning`` say so, and
    so do the sidecars written beside the PNGs in ``data/rasters/run/``.
    """
    from app.services import run_service

    # plan_ids wins when both are sent; a single-plan client keeps working
    # unchanged through the same path.
    plan_ids = [p for p in (body.plan_ids or []) if p]
    if not plan_ids and body.plan_id:
        plan_ids = [body.plan_id]

    try:
        return run_service.run_scenario(
            scene_id,
            rainfall_mm=body.rainfall_mm,
            temperature_c=body.temperature_c,
            plan_id=plan_ids[0] if plan_ids else None,
            features=intervention_features_many(plan_ids),
            plan_ids=plan_ids,
            tide_state=body.tide_state,
        )
    except run_service.RunInputsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
