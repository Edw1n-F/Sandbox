"""Routing tests: a proposal-shaped instruction must reach the gate, with no model.

DESIGN §2.5 names four causes for "some outputs that should be intervention
proposals are still being returned as result cards". The one that dominates is
R1: with no model key configured the card's `intent` stays at its safe default
`"question"`, so nothing routes. The load-bearing case here is exactly that --
**with no provider configured**, an intervention phrasing still yields
`intent="intervention"` and an `intent_hint` the client can open the gate with.

R2 (the client vocabulary is smaller than the server's) is covered by asserting
the design's verbs -- `dig`, `create`, `grow`, `retain` -- all route. R3 (the
`FIT_TOOL` drop) and R4 (`route_tools` precedence) are `ai_plan.py` changes owned
by T02, not this file.

Everything is deterministic: no model, no network, no clock. The classifier is a
pure function over the sentence, so each corpus entry is asserted directly.

Run:  cd backend && ./.venv/bin/python tests/test_intent_routing.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.agent import intent as intent_mod  # noqa: E402
from app.agent.answer import AnswerCard, Confidence  # noqa: E402
from app.agent.chat_service import answer  # noqa: E402
from app.agent import chat_service as cs  # noqa: E402
from app.agent import llm  # noqa: E402

classify_turn = intent_mod.classify_turn
parse_intervention_intent = intent_mod.parse_intervention_intent


# --------------------------------------------------------------------------- #
# Corpora
# --------------------------------------------------------------------------- #

#: ≥ 12 intervention phrasings that must route. Includes the design's verbs --
#: `dig`, `create`, `grow`, `retain` -- which the client's 12-verb regex missed.
INTERVENTION_CORPUS: tuple[str, ...] = (
    "add a water reservoir in the north",
    "place a green space in the centre",
    "create permeable paving across the whole area",
    "build a retention basin in the south",
    "dig a retention basin in the north",
    "grow a green space in the east",
    "fill a retention basin in the south",
    "retain water in a reservoir in the north",
    "install permeable paving in the centre",
    "convert the road to permeable paving in the north",
    "retrofit drainage in the south",
    "expand the green space in the north",
    "increase green space in the west",
    "put a water reservoir in the east",
    "remove the drainage in the north",
    "delete the road in the south",
    "drop the green space in the west",
    "take out the permeable paving in the centre",
)

#: Question phrasings that must stay `intent="question"`.
QUESTION_CORPUS: tuple[str, ...] = (
    "what changed?",
    "how much runoff was stored?",
    "why did the buffer overflow?",
    "which project helps most?",
    "is this better than the baseline?",
    "what is the wet area?",
    "does the project reduce flooding?",
    "how many interventions were applied?",
    "where is the deepest water?",
    "who decided the baseline?",
)

#: Massing edits are changes, but not proposals (DESIGN R4): they stay questions.
MASSING_CORPUS: tuple[str, ...] = (
    "make the district 25% taller",
    "make the blocks shorter",
    "reset the building heights",
)


def _card(intent: str = "question", hint: str | None = None) -> AnswerCard:
    """A code-assembled card, exactly as the no-model path builds one."""
    return AnswerCard(
        title="t",
        sentence="s",
        confidence=Confidence(level="Low", pct=30, note="n"),
        source="template",
        intent=intent,  # type: ignore[arg-type]
        intent_hint=hint,
    )


# --------------------------------------------------------------------------- #
# The corpus: intervention phrasings route
# --------------------------------------------------------------------------- #

def test_every_intervention_phrasing_routes() -> None:
    assert len(INTERVENTION_CORPUS) >= 12, len(INTERVENTION_CORPUS)
    for phrase in INTERVENTION_CORPUS:
        out = classify_turn(phrase, _card())
        assert out.intent == "intervention", (phrase, out.intent)
        assert out.intent_source == "parser", (phrase, out.intent_source)
        assert out.intent_hint, (phrase, "no hint")


def test_the_designs_verbs_are_all_covered() -> None:
    """`dig`/`create`/`grow`/`retain` -- the ones the old client regex dropped."""
    for verb in ("dig", "create", "grow", "retain"):
        assert verb in intent_mod.INTERVENTION_VERBS, verb
        assert any(verb in p for p in INTERVENTION_CORPUS), verb


def test_a_question_phrasing_stays_a_question() -> None:
    for phrase in QUESTION_CORPUS:
        out = classify_turn(phrase, _card())
        assert out.intent == "question", (phrase, out.intent)
        assert out.intent_hint is None, (phrase, out.intent_hint)
        assert out.intent_source == "parser", (phrase, out.intent_source)


def test_a_massing_edit_is_not_a_proposal() -> None:
    for phrase in MASSING_CORPUS:
        out = classify_turn(phrase, _card())
        assert out.intent == "question", (phrase, out.intent)


def test_a_vague_turn_is_ambiguous() -> None:
    out = classify_turn("make it nicer", _card())
    assert out.intent == "ambiguous", out.intent
    assert out.intent_source == "parser", out.intent_source


def test_a_verb_without_a_type_does_not_route() -> None:
    """"add a bike lane" names no proposal type, so it is not a proposal."""
    out = classify_turn("add a bike lane", _card())
    assert out.intent != "intervention", out.intent


def test_the_hint_uses_the_prompt_shape() -> None:
    """Every hint is `add <type> in <region>` or `remove the <type> near <region>`."""
    for phrase in INTERVENTION_CORPUS:
        hint = parse_intervention_intent(phrase)
        assert hint, phrase
        shaped = (hint.startswith("add ") and " in " in hint) or (
            hint.startswith("remove the ") and " near " in hint
        )
        assert shaped, (phrase, hint)


def test_the_classifier_is_deterministic() -> None:
    a = classify_turn("dig a retention basin in the north", _card())
    b = classify_turn("dig a retention basin in the north", _card())
    assert (a.intent, a.intent_hint, a.intent_source) == (
        b.intent, b.intent_hint, b.intent_source
    ), "classification differs between identical calls"


# --------------------------------------------------------------------------- #
# Precedence: the model can never lower the intent
# --------------------------------------------------------------------------- #

def test_parser_and_model_agreeing_records_both() -> None:
    card = _card(intent="intervention", hint="add a water reservoir in the north")
    out = classify_turn("dig a retention basin in the north", card)
    assert out.intent == "intervention", out.intent
    assert out.intent_source == "both", out.intent_source


def test_a_model_only_intervention_is_kept() -> None:
    """"what changed?" parses to nothing, so the model's intent stands."""
    card = _card(intent="intervention", hint="add green space in the east")
    out = classify_turn("what changed?", card)
    assert out.intent == "intervention", out.intent
    assert out.intent_source == "model", out.intent_source
    assert out.intent_hint == "add green space in the east", out.intent_hint


# --------------------------------------------------------------------------- #
# The load-bearing case: no provider configured (R1)
# --------------------------------------------------------------------------- #

def test_no_run_but_an_intervention_still_routes() -> None:
    """The no-run branch is the one that matters with no key (DESIGN R1)."""
    events = list(cs.stream_events("add a green space in the centre", {}))
    card = next(e["answer"] for e in events if e["event"] == "answer")
    assert card["intent"] == "intervention", card["intent"]
    assert card["intent_source"] == "parser", card["intent_source"]
    assert card["intent_hint"] == "add green space in the centre", card["intent_hint"]


def test_no_provider_still_routes_an_intervention() -> None:
    """With the provider unavailable the card is code-assembled -- and must route.

    `_draft` is forced to raise `ProviderUnavailable` (what a missing key does)
    and `template_card` is stubbed so the test does not depend on the run-card
    text. What is asserted is the wiring: `stream_events` classifies the card it
    is about to emit.
    """
    original_draft = cs._draft
    original_template = cs.template_card

    def _unavailable(*_args, **_kwargs):
        raise llm.ProviderUnavailable("no key configured")

    def _template(_facts):
        return _card()

    cs._draft = _unavailable  # type: ignore[assignment]
    cs.template_card = _template  # type: ignore[assignment]
    try:
        events = list(cs.stream_events("dig a retention basin in the north", {"scenario": {}}))
    finally:
        cs._draft = original_draft  # type: ignore[assignment]
        cs.template_card = original_template  # type: ignore[assignment]

    card = next(e["answer"] for e in events if e["event"] == "answer")
    assert card["intent"] == "intervention", card["intent"]
    assert card["intent_source"] == "parser", card["intent_source"]
    assert card["intent_hint"] == "add a water reservoir in the north", card["intent_hint"]


def test_the_non_streaming_answer_routes_too() -> None:
    card = answer("dig a retention basin in the north", {})
    assert card.intent == "intervention", card.intent
    assert card.intent_hint == "add a water reservoir in the north", card.intent_hint


# --------------------------------------------------------------------------- #
# The fixture is the one vocabulary: pin it back to the backend
# --------------------------------------------------------------------------- #

def test_the_fixture_matches_the_backend_vocabulary() -> None:
    path = Path(__file__).resolve().parents[2] / "contracts" / "intent_vocabulary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["interventionVerbs"] == list(intent_mod.INTERVENTION_VERBS), \
        "contracts/intent_vocabulary.json is stale: interventionVerbs"
    assert data["removalVerbs"] == list(intent_mod.REMOVAL_VERBS), \
        "contracts/intent_vocabulary.json is stale: removalVerbs"
    assert data["nouns"] == intent_mod.NOUNS, \
        "contracts/intent_vocabulary.json is stale: nouns"
    assert data["kinds"] == intent_mod.KIND_WORD, \
        "contracts/intent_vocabulary.json is stale: kinds"
    assert data["regions"] == intent_mod.REGION_WORDS, \
        "contracts/intent_vocabulary.json is stale: regions"


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
