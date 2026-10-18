"""`POST /api/scenes/{sceneId}/massing/assess` -- a height sentence, scored.

## Why this exists

"Make the district 25% taller" is a natural thing to ask a planning sandbox,
and the project already scores it: `/scenario/evaluate` re-runs shade,
ventilation and drainage on the scaled massing and reports the three axes
separately. But nothing could reach it from the chat. Sent to `/ai-plan`, the
height edit was handed to the *land* constraint engine, whose building-overlap
predicate refused it -- a height change to existing buildings overlaps buildings
by definition -- and the chat card then told the planner that height was not
modelled at all.

This route connects the sentence to the scorer that already exists. It writes
nothing and places nothing: a height change to the existing district is a
**what-if**, scored and reported, not an intervention added to the sandbox.

## What it never does

It never invents an operation. The sentence is parsed by
`ai_plan.parse_massing_intent`, the same deterministic parser the planner uses;
a sentence it cannot parse is a 422 naming what it can do, not a guess.
It never combines the axes: `/scenario/evaluate` returns `combinedScore: null`
with its reason, and this passes both through unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.config import DEFAULT_RAINFALL_MM
from app.schemas.scene import WireModel

router = APIRouter(tags=["massing"])


class MassingAssessRequest(WireModel):
    """A height instruction, in the planner's own words."""

    instruction: str = Field(min_length=1, max_length=500)
    rainfall_mm: float = Field(default=DEFAULT_RAINFALL_MM, ge=0, le=800)


@router.post("/scenes/{scene_id}/massing/assess")
def assess_massing(scene_id: str, body: MassingAssessRequest) -> dict[str, Any]:
    """Score a height change to the existing buildings. Writes nothing."""
    from app.routers.scenes import _parse_massing_ops
    from app.services import scene_service
    from app.services.ai_plan import parse_massing_intent
    from app.services.sandbox_model import load_grids
    from app.services.scenario import evaluate

    scene_service.get_scene(scene_id)
    raw_ops = parse_massing_intent(body.instruction)
    if not raw_ops:
        raise HTTPException(
            status_code=422,
            detail=("I could not read a height change in that. Try "
                    "'make the district 25% taller' or 'make the west 20% "
                    "shorter'."),
        )
    # An unscoped "make the buildings taller" means the whole district; the
    # evaluator needs that said explicitly rather than inferred from None.
    for op in raw_ops:
        if not op.get("scope"):
            op["scope"] = "all"

    grids = load_grids()
    if grids is None:
        raise HTTPException(status_code=422,
                            detail="no analysis on disk: run scripts/make_wetgrid.py")
    model = scene_service.buildings_path(scene_id)
    if not model:
        raise HTTPException(status_code=422,
                            detail="this scene has no building footprints")
    features = json.loads(model.read_text()).get("features", [])

    try:
        report = evaluate(grids, features, ops=_parse_massing_ops(raw_ops),
                          interventions=[], rainfall_mm=body.rainfall_mm,
                          sun_hours=(9, 14), day_of_year=355)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "error" in report:
        raise HTTPException(status_code=422, detail=report["error"])

    return {
        "sceneId": scene_id,
        "status": "SCREENING_MASSING_WHAT_IF",
        "warning": (
            "A what-if on the existing buildings, scored and not applied: "
            "nothing was added to the sandbox. Heights are the 90th percentile "
            "of tile surface minus tile terrain, not surveyed storeys. Shade, "
            "ventilation and drainage are reported separately and never "
            "combined."),
        "ops": raw_ops,
        "massing": report.get("massing"),
        "axes": report.get("axes"),
        "tradeoffs": report.get("tradeoffs") or [],
        "combinedScore": report.get("combinedScore"),
        "whyNoCombinedScore": report.get("whyNoCombinedScore"),
    }
