"""Wire schemas for the confirm gate's re-gate call (Phase 2 R19.1).

## What this call is for

`POST /api/scenes/{sceneId}/ai-plan/revalidate` answers one question: *what did
my edit just do?* A planner who sets a storage depth by hand has changed a value
the constraint engine reads, and must be able to see the consequence -- and the
refusal, if the edit made the placement illegal -- **before** pressing Apply.

## Why it is stateless

No writes, no plan id, no run id. The proposals travel in from the client and
come back gated, exactly as the gate's own first call does, so a client that
loses the response has lost nothing but the render. Revalidating never spends a
provider call either: a planner editing a number wants the constraints
re-checked, not a second opinion invented.

New file rather than additions to `app/schemas/plan.py`, per the repo's
append-only rule for shared modules -- nothing here is used by the plan storage
path.
"""

from __future__ import annotations

from app.schemas.intervention import (
    AnsweredQuestion,
    InterventionProposal,
    ProposalQuestion,
    ProposalRejection,
)
from app.schemas.scene import WireModel
from pydantic import Field

#: The re-gate's own honesty label, stated on every response like every other
#: status in this project rather than left to be inferred from the body.
REVALIDATE_STATUS = "SCREENING_PROPOSAL_REVALIDATED"


class RevalidateRequest(WireModel):
    """The proposals as the client currently holds them, plus any answers.

    ``instruction`` is carried for the audit trail the response echoes; it does
    not re-drive the model, so a revalidate cannot change which provider was
    used or what it proposed.
    """

    proposals: list[InterventionProposal] = Field(default_factory=list)
    instruction: str = ""
    # Phase 2 R13: an answer the planner typed in this same edit, so a depth
    # entered as an answer and a depth typed into the field take one path.
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)


class RevalidateResponse(WireModel):
    """What the edit did, split by whether it could move geometry (R15).

    ``geometry_affecting_parameters`` is empty on the edit path this endpoint
    exists for, and it is returned as an explicit empty list rather than
    omitted so the client **renders** the distinction instead of inferring it
    from a missing field. ``values_affecting_only`` names the parameters whose
    change touched no cell.

    ``explicit_storage_added_m3`` and ``net_storage_capacity_change_m3`` are
    recomputed for the edited set so the consequence is visible before Apply --
    and they are the *gross* and *net* figures, never conflated, because the
    run's own net capacity change also credits the storage the ground gave up.
    """

    proposals: list[InterventionProposal] = Field(default_factory=list)
    rejected: list[ProposalRejection] = Field(default_factory=list)
    questions: list[ProposalQuestion] = Field(default_factory=list)
    status: str = REVALIDATE_STATUS
    warning: str = ""
    geometry_affecting_parameters: list[str] = Field(default_factory=list)
    values_affecting_only: list[str] = Field(default_factory=list)
    explicit_storage_added_m3: float | None = None
    net_storage_capacity_change_m3: float | None = None
    provider: str = "revalidate"
