"""The run's own card, and the reason it was assembled by code.

Why this module exists rather than the card living in either caller: the same
card is produced on two paths, and they used to disagree.

  * the **run result** carries an explanation paragraph shown automatically after
    every run, and
  * the **chat** falls back to `answer.template_card` when no model answers.

Both are "the run explained without a model", but only the chat's version had the
card shape -- the run's was a paragraph with different wording, different numbers
and different framing. A planner who ran once and then asked a question got two
different descriptions of the same run, which is worse than one imperfect one.

So the card is built here, once, and `run_service` keeps a plain-text rendering of
it for the places that want a paragraph (`explanation`). The card is the source;
the prose is derived.

The `reason` is not decoration. "No language model is configured" and "the model
was rate-limited" are different problems -- only one is worth waiting out -- and
they used to be reported identically because the calling code swallowed the
distinction along with the exception.
"""

from __future__ import annotations

from typing import Any

from app.agent.answer import NO_MODEL, AnswerCard, Confidence, StatItem, _m3

__all__ = ["NO_MODEL", "explanation_note", "explanation_text", "run_card"]


def explanation_note(exc: BaseException | None) -> str:
    """Why code wrote the explanation, in words a planner can act on.

    Takes the exception rather than a flag on purpose: collapsing `LLMError` and
    `ProviderUnavailable` into one boolean is exactly how a 429 came to be
    reported as a missing key.
    """
    if exc is None:
        return "the language model returned nothing"
    from app.agent import llm

    if isinstance(exc, llm.ProviderUnavailable):
        return NO_MODEL
    if isinstance(exc, llm.LLMError):
        # `message` already reads like "provider HTTP 429", which is the part
        # that tells the reader this is transient.
        return f"the language model was unavailable ({exc.message})"
    return f"the language model call failed ({type(exc).__name__})"


def run_card(stats: dict[str, Any], rainfall_mm: float,
             temperature_c: float | None = None,
             applied: list[dict] | None = None,
             skipped: list[dict] | None = None,
             reason: str = NO_MODEL) -> AnswerCard:
    """The run as a card, assembled by code. Every number is the run's own.

    `reason` goes into the confidence note and the first caveat, so the reader is
    told *why* this is a printout rather than an interpretation.
    """
    scen = stats.get("scenario") or {}
    base = stats.get("baseline") or {}
    delta = stats.get("delta") or {}
    tide = stats.get("tide") or {}

    unstored = float(scen.get("unstoredM3", 0.0))
    wet = float(scen.get("wetAreaHa", 0.0))
    heat = float(scen.get("heatBlendedMean", 0.0))
    d_unstored = float(delta.get("unstoredM3", 0.0))
    d_wet = float(delta.get("wetAreaHa", 0.0))
    d_heat = float(delta.get("heatBlendedMean", 0.0))

    improved = d_unstored < -1.0
    worsened = d_unstored > 1.0
    title = (
        "The run is unchanged from the baseline"
        if not (improved or worsened)
        else "The interventions reduce the overflow"
        if improved
        else "The buffer still overflows"
    )

    sentence = (
        f"At {rainfall_mm:.0f} mm of rain, {_m3(unstored)} of runoff is not stored."
        if unstored
        else f"At {rainfall_mm:.0f} mm of rain, every drop is stored."
    )

    cause_bits = [f"Wet area {wet:.1f} ha."]
    if d_wet:
        cause_bits.append(f"{d_wet:+.1f} ha vs baseline.")
    cause_bits.append(f"Blended heat index {heat:.3f}.")
    if d_heat:
        cause_bits.append(f"{d_heat:+.3f} vs baseline.")

    todo = (
        "Run again with a project enabled to compare interventions."
        if not (improved or worsened)
        else "Toggle a project and re-run to see whether the change holds."
    )

    stats_items = [
        StatItem(l="Unstored runoff", v=_m3(unstored),
                 ci=None if abs(d_unstored) < 1.0 else f"{_m3(d_unstored)} vs baseline"),
        StatItem(l="Wet area", v=f"{wet:.1f} ha",
                 ci=None if not d_wet else f"{d_wet:+.1f} ha vs baseline"),
        StatItem(l="Heat index", v=f"{heat:.3f}",
                 ci=None if not d_heat else f"{d_heat:+.3f} vs baseline"),
    ]
    if isinstance(temperature_c, (int, float)):
        stats_items.append(StatItem(l="Surface temperature", v=f"{temperature_c:.1f} °C"))

    caveats = [f"Assembled by code: {reason}."]
    if applied:
        kinds = ", ".join(sorted({str(a.get("kind")) for a in applied}))
        caveats.append(f"{len(applied)} intervention(s) shaped this run: {kinds}.")
    if skipped:
        caveats.append(f"{len(skipped)} intervention(s) had no land-cover effect and were reported, not approximated.")
    caveats.append("Screening SCS-CN and level-pool results for this scenario only, not engineering-grade.")
    return AnswerCard(
        kind="run",
        title=title,
        eyebrow="Sandbox run",
        sentence=sentence,
        l1="What the run shows",
        cause=" ".join(cause_bits),
        l2="What to do",
        todo=todo,
        stats=stats_items[:4],
        # Medium, not High: a template can only report what the model computed,
        # and the printout of a scenario is not an explanation of a place.
        confidence=Confidence(
            level="Medium",
            pct=60,
            note=f"Assembled from the run's own numbers; {reason}.",
        ),
        caveats=caveats[:3],
        followups=[
            # Intervention instructions, not questions: clicking one goes back
            # through chat, is judged intent="intervention", and opens the
            # ai-plan confirm gate. A question ("Is this better than baseline?")
            # would just re-ask the run and never reach the design loop.
            "add a water reservoir in the north",
            "add green space in the centre",
            "add permeable paving across the whole area",
        ],
        source="template",
        grounding=None,
    )


def explanation_text(card: AnswerCard, tide: dict[str, Any] | None = None) -> str:
    """The card as the paragraph the run response has always carried.

    Derived from the card rather than written alongside it, so the two cannot
    disagree. Kept because the paragraph is what a plain `POST /run` consumer
    reads, and a card is a heavier thing to hand a script.
    """
    parts = [f"{card.title}.", card.sentence]
    if card.cause:
        parts.append(card.cause)
    if card.todo:
        parts.append(card.todo)
    if tide and tide.get("active"):
        parts.append(
            f"{tide.get('label')} ({tide.get('tailwaterM', 0.0):.1f} m tailwater) "
            "holds water at the outfall; the flood extent reflects this "
            "screening backwater condition."
        )
    # The screening-flood caveat is the one line a reader must not be able to miss,
    # so it is stated in the prose as well as carried in `caveats`. `test_run`
    # asserts the word "flood" survives here, and it caught this text losing it
    # when the card became the source.
    parts.append(
        "Screening-level SCS-CN runoff routed to a flood extent; not "
        "engineering-grade."
    )
    parts.append(f"Assembled by code: {card.confidence.note.split('; ', 1)[-1]}")
    return " ".join(parts)
