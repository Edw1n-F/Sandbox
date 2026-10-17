"""Shared prompt machinery for the chat and the run-explanation cards.

`render_facts` and `user_turn` build the FACTS block and the per-turn message the
same way for both the chat and the result card, so the two paths cannot drift in
how they hand a run's numbers to a model. `_data_quality_note` is the prose
rendering of the run's own report on its inputs, used by `render_facts`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_facts", "user_turn"]


def render_facts(facts: dict[str, Any]) -> str:
    """The FACTS block: every number the model may quote, labelled and unit-ed.

    Deliberately exhaustive rather than curated. A model that cannot see the
    baseline cannot answer "is this better than before", and a model that can
    see it will quote it -- which is exactly what grounding exists to verify.
    """
    def block(title: str, values: dict[str, Any]) -> str:
        if not values:
            return ""
        # `bool` is a subclass of `int`, so a bool would render as "True" here
        # and read as a fact. The data block is prose below, not numbers.
        lines = [f"{key}: {value}" for key, value in values.items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool)]
        return f"[{title}]\n" + "\n".join(lines) if lines else ""

    parts = [
        block("INPUTS", facts.get("inputs", {}) or {}),
        block("SCENARIO (this run)", facts.get("scenario", {}) or {}),
        block("BASELINE (no project)", facts.get("baseline", {}) or {}),
        block("DELTA (scenario - baseline)", facts.get("delta", {}) or {}),
    ]
    applied = facts.get("applied") or []
    skipped = facts.get("skipped") or []
    if applied:
        parts.append(f"[APPLIED INTERVENTIONS]\n" + "\n".join(f"- {a}" for a in applied))
    if skipped:
        parts.append(f"[SKIPPED INTERVENTIONS]\n" + "\n".join(f"- {s}" for s in skipped))
    quality = _data_quality_note(facts.get("data") or {})
    if quality:
        parts.append(quality)
    body = "\n\n".join(p for p in parts if p)
    return f"FACTS\n{body}\n\nUnits: m3 = cubic metres, ha = hectares, \
heatBlendedMean is a 0-1 index. Only these numbers may appear in your answer."


def _data_quality_note(data: dict[str, Any]) -> str:
    """What the run says about its own inputs, as prose rather than as fields.

    Written as sentences for a reason. If this were rendered as `lstAvailable:
    False` a model could quote the token, and a reader would have to know what it
    means. As prose the model can carry the honest caveat into the card in the
    user's own language -- and the confidence ladder already caps the level, so
    the caveat and the number agree.
    """
    if not data:
        return ""
    lines: list[str] = []
    if data.get("lstAvailable") is False:
        lines.append("- The observed heat anchor did not load; heat figures come from a fallback field.")
    else:
        lines.append("- Heat figures are anchored to an observed Landsat surface-temperature scene.")
    failed = data.get("sanityChecksFailed") or 0
    if failed:
        ids = ", ".join(str(i) for i in (data.get("sanityCheckIds") or []))
        lines.append(f"- {failed} sanity check(s) fired on this run ({ids}): the numbers are arithmetically "
                     f"legal but not credible, and confidence is capped accordingly.")
    else:
        lines.append("- No sanity check fired.")
    if data.get("interventionsSkipped"):
        lines.append("- Some interventions could not be applied, so this scenario is not exactly the one asked for.")
    lines.append(f"- Terrain from {data.get('terrainSource') or 'an unnamed source'}; "
                 f"land cover from {data.get('landCoverSource') or 'an unnamed source'}.")
    return "[DATA QUALITY]\n" + "\n".join(lines)


def user_turn(question: str, facts: dict[str, Any]) -> str:
    """The per-turn message: facts first, so the question is read against them."""
    return f"{render_facts(facts)}\n\nQUESTION\n{question.strip()}"
