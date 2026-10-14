"""The answer card (Constellation's `Answer` shape, trimmed to what a run knows).

Why a card at all, and not a paragraph: the same fields are always present, so
the UI can render them in the same place every time and the reader learns where
to look. A paragraph can hide a missing cause; a card with `cause: null` shows
one, which is the honest outcome when the numbers do not support one.

Every field the model may not invent is checked by `grounding.check_card`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Why a card was assembled by code when no model answered. Defined here rather
#: than in `services/run_card.py` because both that module and this one need it,
#: and `run_card` already imports from here -- putting it there would be circular.
NO_MODEL = "no language model is configured"

__all__ = [
    "ANSWER_SCHEMA",
    "AnswerCard",
    "Confidence",
    "NO_MODEL",
    "GroundingReport",
    "StatItem",
    "template_card",
]


class StatItem(BaseModel):
    """One headline number. `ci` is the honest range when one exists."""

    l: str = Field(description="Label, e.g. 'Unstored runoff'.")
    v: str = Field(description="Value with unit, e.g. '1.08 million m³'.")
    ci: str | None = Field(None, description="Uncertainty or range, e.g. 'baseline 1.08, +0.0'.")


class Confidence(BaseModel):
    """How sure the answer is, with a one-line reason the reader can check."""

    level: Literal["High", "Medium", "Low"]
    pct: int = Field(ge=0, le=100)
    note: str


class GroundingReport(BaseModel):
    """The outcome of the number check, surfaced rather than hidden.

    Constellation returns the problems to the model for a revision; we do one
    revision and then report what survived, because a planner reading "the basin
    holds 40,000 m³" needs to know whether that number came from the run.
    """

    ok: bool = Field(description="True when every number in the card came from the run.")
    ungrounded: list[str] = Field(default_factory=list)
    revised: bool = Field(False, description="True when the model was sent back once.")


class AnswerCard(BaseModel):
    """The answer, as the chat renders it."""

    kind: Literal["run", "general"] = Field(
        "run", description="'run' = about the sandbox run; 'general' = no run in scope."
    )
    title: str = Field(description="Headline, e.g. 'The buffer still overflows by 1.08 million m³'.")
    eyebrow: str | None = Field(None, description="Small label above the title.")
    sentence: str = Field(description="One-sentence answer in plain words.")
    l1: str = Field("What the run shows", description="Label above `cause`.")
    cause: str | None = Field(None, description="What the numbers say is happening; None when they do not support one.")
    l2: str = Field("What to do", description="Label above `todo`.")
    todo: str | None = Field(None, description="What to do next, or None.")
    stats: list[StatItem] = Field(default_factory=list)
    confidence: Confidence
    caveats: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list, max_length=3)
    source: Literal["model", "template"] = Field(
        "model", description="Whether a model wrote this or code assembled it."
    )
    grounding: GroundingReport | None = Field(None, description="Number check; None for template cards.")


#: The JSON schema handed to the provider. Mirrors the model above, minus the
#: fields only code fills (`source`, `grounding`).
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["run", "general"]},
        "title": {"type": "string", "maxLength": 120},
        "eyebrow": {"type": "string", "maxLength": 40},
        "sentence": {"type": "string", "maxLength": 400},
        "l1": {"type": "string", "maxLength": 40},
        "cause": {"type": "string", "maxLength": 400},
        "l2": {"type": "string", "maxLength": 40},
        "todo": {"type": "string", "maxLength": 400},
        "stats": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "l": {"type": "string", "maxLength": 40},
                    "v": {"type": "string", "maxLength": 40},
                    "ci": {"type": "string", "maxLength": 60},
                },
                "required": ["l", "v"],
            },
        },
        "confidence": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["High", "Medium", "Low"]},
                "pct": {"type": "integer", "minimum": 0, "maximum": 100},
                "note": {"type": "string", "maxLength": 200},
            },
            "required": ["level", "pct", "note"],
        },
        "caveats": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 200}},
        "followups": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 120}},
    },
    "required": ["title", "sentence", "confidence"],
}


def _m3(value: float) -> str:
    """Cubic metres, scaled to whatever reads best. Grounding sees the raw number."""
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} million m³"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f} thousand m³"
    return f"{value:.0f} m³"


def template_card(facts: dict[str, Any], question: str | None = None) -> AnswerCard:
    """The no-model card: the same fields, assembled by code and labelled.

    This is not a degraded path to be hidden. It is the answer a reader gets
    when no language model answers, and every number in it is one the run
    actually produced -- which is more than a hallucinated model answer can
    claim.

    Delegates to `services/run_card.py` so the card a planner sees in the chat is
    the same card the run's own explanation is derived from. They used to be two
    implementations of "explain this run without a model", and they disagreed.
    """
    if not facts:
        return _empty_card()
    # Imported here rather than at module scope: `run_card` imports from this
    # module, and a top-level import would be circular.
    from app.services.run_card import run_card

    return run_card(
        {
            "scenario": facts.get("scenario") or {},
            "baseline": facts.get("baseline") or {},
            "delta": facts.get("delta") or {},
            "tide": facts.get("tide") or {},
            "temperature": facts.get("temperature"),
        },
        rainfall_mm=float((facts.get("inputs") or {}).get("rainfallMm") or 0.0),
        temperature_c=(facts.get("inputs") or {}).get("temperatureC"),
        applied=facts.get("applied") or [],
        skipped=facts.get("skipped") or [],
        reason=(facts.get("data") or {}).get("reason") or NO_MODEL,
    )


def _empty_card() -> AnswerCard:
    """The card for "nothing has run yet".

    Its note must not blame a missing model: a model may be configured and
    reachable and this card would look identical, because the real cause is that
    there is no run to explain.
    """
    return AnswerCard(
        kind="general",
        title="Nothing to explain yet",
        eyebrow="Sandbox",
        sentence=("Run the scenario first: adjust the sliders, pick a project, and press "
                  "Run. Then I can answer questions about it."),
        cause=None,
        todo="Press Run Simulation in the Scenario Inputs panel.",
        stats=[],
        confidence=Confidence(
            level="Low",
            pct=30,
            note="No run in scope yet: there is nothing to explain until a scenario has been run.",
        ),
        caveats=[],
        followups=[],
        source="template",
        grounding=None,
    )
