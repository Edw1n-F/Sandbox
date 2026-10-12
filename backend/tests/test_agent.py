"""The agent's pure half: number grounding and fact assembly.

`app/agent/` had no test coverage at all (STATUS gap 9) -- it was verified by
hand against a live key. The LLM call needs a key and a network, but the two
pieces that decide whether an answer is *trustworthy* do not: `grounding.py` is
pure functions, and `build_facts` is a dict rebuild. Those are the pieces a
regression would hurt most, so they are tested here with no key and no network.

The load-bearing case is `test_a_derived_share_grounds_only_when_the_run_supplies_it`:
a card reading "95 % of runoff is unstored" used to be reported as ungrounded,
because 95 is not a number the run produced -- it is a ratio of two. The fix is
deliberately *not* a looser check: the run now publishes the
shares it will stand behind, so grounding still only accepts facts.

Run:  python tests/test_agent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.agent.answer import AnswerCard, Confidence, StatItem  # noqa: E402
from app.agent.chat_service import build_facts  # noqa: E402
from app.agent.grounding import (  # noqa: E402
    check_card,
    extract_numbers,
    fact_values,
)


def _card(**overrides) -> AnswerCard:
    base = dict(
        title="The buffer still overflows",
        sentence="Runoff exceeds storage.",
        confidence=Confidence(level="High", pct=90, note="from the ledger"),
    )
    base.update(overrides)
    return AnswerCard(**base)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def test_numbers_are_extracted_with_their_written_decimals() -> None:
    got = extract_numbers("1,077,749.54 m³ and 95 % and 2 projects")
    assert ("1,077,749.54", 1077749.54, 2) in got, got
    assert ("95", 95.0, 0) in got, got
    assert ("2", 2.0, 0) in got, got


def test_free_integers_and_years_need_no_source() -> None:
    card = _card(sentence="2 projects were active in 2026.")
    assert check_card(card, {}) == [], check_card(card, {})


# --------------------------------------------------------------------------- #
# The core rule: every other number must trace back
# --------------------------------------------------------------------------- #

def test_a_made_up_number_is_reported() -> None:
    card = _card(sentence="Runoff is about 999,999 m³.")
    problems = check_card(card, {})
    assert len(problems) == 1, problems
    assert "999,999" in problems[0], problems


def test_rounding_and_scaling_are_accepted() -> None:
    facts = {"scenario": {"runoffM3": 1077749.54}}
    for text in ("Runoff is 1,077,749.54 m³.",
                 "Runoff is about 1.08 million m³."):
        problems = check_card(_card(sentence=text), facts)
        assert problems == [], (text, problems)


def test_stats_and_caveats_are_checked_too() -> None:
    card = _card(
        stats=[StatItem(l="Unstored", v="40,000 m³")],
        caveats=["Assumes 12 mm of depression storage."],
    )
    problems = check_card(card, {"scenario": {"unstoredM3": 1234.0}})
    joined = " | ".join(problems)
    assert "stats[0].v" in joined, joined
    assert "40,000" in joined, joined


# --------------------------------------------------------------------------- #
# A derived share, and why the fix is not a looser check
# --------------------------------------------------------------------------- #

def test_a_derived_share_grounds_only_when_the_run_supplies_it() -> None:
    card = _card(sentence="95 % of runoff has nowhere to go.")

    # Without the run's own ratio, 95 is a number nobody produced -> flagged.
    # (The fixture is chosen so 95 is *not* reachable by scaling: 0.5 x 100 = 50
    # and 2.0 x 100 = 200, neither of which is 95.)
    plain = {"scenario": {"runoffM3": 2.0, "unstoredM3": 0.5}}
    without = check_card(card, plain)
    assert len(without) == 1 and "95" in without[0], without

    # With the ratio published by the run, it is a fact like any other.
    with_ratio = check_card(card, {"ratios": {"unstoredOverRunoff": 0.95}})
    assert with_ratio == [], with_ratio


def test_a_ratio_is_accepted_in_both_spellings() -> None:
    facts = {"ratios": {"unstoredOverRunoff": 0.95}}
    for text in ("95 % is unstored.", "A share of 0.95 is unstored."):
        problems = check_card(_card(sentence=text), facts)
        assert problems == [], (text, problems)


# --------------------------------------------------------------------------- #
# build_facts forwards; it never computes
# --------------------------------------------------------------------------- #

def test_build_facts_is_empty_without_a_run() -> None:
    assert build_facts(None) == {}
    assert build_facts({}) == {}


def test_build_facts_forwards_the_run_verbatim() -> None:
    stats = {
        "scenario": {"unstoredM3": 1.08e6},
        "baseline": {"unstoredM3": 1.08e6},
        "delta": {"unstoredM3": 0.0},
        "ratios": {"unstoredOverRunoff": 0.952381},
    }
    run = {
        "inputs": {"rainfallMm": 450},
        "stats": stats,
        "interventionsApplied": [{"id": "a"}],
        "interventionsSkipped": [],
    }
    facts = build_facts(run)
    assert facts["scenario"] == stats["scenario"]
    assert facts["ratios"] == stats["ratios"], "ratios must be forwarded, not recomputed"
    assert facts["inputs"] == {"rainfallMm": 450}
    assert facts["applied"] == [{"id": "a"}]
    # A ratio the run did not publish is not invented here.
    assert set(facts["ratios"]) == {"unstoredOverRunoff"}


def test_fact_values_admits_every_rounding_of_a_fact() -> None:
    allowed = fact_values({"scenario": {"runoffM3": 1077749.54}})
    # The fact itself, its roundings at 0-4 places, and its reported scales.
    for value in (1077749.54, 1077750.0, 1077749.5, 1077.75, 1.08, 107774954.0):
        assert value in allowed, value


# --------------------------------------------------------------------------- #

def _run() -> int:
    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
