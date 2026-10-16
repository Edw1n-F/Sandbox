"""`POST /api/scenes/{sceneId}/ai-plan/revalidate` (Phase 2 R19.1).

Its own router rather than a route appended to `routers/scenes.py`, per the
repo's rule that a new feature gets new files and shared modules only grow at
the end. Nothing here touches the plan store: the re-gate is a read of the
constraint engine over proposals the client already holds.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.revalidate import (
    RevalidateRequest,
    RevalidateResponse,
)
from app.services.revalidate import RevalidationError, revalidate

router = APIRouter(tags=["ai"])


@router.post("/scenes/{scene_id}/ai-plan/revalidate",
             response_model=RevalidateResponse)
def revalidate_plan(scene_id: str, payload: RevalidateRequest) -> RevalidateResponse:
    """Re-check edited proposals without saving them.

    The confirm gate's third call: the client holds the proposals, the planner
    edits a parameter, and this reports what the edit did. It persists nothing,
    starts no run and calls no model, so it is safe to call on every keystroke
    that changes the set.

    A depth outside the accepted range is a **422**, not a constraint refusal:
    the closed `reasonCode` set carries placement predicates on geometry, and a
    client typo is not one of those (R20).
    """
    answers = {q.id: q.answer for q in payload.answered_questions}
    try:
        result = revalidate(
            scene_id,
            [p.model_dump(by_alias=False) for p in payload.proposals],
            answers=answers,
        )
    except RevalidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RevalidateResponse.model_validate(result)
