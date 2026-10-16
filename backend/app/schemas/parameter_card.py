"""Wire schemas for the screening parameter card (brief §2).

## What this card is

The brief's project-parameter table: the handful of numbers a reader needs
*before* trusting any result — site area, how much pond/green/impervious ground
there is, how much storage the proposal claims, what rainfall and heat the run
assumed, and — the rows that matter most — **the things this project does not
know**.

## Why every field is defaulted

The card is additive. A client written before this endpoint existed parses
unchanged, and a row whose value this project does not have is represented as
``value: None`` with a stated ``reason``, never omitted. An absent row and a
row we cannot fill look identical on the wire, and the difference between "we
did not measure this" and "we forgot to include it" is exactly the difference
this project is trying to keep visible.

## The honesty rules encoded here

- ``value`` may be ``None``. That is a *finding*, not a gap: ``soilGroup`` is
  permanently ``None`` because no soil survey exists in this project.
- ``confidence`` is a closed enum, not free text. ``MEASURED`` (read from a
  committed raster), ``DERIVED`` (computed from one), ``ASSUMED`` (a stated
  class assumption), ``UNAVAILABLE`` (this project does not have it).
- ``date`` is ``None`` unless a real acquisition date exists. It is never
  today's date: writing that would be a fabrication *and* would break
  determinism (invariant 7).
"""

from typing import Literal

from pydantic import Field

from app.schemas.scene import WireModel

#: The four states a card row's confidence can be in. Closed on purpose: a
#: free-text confidence would drift into adjectives nobody can act on, and the
#: whole point of the column is that a reader can sort or filter by it.
Confidence = Literal["MEASURED", "DERIVED", "ASSUMED", "UNAVAILABLE"]

#: The card's own honesty label. Upper-snake like every other status in this
#: project, and stated on every response rather than inferred from the rows.
CARD_STATUS = "SCREENING_PARAMETER_CARD"


class ParameterCardEntry(WireModel):
    """One row of the card.

    ``key`` is stable and machine-readable; ``label`` is the human phrase. Both
    are always present even when ``value`` is ``None``, so a row that cannot be
    filled still appears and states why.
    """

    key: str
    label: str
    # `None` means "this project does not have this number". It is never a
    # stand-in for zero: an absent soil survey is not a soil group of 0.
    value: float | None = None
    unit: str = ""
    status: str = "DERIVED"
    # What produced the value, named concretely enough to be checked. For an
    # unavailable row this is where the *absence* is sourced.
    source: str = ""
    date: str | None = None
    confidence: Confidence = "UNAVAILABLE"
    note: str = ""


class ParameterCard(WireModel):
    """`GET /api/scenes/{sceneId}/parameter-card` response.

    ``sources`` names the rasters the numbers came from, so a reader can tell
    whether they are looking at the committed analysis or a synthetic fallback
    without reading any individual row. ``inputs`` echoes the four values the
    caller chose (or the defaults), because a card whose inputs are not visible
    cannot be compared against another card.
    """

    scene_id: str = ""
    plan_id: str | None = None
    status: str = CARD_STATUS
    # Non-empty on every response, invariant 3: a card is a screening artefact
    # and says so in words, not only in a status string nobody reads.
    warning: str = ""
    sources: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, object] = Field(default_factory=dict)
    entries: list[ParameterCardEntry] = Field(default_factory=list)