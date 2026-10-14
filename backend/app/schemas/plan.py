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
from app.schemas.intervention import InterventionIn


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
    """

    instruction: str = Field(min_length=1, max_length=2000)
    provider: str = Field(default="auto", pattern="^(auto|llm|heuristic)$")
    max_features: int = Field(default=6, ge=1, le=20)
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
    """

    plan_id: str | None = None
    features: int = 0
    provider: str
    types: list[str] = Field(default_factory=list)
    grounding_notes: list[str] = Field(default_factory=list)
    message: str | None = None
    model_config = {
        "alias_generator": __import__(
            "pydantic.alias_generators", fromlist=["to_camel"]
        ).to_camel,
        "populate_by_name": True,
    }
