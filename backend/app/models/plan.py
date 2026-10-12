"""Storage-layer row definitions for ``plan`` (§4.2.2)."""

from typing import Any, NotRequired, TypedDict


class PlanRow(TypedDict):
    """One row of the ``plan`` table.

    ``plan_type`` reproduces the DDL CHECK verbatim.  Note the known gap recorded
    in docs/ARCHITECTURE.md: ``economic`` and ``environmental`` are legal values
    that nothing in today's product creates -- they exist for the AI generator,
    which is out of scope until the physics engine lands.
    """

    id: str
    scene_id: str  # REFERENCES scene(id) ON DELETE CASCADE
    plan_type: str  # CHECK IN ('baseline','economic','environmental','comprehensive','custom')
    label: NotRequired[str]  # "基线" / "方案A" / "方案B"
    source: str  # CHECK IN ('manual','ai')
    nl_instruction: NotRequired[str]  # original instruction, when source == 'ai'
    params: NotRequired[dict[str, Any]]  # plan-level parameter card
    interventions: NotRequired[list[dict[str, Any]]]  # denormalised for the file store
    created_at: str
    updated_at: str


PLAN_COLUMNS: tuple[str, ...] = tuple(PlanRow.__annotations__)
