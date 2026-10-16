"""The sandbox loop's routes (Stage 4): suggest, confirm, modify, remove.

HTTP only: every rule lives in ``app/services/sandbox.py``. A new router rather
than more routes in ``scenes.py``, per the repo's "new feature, new files" rule.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from app.schemas.scene import WireModel

router = APIRouter()


class SandboxSuggestRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/suggestions`` body."""

    rainfall_mm: float = Field(ge=0, le=800)
    #: The plans the run scored, so a suggestion never lands on ground a
    #: toggled project already occupies.
    plan_ids: list[str] = Field(default_factory=list)


class SandboxInputsRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/inputs`` body."""

    intervention_type: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class SandboxStageRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/stage`` body.

    ``features`` are the confirmed interventions verbatim (geometry, type,
    params, style); ``values[i]`` holds the input card's answers for
    ``features[i]``. A key left out of ``values[i]`` is one the user handed to
    the AI's estimate.
    """

    features: list[dict[str, Any]] = Field(min_length=1)
    values: list[dict[str, Any] | None] = Field(default_factory=list)
    source: str = Field(default="manual", pattern="^(manual|ai)$")


class SandboxRemoveRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/resolve-removal`` body."""

    instruction: str = Field(min_length=1, max_length=2000)


class SandboxModifyRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/modify`` body.

    ``values`` are the input card's answers; a key left out keeps the value the
    intervention already carries.
    """

    feature_id: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)


def _require_scene(scene_id: str) -> None:
    from app.db import read_doc, scene_path

    if read_doc(scene_path(scene_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="scene not found")


@router.post("/scenes/{scene_id}/sandbox/suggestions")
def sandbox_suggestions(scene_id: str, body: SandboxSuggestRequest) -> dict:
    """The three suggestions shown under a run's card: one per axis.

    Real footprints from the rasters, with the estimate and the input card
    attached. Asked for after the run returns rather than inside it, because
    ranking candidates takes seconds the run should not wait on.
    """
    from app.services import sandbox

    _require_scene(scene_id)
    return sandbox.run_suggestions(scene_id, body.rainfall_mm, body.plan_ids)


@router.post("/scenes/{scene_id}/sandbox/inputs")
def sandbox_inputs(scene_id: str, body: SandboxInputsRequest) -> dict:
    """The input card for one intervention: what the run reads, and the estimate.

    An empty ``inputs`` list is an answer: the kind has no value the ledger
    reads, so there is nothing to ask.
    """
    from app.services import sandbox

    _require_scene(scene_id)
    return {"interventionType": body.intervention_type,
            "inputs": sandbox.inputs_for(body.intervention_type, body.params)}


@router.post("/scenes/{scene_id}/sandbox/stage")
def sandbox_stage(scene_id: str, body: SandboxStageRequest) -> dict:
    """Confirm interventions onto the sandbox, with the input card's answers.

    The features are pinned into the scene's staged plan, the one place the
    Interventions panel reads and every run composes. A value the run would
    refuse is a 422 naming the field, and nothing is written.
    """
    from app.services import sandbox
    from app.services.pins import PinError

    _require_scene(scene_id)
    try:
        return sandbox.stage(scene_id, body.features, body.values, body.source)
    except (sandbox.InputError, PinError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/scenes/{scene_id}/sandbox/resolve-removal")
def sandbox_resolve_removal(scene_id: str, body: SandboxRemoveRequest) -> dict:
    """What a removal instruction refers to, without removing anything.

    The client confirms with the user, then unpins the intervention or toggles
    the project off. Resolving and acting are separate calls on purpose: a
    removal is never applied on the strength of a parse alone.
    """
    from app.services import sandbox

    _require_scene(scene_id)
    return sandbox.resolve_removal(scene_id, body.instruction)


@router.post("/scenes/{scene_id}/sandbox/resolve-modification")
def sandbox_resolve_modification(scene_id: str, body: SandboxRemoveRequest) -> dict:
    """Which placed intervention a change instruction refers to, with its card.

    ``isModification: false`` means the sentence does not change anything
    placed, and the client treats it as an addition instead.
    """
    from app.services import sandbox

    _require_scene(scene_id)
    return sandbox.resolve_modification(scene_id, body.instruction)


@router.post("/scenes/{scene_id}/sandbox/modify")
def sandbox_modify(scene_id: str, body: SandboxModifyRequest) -> dict:
    """Change a placed intervention's values; its footprint stays where it is.

    A value the run would refuse is a 422 and the sandbox is left unchanged.
    """
    from app.services import sandbox
    from app.services.pins import PinError

    _require_scene(scene_id)
    try:
        return sandbox.modify(scene_id, body.feature_id, body.values)
    except (sandbox.InputError, PinError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class SandboxPreviewRequest(WireModel):
    """``POST /api/scenes/{sceneId}/sandbox/preview`` body (Phase 4 T01).

    The candidates are the ones the confirm gate is showing, in the shape
    `/ai-plan/apply` would receive them; ``values[i]`` holds the input card's
    answers for ``interventions[i]``, because the card changes the ledger and a
    preview that ignored it would measure a different intervention than the one
    about to be confirmed.

    There is **no ``tideState``**: the level-pool ledger does not consume it, and
    accepting an input this route ignores would imply the preview reflects the
    tide. ``planIds`` are the run's toggled projects, which are prior work.
    """

    interventions: list[dict[str, Any]] = Field(default_factory=list)
    values: list[dict[str, Any] | None] = Field(default_factory=list)
    plan_ids: list[str] = Field(default_factory=list)
    rainfall_mm: float = Field(default=450.0, ge=0, le=800)


@router.post("/scenes/{scene_id}/sandbox/preview")
def sandbox_preview(scene_id: str, body: SandboxPreviewRequest) -> dict:
    """What the candidates would do, measured against what is on the sandbox now.

    A dedicated route rather than a `dryRun` flag on `/run`, because a run
    writes overlays, prunes the run history to 20 entries, mints a `runId` and
    calls a model for an explanation card -- none of which a preview may do, and
    one endpoint with two shapes and different side effects is the
    shape-failure class this project has already paid for twice.

    Writes nothing, starts no run, calls no model. The response carries no
    `runId`, no `overlayUrls` and no `explanationCard` key at all, and it labels
    itself (`status` / `warning` / `scope`) in the payload rather than only in
    the UI, so any other client cannot mistake these numbers for a run's.
    """
    from app.services import preview as preview_service
    from app.services.sandbox import InputError

    _require_scene(scene_id)
    try:
        return preview_service.preview(
            scene_id,
            interventions=body.interventions,
            values=body.values,
            plan_ids=body.plan_ids,
            rainfall_mm=body.rainfall_mm,
        )
    except preview_service.PreviewUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InputError as exc:
        # A value the ledger would refuse is the card's problem, not a preview
        # bug: 422 with the same words `/sandbox/stage` would use.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
