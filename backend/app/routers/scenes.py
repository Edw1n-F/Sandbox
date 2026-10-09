"""Scene CRUD routes (§3.2, §3.3.1, §3.3.3).

Status codes are the spec's: 201 create, 200 read/update, 204 delete, 404 with
``{"detail": "scene not found"}`` plus the unified ``error`` envelope.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from app.schemas.intervention import InterventionCreateResponse
from app.schemas.plan import (
    AIPlanRequest,
    AIPlanResponse,
    PlanCreate,
    PlanListRead,
)
from app.schemas.scene import SceneCreate, SceneCreated, SceneRead, SceneUpdate
from app.services import scene_service
from app.services.massing_fit import height_options
from app.services.sandbox_model import DEFAULT_RAINFALL_MM, sea_level_report

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
    grounded in the area's own terrain, land cover and flood model, then stored
    through the same path as a hand-drawn feature: `source="ai"`, every feature
    `status="draft"` (ADR-030), rendered by the same intervention layer.

    It never invents geometry. If the analysis has not been run there is nothing
    legitimate to site on, and the endpoint says so rather than guessing.
    """
    from app.services.ai_plan import PlanGenerationError, generate_plan

    try:
        result = generate_plan(
            scene_id,
            payload.instruction,
            prefer=payload.provider,
            max_features=payload.max_features,
        )
    except PlanGenerationError as exc:
        # 422 rather than 500: the request was well-formed, the machine simply
        # cannot honour it, and the client needs to know which.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AIPlanResponse.model_validate({
        "planId": result.get("planId"),
        "features": result.get("features", 0),
        "provider": result["provider"],
        "types": result.get("types", []),
        "groundingNotes": result.get("groundingNotes", []),
        "message": result.get("message"),
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

    model = scene_service.city_model_path(scene_id)
    if not model or not Path(model).exists():
        raise HTTPException(
            status_code=422,
            detail="this scene has no city model; run scripts/make_citymodel.py",
        )

    features = json.loads(Path(model).read_text()).get("features", [])
    feature = next(
        (f for f in features
         if str(f.get("properties", {}).get("id")) == building_id), None,
    )
    if feature is None:
        raise HTTPException(status_code=404, detail="building not found in this scene")

    ring = feature["geometry"]["coordinates"][0]
    district = [
        (sum(p[0] for p in f["geometry"]["coordinates"][0][:-1]) / max(1, len(ring) - 1),
         sum(p[1] for p in f["geometry"]["coordinates"][0][:-1]) / max(1, len(ring) - 1),
         float(f["properties"].get("height_m") or 0.0))
        for f in features
        for ring in [f["geometry"]["coordinates"][0]]
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
