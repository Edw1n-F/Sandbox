"""The confidence ladder: code caps the claim, the model does not get to set it.

Why this exists, in one line: except for `confidence`, every number in a card is
checked by code and the model is never asked whether it is right. Confidence was
the one field left to the model's own self-assessment, which is exactly the
boundary this project draws everywhere else. So the model still *proposes* a
level -- it knows whether a delta was large or marginal -- and code decides how
high that proposal may stand on the evidence that actually exists.

The ladder, adapted from Constellation's:

  1. base     the model's proposed level, or Medium when it proposed nothing
  2. cap      the highest level the run's own data quality supports
  3. lower    one rung per run problem (a skipped intervention)
  4. clamp    the percentage into whatever band the surviving level owns

Steps 2-4 are arithmetic. Nothing here calls a model, and nothing here reads a
number the run did not report.

**What the caps are, and are not.** A cap is a statement about the run's inputs,
not about its findings. "The heat anchor did not load" genuinely lowers
confidence in anything a card says about heat. It does *not* follow that a clean
run supports a strong conclusion -- so a clean run caps at Medium, and High is
reserved for a delta the run can demonstrate. That is the same rule already
written into the prompt (rule 5); this module is where it becomes enforced
rather than requested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.answer import Confidence

__all__ = ["BANDS", "Cap", "Level", "cap_of", "apply_ladder"]

Level = str  # "High" | "Medium" | "Low"

#: Ascending, so a cap is a `min` on the index.
LEVELS: tuple[Level, ...] = ("Low", "Medium", "High")

#: The percentage band each level owns. A level whose percentage sits outside its
#: own band reads as a contradiction ("High confidence, 40 %"), so once a level is
#: decided the percentage is clamped into it.
BANDS: dict[Level, tuple[int, int]] = {
    "Low": (20, 50),
    "Medium": (55, 79),
    "High": (80, 95),
}

#: What a run with nothing wrong with it can reach. Not "High": every number in
#: this prototype is a screening estimate, and the run has no error bars to
#: justify "High" about a magnitude. See the prompt's rule 5.
CLEAN_RUN_CAP: Level = "Medium"

#: A note this long is a paragraph. The card's schema advertises 200 characters.
NOTE_MAX = 200


@dataclass(frozen=True)
class Cap:
    """A ceiling on confidence, with the reason a reader would need to accept it."""

    level: Level
    reason: str


def rank(level: Level) -> int:
    return LEVELS.index(level) if level in LEVELS else 1


def _level_of(index: int) -> Level:
    return LEVELS[max(0, min(len(LEVELS) - 1, index))]


def cap_of(data: dict[str, Any]) -> Cap | None:
    """The highest level this run's data quality supports, or None when unbounded.

    `data` is `build_facts`' `data` block: booleans about the run, all of them
    things the run reports about itself. Anything missing is treated as *unknown*,
    not as fine -- an older client that sends no `data` block gets the clean-run
    cap rather than a free pass.
    """
    if not data:
        return Cap(CLEAN_RUN_CAP, "no data-quality report travelled with this run")

    reasons: list[str] = []

    # The heat anchor: everything the heat map says rests on it. When it is
    # absent the surface temperature is a synthetic fallback, and the card should
    # not sound as sure about heat as about water.
    if data.get("lstAvailable") is False:
        reasons.append("the observed heat anchor did not load, so heat figures come from a fallback field")

    # A sanity check fired: the run composed into something it does not itself
    # believe. The card may still explain it, but not confidently.
    failed = data.get("sanityChecksFailed") or 0
    if failed:
        reasons.append(f"{failed} sanity check{'s' if failed != 1 else ''} fired on this run")

    # Interventions dropped before scoring: the scenario is not the one asked for.
    skipped = data.get("interventionsSkipped") or 0
    if skipped:
        reasons.append(f"{skipped} intervention{'s' if skipped != 1 else ''} could not be applied")

    # Provenance: a run standing on an undeclared surface cannot support much.
    if not data.get("terrainSource"):
        reasons.append("the terrain source is unnamed")
    if not data.get("landCoverSource"):
        reasons.append("the land-cover source is unnamed")

    if not reasons:
        return Cap(CLEAN_RUN_CAP, "this is a screening estimate, not a surveyed result")

    # Any one of the data problems is enough to make a claim rest on something
    # unverified, so the cap is Low rather than a rung per problem: the reasons
    # are reported in full, but they do not stack into "lower than Low".
    return Cap("Low", "; ".join(reasons))


def _clamp_pct(level: Level, pct: int) -> int:
    low, high = BANDS[level]
    return max(low, min(high, int(pct)))


def apply_ladder(
    proposed: Confidence | None,
    cap: Cap | None,
    *,
    ungrounded: bool = False,
) -> Confidence:
    """The model's proposal, lowered as far as the evidence requires.

    `ungrounded` is the residual state: a card was sent back once and a number
    still does not trace to the run. A card carrying a number it cannot support
    is the least confident thing this system can produce, so it goes to Low
    regardless of what the data quality said.
    """
    base = proposed or Confidence(level=CLEAN_RUN_CAP, pct=60, note="")
    note = (base.note or "").strip()

    if cap is None:
        # Unbounded: the model proposed a level and there is nothing to cap it
        # with. Leave both the level and the percentage alone -- inventing a
        # clamp here would be this module pretending to know something.
        return Confidence(level=base.level, pct=base.pct, note=note[:NOTE_MAX])

    index = min(rank(base.level), rank(cap.level))

    if ungrounded:
        index = 0

    level = _level_of(index)
    capped = level != base.level

    if capped:
        why = (
            f"Lowered to {level}: {cap.reason}."
            if rank(level) < rank(base.level)
            else f"Held at {level}: {cap.reason}."
        )
        # The model's own note is a sentence and may already end in a full stop;
        # joining on a bare space produced "the delta is clear Lowered to Medium".
        joiner = "" if not note or note.endswith((".", "!", "?")) else "."
        note = f"{note}{joiner} {why}".strip() if note else why
    elif not note:
        note = cap.reason

    return Confidence(level=level, pct=_clamp_pct(level, base.pct), note=note[:NOTE_MAX])
