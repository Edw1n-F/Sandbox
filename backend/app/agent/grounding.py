"""Number grounding: every number in an answer must come from the run.

The rule, taken from Constellation's `validate.py`: a number in the title,
sentence, cause, todo, stats or caveats must appear in the run's own outputs,
with rounding to the decimals written. Anything else is a number the model
made up, and a made-up number in a planning tool is worse than no number at
all -- it looks like a measurement.

What is free (also as in Constellation): small integers 0-12 (counts, "two
projects", step numbers) and years. Everything else has to trace back.

The check is deliberately mechanical and its verdict is surfaced in the card's
`grounding` field rather than silently enforced: a planner who sees
"ungrounded: 40,000 m³" knows to distrust that line.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from app.agent.answer import AnswerCard

__all__ = ["extract_numbers", "fact_values", "check_card", "texts_of"]

#: Digits only: 1, 1.5, 1,234.5. Spelled-out numbers are rejected by the prompt.
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

FREE_INT_MAX = 12
YEAR_MIN = 1900
YEAR_MAX = 2100


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _decimals(raw: str) -> int:
    return len(raw.split(".")[1]) if "." in raw else 0


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """(raw, value, decimals written) for every number in `text`."""
    return [(m.group(0), _to_float(m.group(0)), _decimals(m.group(0)))
            for m in NUMBER_RE.finditer(text or "")]


def _is_free(value: float, raw: str) -> bool:
    """Small integers and years carry no measurement, so they need no source."""
    if "." in raw or "," in raw:
        return False
    if YEAR_MIN <= value <= YEAR_MAX:
        return True
    return 0 <= value <= FREE_INT_MAX and value.is_integer()


def _walk(node: Any) -> Iterable[float]:
    """Every number in a nested stats/results dict."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk(value)


def fact_values(facts: dict[str, Any]) -> set[float]:
    """The numbers a model is allowed to write, with the roundings it may use.

    A run reports runoff in cubic metres (1,077,749.54) and a card reads better
    in millions (1.08), so each fact is accepted at every plausible scale and
    rounding. Without this the check would reject an honestly-rounded number and
    the revision loop would fight the model over formatting.
    """
    out: set[float] = set()
    for value in _walk(facts):
        if not math.isfinite(value):
            continue
        for scaled in (value, value / 1e3, value / 1e6, value * 100, abs(value)):
            for decimals in range(0, 5):
                out.add(round(scaled, decimals))
            out.add(scaled)
    return out


def _grounded(value: float, decimals: int, allowed: set[float]) -> bool:
    """True when `value` is some allowed fact rounded to `decimals` places."""
    for candidate in allowed:
        if abs(round(candidate, decimals) - value) <= max(0.5 * 10 ** -decimals, 1e-9):
            return True
    return False


def texts_of(card: AnswerCard) -> list[tuple[str, str]]:
    """(field name, text) for every field a number may appear in."""
    pairs: list[tuple[str, str]] = [
        ("title", card.title),
        ("sentence", card.sentence),
        ("cause", card.cause or ""),
        ("todo", card.todo or ""),
    ]
    pairs += [(f"caveats[{i}]", c) for i, c in enumerate(card.caveats)]
    for i, stat in enumerate(card.stats):
        pairs.append((f"stats[{i}].v", stat.v))
        if stat.ci:
            pairs.append((f"stats[{i}].ci", stat.ci))
    return pairs


def check_card(card: AnswerCard, facts: dict[str, Any]) -> list[str]:
    """Numbers in the card that the run does not support, each explained.

    Empty means every number traces back. The message is written for a reader,
    not for the model -- it is shown in the UI as well as fed back once.
    """
    allowed = fact_values(facts)
    problems: list[str] = []
    for field_name, text in texts_of(card):
        for raw, value, decimals in extract_numbers(text):
            if _is_free(value, raw):
                continue
            if _grounded(value, decimals, allowed):
                continue
            problems.append(
                f"{field_name}: '{raw}' is not a number this run produced "
                f"(nearest allowed value is not within {decimals} decimals)"
            )
    return problems
