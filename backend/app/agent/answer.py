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

__all__ = [
    "ANSWER_SCHEMA",
    "AnswerCard",
    "Confidence",
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
    when no model is configured, and every number in it is one the run actually
    produced -- which is more than a hallucinated model answer can claim.
    """
    scenario = facts.get("scenario", {}) or {}
    delta = facts.get("delta", {}) or {}
    inputs = facts.get("inputs", {}) or {}

    unstored = float(scenario.get("unstoredM3", 0.0))
    wet = float(scenario.get("wetAreaHa", 0.0))
    heat = float(scenario.get("heatBlendedMean", 0.0))
    d_unstored = float(delta.get("unstoredM3", 0.0))
    d_heat = float(delta.get("heatBlendedMean", 0.0))
    d_wet = float(delta.get("wetAreaHa", 0.0))
    rainfall = inputs.get("rainfallMm")
    temperature = inputs.get("temperatureC")

    improved = d_unstored < -1.0
    worsened = d_unstored > 1.0
    title = (
        "The interventions reduce the overflow"
        if improved
        else "The buffer still overflows"
        if worsened
        else "The run is unchanged from the baseline"
    )
    sentence = (
        f"At {rainfall:.0f} mm of rain"
        if isinstance(rainfall, (int, float))
        else "For this scenario"
    )
    sentence += f", {_m3(unstored)} of runoff is not stored"
    if abs(d_unstored) > 1.0:
        sentence += f", {abs(d_unstored) / 1_000_000:.2f} million m³ {'less' if improved else 'more'} than the baseline"
    sentence += "."

    cause_bits = [
        f"Wet area {wet:.1f} ha.",
        f"Blended heat index {heat:.3f}"
        + (f" ({d_heat:+.3f} vs baseline)." if abs(d_heat) > 1e-6 else "."),
    ]
    if isinstance(temperature, (int, float)):
        cause_bits.append(f"Air temperature {temperature:.0f} °C.")
    if abs(d_wet) > 1e-6:
        cause_bits.append(f"Wet area changed by {d_wet:+.1f} ha.")

    todo = (
        "Run again with a project enabled to compare interventions."
        if not (improved or worsened)
        else "Toggle a project and re-run to see whether the change holds."
    )

    return AnswerCard(
        kind="run",
        title=title,
        eyebrow="Sandbox run",
        sentence=sentence,
        l1="What the run shows",
        cause=" ".join(cause_bits),
        l2="What to do",
        todo=todo,
        stats=[
            StatItem(l="Unstored runoff", v=_m3(unstored),
                     ci=None if abs(d_unstored) < 1.0 else f"{d_unstored / 1_000_000:+.2f} million m³ vs baseline"),
            StatItem(l="Wet area", v=f"{wet:.1f} ha",
                     ci=None if abs(d_wet) < 1e-6 else f"{d_wet:+.1f} ha vs baseline"),
            StatItem(l="Heat index", v=f"{heat:.3f}",
                     ci=None if abs(d_heat) < 1e-6 else f"{d_heat:+.3f} vs baseline"),
        ],
        # Medium, not High: a template can only report what the model computed,
        # and the printout of a scenario is not an explanation of a place.
        confidence=Confidence(
            level="Medium",
            pct=60,
            note="Assembled from the run's own numbers; no language model is configured, "
                 "so this is a printout rather than an interpretation.",
        ),
        caveats=[
            "No language model configured: this card was assembled by code.",
            "Figures are the SCS-CN and level-pool results for this scenario only.",
        ],
        followups=[
            "What changes if rainfall doubles?",
            "Which project helps most?",
            "Where does the water pond?",
        ],
        source="template",
        grounding=None,
    )
