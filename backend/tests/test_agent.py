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

from app.agent import confidence  # noqa: E402
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


def test_the_system_prompt_introduces_no_number_a_card_could_echo_ungrounded() -> None:
    """Any figure written into the prompt is one a model may copy into a card, and
    grounding would then reject it as "not from this run". An example like
    "-0.12 million m3" or a hard-coded site area does exactly that, so the prompt
    may contain only the small integers grounding already treats as free."""
    import re

    from app.agent.prompts import SYSTEM

    for token in re.findall(r"\d+(?:\.\d+)?", SYSTEM):
        assert "." not in token and int(token) <= 12, (
            f"SYSTEM contains {token!r}, which a model could quote and fail grounding"
        )


def test_fact_values_admits_every_rounding_of_a_fact() -> None:
    allowed = fact_values({"scenario": {"runoffM3": 1077749.54}})
    # The fact itself, its roundings at 0-4 places, and its reported scales.
    for value in (1077749.54, 1077750.0, 1077749.5, 1077.75, 1.08, 107774954.0):
        assert value in allowed, value


# --------------------------------------------------------------------------- #
# The confidence ladder: code caps the claim, not the model
# --------------------------------------------------------------------------- #

def _run_result(**overrides):
    """A run result shaped like the API's, with the data-quality block."""
    run = {
        "inputs": {"rainfallMm": 450},
        "stats": {"scenario": {"unstoredM3": 1e6}, "baseline": {}, "delta": {"unstoredM3": -5.0}},
        "sanityChecks": [],
        "sources": {"terrain": "open3dhk-tiles", "landCover": "esa-worldcover", "lst": "Landsat"},
        "interventionsApplied": [],
        "interventionsSkipped": [],
    }
    run.update(overrides)
    return run


def test_the_ladder_caps_a_clean_run_at_medium_however_sure_the_model_is() -> None:
    """The load-bearing case. A model that claims High on a clean run is not
    lying about its own certainty; it is claiming a precision this prototype
    cannot have. The cap is about the evidence, and a screening estimate with no
    error bars does not reach High."""
    facts = build_facts(_run_result())
    capped = confidence.apply_ladder(
        Confidence(level="High", pct=92, note="clear reduction"), confidence.cap_of(facts["data"])
    )
    assert capped.level == "Medium", capped.level
    assert capped.pct == 79, capped.pct  # clamped out of the High band
    assert "screening" in capped.note, capped.note


def test_the_ladder_leaves_a_modest_proposal_alone() -> None:
    facts = build_facts(_run_result())
    capped = confidence.apply_ladder(
        Confidence(level="Low", pct=30, note="one run only"), confidence.cap_of(facts["data"])
    )
    assert (capped.level, capped.pct) == ("Low", 30), capped
    assert capped.note == "one run only", "a card that was not capped gains no editorialising"


def test_a_failed_heat_anchor_drives_confidence_to_low() -> None:
    """The fallback field is real and usable, but the card must not sound as sure
    about heat as about water when the observed anchor is missing."""
    facts = build_facts(_run_result(sources={"terrain": "t", "landCover": "l", "lst": None}))
    cap = confidence.cap_of(facts["data"])
    assert cap and cap.level == "Low", cap
    assert "heat anchor" in cap.reason, cap.reason


def test_a_fired_sanity_check_drives_confidence_to_low_and_names_itself() -> None:
    facts = build_facts(_run_result(sanityChecks=[{"id": "storage-exceeds-runoff", "severity": "warning"}]))
    cap = confidence.cap_of(facts["data"])
    assert cap and cap.level == "Low", cap
    assert "sanity check" in cap.reason, cap.reason


def test_a_skipped_intervention_drives_confidence_to_low() -> None:
    facts = build_facts(_run_result(interventionsSkipped=[{"kind": "basin", "why": "outside the site"}]))
    cap = confidence.cap_of(facts["data"])
    assert cap and cap.level == "Low", cap
    assert "could not be applied" in cap.reason, cap.reason


def test_an_unnamed_data_source_drives_confidence_to_low() -> None:
    """Provenance is not decoration: if the run cannot say what surface it stood
    on, nothing it reports rests on anything nameable."""
    facts = build_facts(_run_result(sources={}))
    cap = confidence.cap_of(facts["data"])
    assert cap and cap.level == "Low", cap
    assert "terrain source is unnamed" in cap.reason, cap.reason


def test_a_missing_data_block_is_treated_as_unknown_not_as_clean() -> None:
    """An older client sends no `sources` and no `sanityChecks`. Absent must not
    read as "nothing was wrong"."""
    facts = build_facts({"inputs": {}, "stats": {}})
    cap = confidence.cap_of(facts["data"])
    assert cap and cap.level == "Low", cap
    assert "unnamed" in cap.reason, cap.reason


def test_no_data_at_all_still_produces_a_cap_rather_than_a_free_pass() -> None:
    cap = confidence.cap_of({})
    assert cap is not None and cap.level == "Medium", cap


def test_a_residual_ungrounded_number_is_low_whatever_the_data_said() -> None:
    """A card carrying a number the run did not produce is the least trustworthy
    thing this system emits, so it cannot sit at Medium because the run was clean."""
    facts = build_facts(_run_result())
    capped = confidence.apply_ladder(
        Confidence(level="High", pct=90, note="clear"), confidence.cap_of(facts["data"]),
        ungrounded=True,
    )
    assert capped.level == "Low", capped.level
    assert capped.pct <= 50, capped.pct


def test_the_ladder_never_raises_a_level() -> None:
    """Lowering only. A model that proposed Low on a clean run stays Low: code
    caps claims, it does not manufacture confidence for a hesitant model."""
    facts = build_facts(_run_result())
    capped = confidence.apply_ladder(
        Confidence(level="Low", pct=45, note="hedged"), confidence.cap_of(facts["data"])
    )
    assert capped.level == "Low", capped.level


def test_an_ungrounded_flag_cannot_raise_a_low_either() -> None:
    cards = [
        confidence.apply_ladder(Confidence(level=lv, pct=p, note=""), confidence.cap_of({}), ungrounded=True)
        for lv, p in (("High", 95), ("Medium", 60), ("Low", 40))
    ]
    assert [c.level for c in cards] == ["Low", "Low", "Low"], cards


def test_the_ladder_is_idempotent_so_re_capping_is_safe() -> None:
    """The stream path re-caps after the revision loop. Applying twice must not
    walk the level down twice."""
    facts = build_facts(_run_result(sanityChecks=[{"id": "cn-out-of-range"}]))
    cap = confidence.cap_of(facts["data"])
    once = confidence.apply_ladder(Confidence(level="High", pct=90, note="n"), cap)
    twice = confidence.apply_ladder(once, cap)
    assert once.level == twice.level == "Low", (once.level, twice.level)


def test_every_level_owns_a_band_and_the_bands_do_not_overlap() -> None:
    """A "High, 40 %" card is a contradiction the reader has to resolve. The
    percentage is clamped so the pair always agrees."""
    seen: list[tuple[int, int]] = []
    for level, (low, high) in confidence.BANDS.items():
        assert low <= high, level
        seen.append((low, high))
    seen.sort()
    for (_, prev_high), (next_low, _) in zip(seen, seen[1:]):
        assert prev_high < next_low, f"bands overlap: {prev_high} >= {next_low}"


def test_a_clamped_percentage_lands_inside_the_surviving_bands_own_range() -> None:
    for level in ("Low", "Medium", "High"):
        low, high = confidence.BANDS[level]
        for proposed in (0, 50, 51, 99, 100):
            got = confidence._clamp_pct(level, proposed)
            assert low <= got <= high, (level, proposed, got)


def test_the_data_block_is_not_quoted_as_a_number_in_the_facts_text() -> None:
    """`render_facts` renders nested dicts as numbers. The data block carries a
    *count* of fired checks; if it leaked into the numeric block a model could
    quote it and the reader could not tell it from a measurement."""
    from app.agent.prompts import render_facts

    facts = build_facts(_run_result(sanityChecks=[{"id": "a"}, {"id": "b"}]))
    rendered = render_facts(facts)
    assert "[DATA QUALITY]" in rendered, rendered
    assert "sanityChecksFailed:" not in rendered, "the raw count must not render as a fact line"


def test_the_prompt_still_introduces_no_echoable_number() -> None:
    """The DATA QUALITY block is new prose in the facts text, so re-check the
    render for figures a model could copy into a card and fail grounding on."""
    import re

    from app.agent.prompts import render_facts

    facts = build_facts(_run_result())
    body = render_facts(facts)
    # Only the prompt's own prose is at risk; the fact lines are the run's own
    # numbers, which grounding will accept. So check just the added section.
    quality = body.split("[DATA QUALITY]")[1]
    for token in re.findall(r"\d+(?:\.\d+)?", quality):
        assert "." not in token and int(token) <= 12, (
            f"the data-quality block contains {token!r}, which a card could echo ungrounded"
        )


def test_a_card_with_no_run_in_scope_says_that_and_not_no_model_configured() -> None:
    """The no-run card is assembled by `template_card`, whose default note blames
    a missing language model. That is the wrong reason here -- a model may be
    configured and reachable and this card would look identical -- so the note has
    to name the actual cause."""
    from app.agent.chat_service import answer, stream_events

    card = answer("what about cost?", {})
    assert card.kind == "general", card.kind
    assert "no run in scope" in card.confidence.note.lower(), card.confidence.note
    assert "language model is configured" not in card.confidence.note, card.confidence.note

    events = list(stream_events("what about cost?", {}))
    assert sum(1 for e in events if e["event"] == "done") == 1, events
    assert events[-2]["answer"]["kind"] == "general", events[-2]


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
