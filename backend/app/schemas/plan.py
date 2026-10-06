"""Pydantic schemas for plans (技术文档 §3.3, §4.2.2).

``plan_type`` keeps the spec's 5-value CHECK verbatim.  Note the standing gap
recorded in docs/OCT6_STATUS.md: nothing in today's product *creates* an
``economic`` or ``environmental`` plan, so those two enum members are legal but
unreachable until the AI generator lands.
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.scene import (
    InterventionSource,
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
