"""Scene CRUD routes (§3.2, §3.3.1, §3.3.3).

Status codes are the spec's: 201 create, 200 read/update, 204 delete, 404 with
``{"detail": "scene not found"}`` plus the unified ``error`` envelope.
"""

import json
import math
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
from app.config import settings
from app.services import scene_service
from app.services.massing_fit import height_options
from app.services.sandbox_model import DEFAULT_RAINFALL_MM, load_grids, sea_level_report

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

    model = scene_service.city_model_path(scene_id)
    if not model:
        raise HTTPException(status_code=422,
                            detail="this scene has no city model; run scripts/make_citymodel.py")

    doc = json.loads(model.read_text())
    features = doc.get("features", [])
    if not features:
        raise HTTPException(status_code=422, detail="the city model has no features")

    lon0, lat0, lon1, lat1 = _study_bounds()
    site_area = abs((lon1 - lon0) * 111_320.0
                    * math.cos(math.radians((lat0 + lat1) / 2))
                    * (lat1 - lat0) * 110_540.0)

    footprints = []
    for f in features:
        ring = f["geometry"]["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
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
    return result


def _study_bounds() -> tuple[float, float, float, float]:
    """The locked study-area bounding box, from the terrain sidecar."""
    meta = json.loads(
        (settings.rasters_dir / "heatmap_hsk.json").read_text())
    bounds = meta["inputs"]["bounds"]
    return bounds[0], bounds[1], bounds[2], bounds[3]


@router.get("/scenes/{scene_id}/design-metrics")
def get_design_metrics(scene_id: str, day_of_year: int = 355,
                       hours: str = "9,14") -> dict:
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

    model = scene_service.city_model_path(scene_id)
    if not model:
        raise HTTPException(
            status_code=422,
            detail="this scene has no city model; run scripts/make_citymodel.py",
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
    from app.services.scenario import MassingOp, evaluate

    scene_service.get_scene(scene_id)
    grids = load_grids()
    if grids is None:
        raise HTTPException(
            status_code=422,
            detail=("no analysis on disk: run scripts/fetch_dem.py, "
                    "scripts/fetch_landcover.py and scripts/make_wetgrid.py"))

    model = scene_service.city_model_path(scene_id)
    if not model:
        raise HTTPException(status_code=422,
                            detail="this scene has no city model; run scripts/make_citymodel.py")
    features = json.loads(model.read_text()).get("features", [])

    raw_ops = body.get("massingOps") or []
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
            ))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

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
