"""Pydantic schemas for plans (技术文档 §3.3, §4.2.2).

``plan_type`` keeps the spec's 5-value CHECK verbatim.  Note the standing gap
recorded in docs/ARCHITECTURE.md: nothing in today's product *creates* an
``economic`` or ``environmental`` plan, so those two enum members are legal but
unreachable until the AI generator lands.
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.scene import (
    PlanSource,
    PlanType,
    WireModel,
)
from app.schemas.intervention import (
    AnsweredQuestion,
    InterventionIn,
    InterventionProposal,
    ProposalQuestion,
    ProposalRejection,
)


class PlanBase(WireModel):
    plan_type: PlanType = "custom"
    label: str | None = Field(default=None, max_length=200)
    source: PlanSource = "manual"
    # The raw natural-language instruction, recorded when source == "ai".
    # ADR-030: free text is stored for provenance and is never parsed into numbers.
    nl_instruction: str | None = None
    # Plan-level parameter card.  JSONB in the DDL, open-ended here.
    params: dict[str, Any] = Field(default_factory=dict)


class PlanCreate(PlanBase):
    """POST /api/scenes/{sceneId}/plans body (§3.3.3).

    Optionally carries initial interventions; they are written in the same
    transaction as the plan so a plan can never exist half-populated.
    """

    interventions: list[InterventionIn] = Field(default_factory=list)


class PlanUpdate(WireModel):
    """PUT /api/plans/{planId} (§3.3.4) -- metadata and/or要素 replacement."""

    plan_type: PlanType | None = None
    label: str | None = Field(default=None, max_length=200)
    source: PlanSource | None = None
    nl_instruction: str | None = None
    params: dict[str, Any] | None = None
    # When present, replaces the plan's intervention set wholesale.
    interventions: list[InterventionIn] | None = None


class PlanRead(PlanBase):
    """GET /api/scenes/{sceneId}/plans item (§3.3.1).

    The spec's list example carries exactly ``planId`` / ``planType`` / ``label``;
    ``sceneId`` and timestamps are added because the frontend store keys on them.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    _id_field: ClassVar[str | None] = "plan_id"

    plan_id: str
    scene_id: str
    created_at: datetime
    updated_at: datetime


class PlanListRead(WireModel):
    """GET /api/scenes/{sceneId}/plans 200 body (§3.3.1)."""

    plans: list[PlanRead]

    @field_validator("plans")
    @classmethod
    def _non_empty_ids(cls, v: list[PlanRead]) -> list[PlanRead]:
        if any(not p.plan_id for p in v):
            raise ValueError("every plan requires a planId")
        return v


class AIPlanRequest(WireModel):
    """POST /api/scenes/{sceneId}/ai-plan body.

    `技术文档` reserves this slot: "AI 相关接口只保留占位" -- the AI endpoints
    were to be placeholders in this phase. This is the placeholder filled in.

    `provider` defaults to "auto": the LLM when ANTHROPIC_API_KEY is set, and
    the deterministic heuristic otherwise. Asking for "llm" without a key is an
    error rather than a silent downgrade, because a demo that quietly stops
    being AI is worse than one that says so.

    `apply=false` activates the confirm gate (plan v2 §5): the server returns
    `InterventionProposal[]` **without persisting** them, the client renders a
    checklist, and the accepted subset is sent back to `/ai-plan/apply`. Default
    `apply=true` keeps the pre-v2 one-shot behaviour so no existing caller has
    to change.
    """

    instruction: str = Field(min_length=1, max_length=2000)
    provider: str = Field(default="auto", pattern="^(auto|llm|heuristic)$")
    max_features: int = Field(default=6, ge=1, le=20)
    apply: bool = True
    # Plan v2 §8: a minimal template whitelist. When non-empty, the server
    # drops proposals outside this list. None/[] = no restriction (default).
    allowed_types: list[str] | None = None
    # Phase 2 R13: the planner's answers to the questions a previous response
    # asked, echoed back. Matched by question id (derived from the parameter
    # alone), an in-range answer replaces the class default for that kind --
    # so answering changes the run rather than merely silencing a prompt.
    # Additive and defaulted: a pre-Phase-2 client is unaffected.
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)
    model_config = {
        "alias_generator": __import__(
            "pydantic.alias_generators", fromlist=["to_camel"]
        ).to_camel,
        "populate_by_name": True,
    }


class AIPlanResponse(WireModel):
    """What the AI module produced, and how.

    `provider` is echoed because "which model actually generated this" is the
    first question anyone will ask, and the answer must not require reading the
    store.

    When `apply=false` (plan v2 §5 confirm gate), `plan_id` is None, `features`
    is the proposed count, and `proposals` carries the un-persisted draft list
    the client echoes back to `/ai-plan/apply`. When `apply=true` the response
    matches the legacy shape (`proposals` empty, `plan_id` populated).

    `rejected` is additive (design §2.2): the refusals that did not survive the
    constraint engine, one entry each, with a closed `reasonCode` and a
    non-empty `warning`. A silently shorter `proposals` list is indistinguishable
    from a provider that returned less, so a refusal is carried, never dropped.
    """

    plan_id: str | None = None
    features: int = 0
    provider: str
    types: list[str] = Field(default_factory=list)
    grounding_notes: list[str] = Field(default_factory=list)
    message: str | None = None
    proposals: list[InterventionProposal] = Field(default_factory=list)
    rejected: list[ProposalRejection] = Field(default_factory=list)
    # Phase 2 R8: the SITE-scope questions, asked once per batch rather than
    # once per proposal -- drainage and tree cover belong to the site, and
    # repeating them on every row would be the duplication the `scope`
    # discriminator exists to prevent. Each proposal carries its own
    # PROPOSAL-scope rows.
    questions: list[ProposalQuestion] = Field(default_factory=list)
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    fallback_note: str | None = None
    model_config = {
        "alias_generator": __import__(
            "pydantic.alias_generators", fromlist=["to_camel"]
        ).to_camel,
        "populate_by_name": True,
    }


class AIPlanApplyRequest(WireModel):
    """`POST /api/scenes/{sceneId}/ai-plan/apply` body (plan v2 §5).

    The client echoes the accepted proposals (and the instruction/provider so
    the audit trail matches what the user saw). No server-held list bridges
    the two calls: the frontend owns the proposals until a user accepts.

    ``provider`` here is an **echo, not a control input**, and that distinction
    is the whole reason it is a free-form string. ``AIPlanRequest.provider``
    carries the pattern ``^(auto|llm|heuristic)$`` because there the client is
    *choosing* a provider. Here it is *reporting* which one answered, and the
    values ``generate_proposals`` actually emits are wider than that set:
    ``openai-compatible`` (any /chat/completions endpoint, including Ollama),
    ``anthropic``, ``heuristic``, ``unknown`` -- and, after a fallback, a
    composite such as ``"heuristic (after openai-compatible returned nothing)"``
    (``ai_plan.py:2287``).

    This field carried that same pattern until it was measured against a live
    Ollama run, and the consequence was total: **every apply whose proposal came
    from a real model failed 422**, because the frontend echoed back exactly the
    provider string the server had just handed it. Only the heuristic fallback
    could be confirmed at all. Rejecting a value the server itself emitted turns
    the audit trail into a trap, so the echo is bounded by length only.
    """

    instruction: str = Field(min_length=1, max_length=2000)
    provider: str = Field(default="auto", max_length=200)
    proposals: list[InterventionProposal] = Field(default_factory=list)
    grounding_notes: list[str] = Field(default_factory=list)
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    # The input card's answers, one entry per proposal (Stage 4). A key left
    # out is one the user handed to the AI's estimate.
    values: list[dict[str, Any] | None] = Field(default_factory=list)
    # Phase 2 R13: the planner's answers to the questions the gate asked. The
    # answer is not persisted server-side (the gate is stateless, and the
    # client carries it forward exactly as it carries the proposal), but an
    # in-range numeric answer is honoured on this path too, so a depth entered
    # as an answer and a depth typed into the field reach the run the same way.
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)
    model_config = {
        "alias_generator": __import__(
            "pydantic.alias_generators", fromlist=["to_camel"]
        ).to_camel,
        "populate_by_name": True,
    }


# The proposal fields above name `InterventionProposal` directly, imported at the
# top of this module beside `InterventionIn`. A private alias bound *after* the
# classes, with a `model_rebuild()` to resolve the forward ref, was the earlier
# shape here and it was import-order dependent: when the app was imported as a
# whole the model could be left incomplete, and Pydantic then resolved the name
# lazily in whatever namespace happened to be calling -- which raised
# `PydanticUndefinedAnnotation: name '_InterventionProposal' is not defined` on
# the first `/ai-plan/apply` request
# (`tests/test_contract.py::test_ai_plan_apply_persists_proposals`). There is no
# cycle to dodge: `intervention.py` imports only `scene.py`, and this module
# already imports `InterventionIn` from it at the top.
AIPlanResponse.model_rebuild()
