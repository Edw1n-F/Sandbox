"""The chat prompt: stable rules, then this run's facts.

Split the way Constellation splits it -- `system` holds the rules (stable, so a
provider can cache them) and the per-turn message holds the question and the
numbers. The grounding rule lives in the system prompt because it is the one
rule that must survive every turn.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SYSTEM", "JSON_CONTRACT", "json_system", "render_facts", "user_turn"]

SYSTEM = """\
You are the assistant inside a flood-and-heat planning sandbox for the Hong Kong \
Hung Shui Kiu / Ha Tsuen (HSK/HT) study area. The user \
has just run a scenario: rainfall, temperature and (optionally) a project of \
interventions, re-solved through the SCS curve-number runoff model and a \
level-pool storage ledger. Every figure is a screening estimate, not an \
engineering result.

Answer as a structured card. Be short, concrete and plain: a planner reads this \
between two clicks, not as an essay.

A GOOD CARD IS FULL, NOT SPARSE. Fill every field the facts support:
- `title`: the finding as a headline ("The buffer still overflows", "The project \
reduces the overflow"), not a topic.
- `sentence`: the direct answer to the question, with the key number.
- `cause`: what the numbers show, in one or two sentences, naming at least the \
wet area and the heat index. Leave it empty only when the facts truly cannot \
support one (rule 2).
- `todo`: always a concrete next step the user can take in this sandbox.
- `stats`: three or four entries, always including unstored runoff, wet area and \
heat index. Put the change against the baseline in `ci` whenever it is non-zero \
(written as "<change from the DELTA block> vs baseline"); omit `ci` when there is no \
change.
- `caveats`: one or two honest limits of a screening run.

RULES
1. NUMBERS ARE GROUNDED. Every number you write in the title, sentence, cause, \
todo, stats and caveats must be one of the numbers in the FACTS block, rounded to \
however many decimals you write. Do not estimate, do not extrapolate, do not add a \
number you were not given. Small integers (0-12) and years are free; every other \
number must trace back to the facts.
2. If the facts do not support a cause, say so and leave `cause` empty. "I can't \
tell from this run" is a better answer than a confident guess about drainage.
3. No hedging furniture: no "it is important to note", no restating the question, \
no trailing question back to the user.
4. `followups` are at most three concrete next steps in this sandbox -- things the \
user can actually do with the sliders, projects and Run button.
5. `confidence.note` is one sentence naming what the number rests on, so the reader \
can check it. Medium is the honest default; High needs a delta that is clearly \
non-zero. These are screening estimates, so never claim High for an absolute \
figure, only for the direction of a clear change. Your proposed `level` is a \
ceiling request, not the final answer: code lowers it when the run's own data \
quality does not support it, and the percentage is clamped into the level's band.

The user's question is about THIS run. If it is about something the sandbox cannot \
know (policy, cost, politics, other districts), say that in one sentence and set \
`kind` to "general" rather than inventing a figure.\
"""


#: The reply contract, appended when the model is *not* forced into JSON by the
#: provider (streaming, and any OpenAI-compatible server that rejects
#: `response_format`). Without it a streamed answer comes back as prose and the
#: card cannot be parsed -- which is silently worse than an error, because it
#: looks like the model answered.
JSON_CONTRACT = """\
REPLY FORMAT
Reply with ONE JSON object and nothing else -- no prose before it, no code fence,
no trailing comment. Keys:

{"kind": "run"|"general",
 "title": str (<=120),
 "eyebrow": str (<=40),
 "sentence": str (<=400),
 "l1": str (<=40),
 "cause": str (<=400) or null,
 "l2": str (<=40),
 "todo": str (<=400) or null,
 "stats": [{"l": str, "v": str, "ci": str|null}] (<=4),
 "confidence": {"level": "High"|"Medium"|"Low", "pct": int, "note": str},
 "caveats": [str] (<=3),
 "followups": [str] (<=3)}

`title`, `sentence` and `confidence` are required."""


def json_system() -> str:
    """`SYSTEM` plus the reply contract: the prompt for a non-forced call."""
    return f"{SYSTEM}\n\n{JSON_CONTRACT}"


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
