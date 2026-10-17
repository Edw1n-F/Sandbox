"""The answer card (Constellation's `Answer` shape, trimmed to what a run knows).

Why a card at all, and not a paragraph: the same fields are always present, so
the UI can render them in the same place every time and the reader learns where
to look. A paragraph can hide a missing cause; a card with `cause: null` shows
one, which is the honest outcome when the numbers do not support one.

Every field the model may not invent is checked by `grounding.check_card`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: Why a card was assembled by code when no model answered. Defined here rather
#: than in `services/run_card.py` because both that module and this one need it,
#: and `run_card` already imports from here -- putting it there would be circular.
NO_MODEL = "no language model is configured"

__all__ = [
    "ANSWER_SCHEMA",
    "FULL_SCHEMA",
    "SLIM_SCHEMA",
    "AnswerCard",
    "Confidence",
    "NO_MODEL",
    "GroundingReport",
    "StatItem",
    "template_card",
    "unsupported_capability_card",
]


class StatItem(BaseModel):
    """One headline number. `ci` is the honest range when one exists."""

    l: str = Field(description="Label, e.g. 'Unstored runoff'.")
    v: str = Field(description="Value with unit, e.g. '1.08 million m³'.")
    ci: str | None = Field(None, description="Uncertainty or range, e.g. 'baseline 1.08, +0.0'.")


class Confidence(BaseModel):
    """How sure the answer is, with a one-line reason the reader can check.

    ``pct`` is **optional, and derived from ``level`` when the model omits it**.

    It was required, and that turned out to cost the whole card. Measured against
    a live Ollama run (`qwen2.5:3b-instruct`): the model intermittently returns
    ``{"level": "Low", "note": ...}`` with no ``pct``, which raised
    ``ValidationError: confidence.pct Field required`` and dropped the run
    explanation back to the code-assembled template -- the reader saw "the
    language model call failed (ValidationError)" for a reply that was right in
    every other respect. It reproduced in roughly 1 run in 3, so it read as
    flakiness rather than as a contract that was too strict.

    Omitting it is not the model being wrong. ``confidence._clamp_pct`` already
    forces ``pct`` into the band its own ``level`` owns -- a level outside its own
    band reads as a contradiction -- so the level is what carries the meaning and
    the percentage is a *rendering* of it. Filling it from the band's midpoint is
    therefore the same number the ladder would have clamped to anyway, not an
    invented figure. Declared optional on the wire for the same reason.
    """

    level: Literal["High", "Medium", "Low"]
    pct: int | None = Field(default=None, ge=0, le=100)
    note: str

    @model_validator(mode="after")
    def _fill_pct_from_level(self) -> "Confidence":
        # Imported here rather than at module scope: `confidence.py` owns the
        # band table and the ladder, and importing it lazily keeps this module
        # importable from `run_card.py` without a cycle.
        from app.agent.confidence import BANDS, _clamp_pct

        if self.pct is None:
            low, high = BANDS[self.level]
            # `_clamp_pct(level, x)` is the identity for an x already inside the
            # band, so this keeps the band table in exactly one place.
            self.pct = _clamp_pct(self.level, (low + high) // 2)
        return self


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
    confidence: Confidence | None = Field(
        None,
        description="How sure the model is. None becomes Low with a note that says "
                    "the model did not state one -- see _default_confidence.",
    )
    caveats: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list, max_length=3)
    source: Literal["model", "template"] = Field(
        "model", description="Whether a model wrote this or code assembled it."
    )
    grounding: GroundingReport | None = Field(None, description="Number check; None for template cards.")
    # Plan v2 §4: the LLM classifies whether the user wants to change the scene
    # or to ask about it, so the frontend no longer guesses from a 12-verb regex.
    # `question` is the safe default; a parser/template response leaves it there
    # and the client falls back to the legacy heuristic.
    intent: Literal["question", "intervention", "ambiguous"] = Field(
        "question",
        description="What the user wants: 'question' explains the run, 'intervention' "
                    "changes the scene (triggers ai-plan on the client), 'ambiguous' asks "
                    "the user to disambiguate.",
    )
    intent_hint: str | None = Field(
        None,
        description="When intent='intervention', the model's normalised English "
                    "instruction (type + region only) fed to ai-plan. None otherwise.",
    )
    # Design §2.5: routing authority is the deterministic classifier in
    # `agent/intent.py`, which runs *after* the card is parsed and can only raise
    # the intent. This field records which rule decided it, so the routing is
    # auditable and a test can assert why a turn went where it did. Code-owned:
    # a value the model writes is overwritten by `classify_turn`.
    intent_source: Literal["parser", "model", "both"] = Field(
        "parser",
        description="Which rule decided `intent`: 'parser' (the deterministic "
                    "classifier), 'model' (the model alone), or 'both' (they agreed).",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalise_lists(cls, data: Any) -> Any:
        """A scalar where a list belongs becomes a one-element list.

        Measured against the same live run, the next failure after `confidence`
        was `caveats` arriving as a plain string: ``Input should be a valid
        list``. The schema says array, and a small model writing one caveat
        reaches for the string form. That is a *container* mismatch, not a
        disagreement about content -- a single caveat written as a string says
        exactly what a one-element list says -- so it is unwrapped rather than
        treated as a malformed card.

        Only the container is repaired. Nothing is added, dropped or reworded,
        and a genuinely wrong element (an object where a string belongs) still
        fails below, because that would be a real disagreement rather than a
        shape the model expressed differently.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for key in ("caveats", "followups", "stats"):
            value = out.get(key)
            if isinstance(value, (str, bytes)) or (
                    isinstance(value, dict) and value):
                out[key] = [value]
        grounding = out.get("grounding")
        if isinstance(grounding, dict):
            ungrounded = grounding.get("ungrounded")
            if isinstance(ungrounded, (str, bytes)):
                grounding = dict(grounding)
                grounding["ungrounded"] = [ungrounded]
                out["grounding"] = grounding
        return out

    @model_validator(mode="before")
    @classmethod
    def _default_confidence(cls, data: Any) -> Any:
        """A model that omits `confidence` entirely gets Low, and says so.

        Measured against a live Ollama run (`qwen2.5:3b-instruct`), `confidence`
        was the *whole* object missing in 2 of 6 identical runs -- the same class
        of failure as the missing `pct`, one level up, and for the same reason:
        a required field on the wire that a small model drops intermittently.

        Dropping the card over it was a bad trade. The explanation is the
        valuable part; the confidence is a self-assessment the reader mostly
        ignores. So the card survives, with the weakest honest reading.

        **Low, not the clean-run cap.** `confidence.apply_ladder` already
        substitutes `CLEAN_RUN_CAP` when it is handed `None` -- but that path
        means "code is reporting this, and the run is clean". Here the model
        wrote the card and then said nothing about how sure it was, which is
        not the same claim at all, so it must not inherit that one's confidence.
        The note names the omission, so the reader can see it was inferred.
        """
        if not isinstance(data, dict) or data.get("confidence") is not None:
            return data
        out = dict(data)
        out["confidence"] = {
            "level": "Low",
            "note": ("the model did not state a confidence for this answer, so "
                     "it is reported as Low"),
        }
        return out


#: The full JSON schema handed to the provider. Mirrors the model above, minus
#: the fields only code fills (`source`, `grounding`). Used by the run
#: explanation card, which fills every field.
FULL_SCHEMA: dict[str, Any] = {
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
                # Optional on purpose, and omitted from `required` below. It was
                # required, and a 3B model intermittently left it out, which
                # raised ValidationError and threw away an otherwise good card
                # (see Confidence). The percentage is a rendering of `level` --
                # the ladder clamps it into that level's band -- so a model that
                # gives the level and the reason has said everything worth saying.
                "pct": {"type": "integer", "minimum": 0, "maximum": 100},
                "note": {"type": "string", "maxLength": 200},
            },
            "required": ["level", "note"],
        },
        "caveats": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 200}},
        "followups": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 120}},
        # Plan v2 §4: let the model tell us whether this turn is a question or
        # an intervention request. Optional on the wire (JSON contract tolerates
        # legacy responses missing the key) and defaulted to "question" in the
        # pydantic model, so a parser/template answer costs the client nothing.
        "intent": {"type": "string", "enum": ["question", "intervention", "ambiguous"]},
        "intent_hint": {"type": "string", "maxLength": 120},
        # Code-owned, and present so the contract is complete rather than
        # implying the model may set it. `classify_turn` overwrites any value.
        "intent_source": {"type": "string", "enum": ["parser", "model", "both"]},
    },
    # `confidence` was here and is deliberately not: a 3B model dropped the whole
    # object in 2 of 6 live runs, and losing the whole explanation to a missing
    # self-assessment was a bad trade. `AnswerCard` fills Low with a note that
    # names the omission, so a card that arrives without one is still labelled
    # honestly rather than discarded.
    "required": ["title", "sentence"],
}

#: The full schema under its historical name. ``run_service`` and any caller
#: that wants the complete card keep importing this; it is the same object as
#: :data:`FULL_SCHEMA`, so an identity check in a test stays true.
ANSWER_SCHEMA = FULL_SCHEMA

#: The slim schema for the chat, which answers with a headline and a sentence
#: only -- no cause/todo/stats/caveats/confidence/followups. The ``required``
#: set stays just ``title``/``sentence``: ``intent`` has a ``"question"``
#: default and ``classify_turn`` falls back to it, so requiring it here would
#: make a small model that omits it drop the whole card. The prose contract
#: (``chat.CHAT_CONTRACT``) writes ``intent`` as required; the schema stays
#: permissive so the card still parses.
SLIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["run", "general"]},
        "title": {"type": "string", "maxLength": 120},
        "sentence": {"type": "string", "maxLength": 400},
        "intent": {"type": "string", "enum": ["question", "intervention", "ambiguous"]},
        "intent_hint": {"type": "string", "maxLength": 120},
    },
    "required": ["title", "sentence"],
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


def unsupported_capability_card(question: str, card: AnswerCard) -> AnswerCard:
    """The card for a request the sandbox has no intervention to answer (§3B.5).

    A sibling of :func:`_empty_card`, and the reason it exists is that
    `_empty_card` dates the cause **wrongly**. For "add a school" it says
    ``confidence.note = "No run in scope yet ..."`` and ``todo = "Press Run
    Simulation ..."`` -- and both are false: a run may well exist, and pressing
    Run Simulation does not help. Worse, with a run in scope the generic run card
    answers a capability gap with "the run is unchanged from the baseline", which
    reads as though the request were understood. This card states the true cause
    -- a limit of what the endpoint can create -- and carries the capability
    statement whose every clause is a fact about the system, never a claim about
    the user's sentence (see :func:`app.agent.intent.capability_statement`).

    `question` is accepted so the builder has the same signature at every call
    site; the wording is deliberately independent of it, because echoing the
    sentence back is how a message drifts into asserting we understood it.
    """
    # Imported here, not at module scope: `intent` imports this module (`AnswerCard`),
    # so a top-level import would be circular -- the same reason `run_card` and
    # `confidence` are imported lazily elsewhere in this file.
    from app.agent.intent import capability_statement

    del question  # interface stability only; the wording never reads it
    return card.model_copy(update={
        "kind": "general",
        "title": "I could not match that to an intervention I can create",
        "eyebrow": "Capability",
        "sentence": ("That is not something this sandbox can turn into a proposal "
                     "-- the cause is a limit of what the endpoint can create, "
                     "not a missing run and not a missing model."),
        "l1": "What this sandbox can create",
        "cause": capability_statement(),
        "l2": "What to do",
        "todo": ("Name one of the interventions above (e.g. \"add a water "
                 "reservoir in the north\"), or ask a question about the current "
                 "run."),
        "stats": [],
        "caveats": [
            "This states what the endpoint can create; it does not claim to have "
            "understood the sentence, because a synonym can read as a miss.",
        ],
        "followups": [],
        "source": "template",
        "grounding": None,
        "confidence": Confidence(
            level="Low",
            pct=30,
            note=("No intervention this endpoint can create matches that request: "
                  "the cause is a capability of the endpoint, not a missing run "
                  "and not a missing model."),
        ),
    })
