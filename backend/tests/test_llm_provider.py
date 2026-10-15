"""The chat, end to end, with no key and no network.

`test_agent.py` covers the pure half (grounding, facts, the confidence ladder).
This file covers the half that used to need a live model: the draft -> ground ->
revise loop in `chat_service`, and the failure paths free-tier models actually
take. It drives the real `answer()` and `stream_events()` through
`FakeProvider` (app/agent/fake_provider.py), installed with `llm.use_provider`.

The last section is a regression check on the *real* providers: `_post` /
`_post_stream` are swapped for canned responses, so the request payloads the
Anthropic and OpenAI-compatible classes build are asserted without a socket.
That is what proves moving the code behind the `Provider` protocol did not
change what goes over the wire.

Run:  PYTHONUTF8=1 python tests/test_llm_provider.py
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.agent import chat_service, llm  # noqa: E402
from app.agent.fake_provider import FakeProvider  # noqa: E402
from app.agent.llm import LLMError, ProviderUnavailable  # noqa: E402
from app.config import settings  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

#: A clean run (every source named, no sanity check fired) -- so the ladder
#: caps at Medium, and an ungrounded residual drops it to Low.
RUN = {
    "inputs": {"rainfallMm": 450},
    "stats": {
        "scenario": {"unstoredM3": 1077749.54, "wetAreaHa": 42.3},
        "baseline": {"unstoredM3": 1082749.54, "wetAreaHa": 42.3},
        "delta": {"unstoredM3": -5000.0, "wetAreaHa": 0.0},
    },
    "sanityChecks": [],
    "sources": {"terrain": "open3dhk-tiles", "landCover": "esa-worldcover", "lst": "Landsat"},
    "interventionsApplied": [],
    "interventionsSkipped": [],
}
FACTS = chat_service.build_facts(RUN)
QUESTION = "Does the buffer hold the storm?"


def _card(sentence: str = "About 1.08 million m³ of runoff is still unstored.") -> dict:
    return {
        "title": "The buffer still overflows",
        "sentence": sentence,
        "cause": "Storage is small next to the runoff.",
        "todo": "Add storage upstream.",
        "stats": [{"l": "Unstored runoff", "v": "1.08 million m³"}],
        "confidence": {"level": "High", "pct": 92, "note": "the delta is clear"},
        "caveats": [],
        "followups": [],
    }


GROUNDED = _card()
BAD_NUMBER = "999,999"
UNGROUNDED = _card(f"About {BAD_NUMBER} m³ of runoff is still unstored.")


def _events(script: list) -> tuple[list[dict], FakeProvider]:
    fake = FakeProvider(script)
    with llm.use_provider(fake):
        events = list(chat_service.stream_events(QUESTION, FACTS))
    return events, fake


def _kinds(events: list[dict]) -> list[str]:
    return [e["event"] for e in events]


def _step_results(events: list[dict]) -> dict[int, str]:
    return {e["index"]: e["result"] for e in events if e["event"] == "step_finished"}


def _answer(events: list[dict]) -> dict:
    answers = [e["answer"] for e in events if e["event"] == "answer"]
    assert len(answers) == 1, answers
    return answers[0]


def _assert_well_formed(events: list[dict]) -> None:
    """Every stream: one run_started first, three steps, one answer, one done last."""
    kinds = _kinds(events)
    assert kinds[0] == "run_started", kinds
    assert kinds[-1] == "done" and kinds.count("done") == 1, kinds
    assert kinds[-2] == "answer" and kinds.count("answer") == 1, kinds
    assert kinds.count("step_finished") == 3, kinds
    assert sorted(_step_results(events)) == [0, 1, 2], kinds


@contextmanager
def _patched(**values):
    """Set settings attributes for a block, restoring the originals in finally."""
    saved = {k: getattr(settings, k) for k in values}
    try:
        for k, v in values.items():
            setattr(settings, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


NO_PROVIDER = dict(anthropic_api_key="", llm_base_url="", llm_model="", llm_api_key="",
                   llm_fallback_models="", llm_json_mode="auto")
OPENAI = dict(NO_PROVIDER, llm_base_url="https://llm.example/v1", llm_model="m-primary",
              llm_api_key="k-test")
ANTHROPIC = dict(NO_PROVIDER, anthropic_api_key="sk-test", anthropic_model="claude-test")


# --------------------------------------------------------------------------- #
# 1. Happy path, streamed
# --------------------------------------------------------------------------- #

def test_a_streamed_grounded_card_forwards_every_delta_in_order() -> None:
    raw = json.dumps(GROUNDED)
    deltas = [raw[:20], raw[20:60], raw[60:]]
    events, fake = _events([deltas])
    _assert_well_formed(events)

    kinds = _kinds(events)
    forwarded = [e["text"] for e in events if e["event"] == "delta"]
    assert forwarded == deltas, forwarded
    # Deltas sit inside step 1: after it starts, before it finishes.
    first_delta = kinds.index("delta")
    step1_start = next(i for i, e in enumerate(events)
                       if e["event"] == "step_started" and e["index"] == 1)
    step1_end = next(i for i, e in enumerate(events)
                     if e["event"] == "step_finished" and e["index"] == 1)
    assert step1_start < first_delta < step1_end, kinds
    assert "error" not in kinds, kinds

    card = _answer(events)
    assert card["source"] == "model", card
    assert card["grounding"] == {"ok": True, "ungrounded": [], "revised": False}, card["grounding"]
    assert card["confidence"]["level"] == "Medium", "a clean run caps at Medium"
    assert _step_results(events)[1] == "card drafted", _step_results(events)
    assert [c["method"] for c in fake.calls] == ["stream_text"], fake.calls
    assert fake.calls[0]["max_tokens"] == chat_service.MAX_TOKENS, fake.calls[0]


# --------------------------------------------------------------------------- #
# 2-3. The stream fails; one unstreamed retry recovers it
# --------------------------------------------------------------------------- #

def test_a_rejected_stream_is_retried_once_without_streaming() -> None:
    for exc in (LLMError("provider HTTP 400"), LLMError("provider HTTP 429", retryable=True)):
        events, fake = _events([exc, GROUNDED])
        _assert_well_formed(events)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1 and errors[0]["message"] == exc.message, errors
        assert errors[0]["recoverable"] is exc.retryable, errors
        assert _step_results(events)[1] == "card drafted (unstreamed)", _step_results(events)
        assert [c["method"] for c in fake.calls] == ["stream_text", "complete_json"], fake.calls
        assert _answer(events)["source"] == "model"
        assert fake.remaining == 0


def test_a_stream_that_breaks_midway_is_retried_without_streaming() -> None:
    raw = json.dumps(GROUNDED)
    events, fake = _events([[raw[:15], LLMError("provider unreachable: reset", retryable=True)],
                            GROUNDED])
    _assert_well_formed(events)
    assert _step_results(events)[1] == "card drafted (unstreamed)", _step_results(events)
    assert [c["method"] for c in fake.calls] == ["stream_text", "complete_json"], fake.calls


def test_a_stream_with_zero_deltas_reports_it_then_recovers() -> None:
    """The reasoning-channel failure: a 200 stream with no content at all."""
    events, fake = _events([[], GROUNDED])
    _assert_well_formed(events)
    errors = [e for e in events if e["event"] == "error"]
    assert len(errors) == 1 and "no card on the stream" in errors[0]["message"], errors
    assert "delta" not in _kinds(events)
    assert _step_results(events)[1] == "card drafted (unstreamed)", _step_results(events)
    assert _answer(events)["source"] == "model"
    assert [c["method"] for c in fake.calls] == ["stream_text", "complete_json"], fake.calls


def test_a_stream_and_retry_that_both_return_nothing_fall_to_the_template() -> None:
    events, _ = _events([[], "{}"])
    _assert_well_formed(events)
    assert _step_results(events)[1] == "no card returned", _step_results(events)
    assert _answer(events)["source"] == "template"


# --------------------------------------------------------------------------- #
# 4. Grounding and the one revision
# --------------------------------------------------------------------------- #

def test_an_ungrounded_number_is_sent_back_once_by_name_and_fixed() -> None:
    events, fake = _events([[json.dumps(UNGROUNDED)], GROUNDED])
    _assert_well_formed(events)

    grounding = [e for e in events if e["event"] == "grounding"]
    assert [g["revised"] for g in grounding] == [False, True], grounding
    assert grounding[0]["ok"] is False and BAD_NUMBER in " ".join(grounding[0]["ungrounded"])
    assert grounding[1]["ok"] is True, grounding[1]

    # The revision is a non-streamed call whose user turn names the bad number.
    assert [c["method"] for c in fake.calls] == ["stream_text", "complete_json"], fake.calls
    retry_turn = fake.calls[1]["messages"][-1].text
    assert BAD_NUMBER in retry_turn and "did not produce" in retry_turn, retry_turn

    card = _answer(events)
    assert card["grounding"] == {"ok": True, "ungrounded": [], "revised": True}, card["grounding"]
    assert card["confidence"]["level"] == "Medium", card["confidence"]
    assert _step_results(events)[2] == "all numbers trace to the run"


def test_a_number_still_ungrounded_after_revision_ends_low() -> None:
    events, fake = _events([[json.dumps(UNGROUNDED)], UNGROUNDED])
    _assert_well_formed(events)
    card = _answer(events)
    assert card["grounding"]["ok"] is False and card["grounding"]["revised"] is True, card
    assert card["confidence"]["level"] == "Low", card["confidence"]
    assert _step_results(events)[2] == "1 ungrounded", _step_results(events)
    assert fake.remaining == 0


def test_a_revision_that_errors_keeps_the_first_card_and_reports_it_low() -> None:
    events, _ = _events([[json.dumps(UNGROUNDED)], LLMError("provider HTTP 429", retryable=True)])
    _assert_well_formed(events)
    card = _answer(events)
    assert card["source"] == "model" and card["grounding"]["revised"] is False, card
    assert card["confidence"]["level"] == "Low", card["confidence"]


# --------------------------------------------------------------------------- #
# 5. No provider at all
# --------------------------------------------------------------------------- #

def test_no_provider_streams_a_labelled_template_card() -> None:
    events, fake = _events([ProviderUnavailable("no LLM provider configured")])
    _assert_well_formed(events)
    errors = [e for e in events if e["event"] == "error"]
    assert len(errors) == 1 and errors[0]["recoverable"] is True, errors
    assert _step_results(events)[1] == "no model configured — template", _step_results(events)
    assert _step_results(events)[2] == "template card — no check needed"
    assert _answer(events)["source"] == "template"
    assert len(fake.calls) == 1, "no retry when nothing is configured"


def test_no_provider_from_settings_streams_the_template_too() -> None:
    """The same path, reached the real way: nothing configured, no override."""
    with _patched(**NO_PROVIDER):
        events = list(chat_service.stream_events(QUESTION, FACTS))
    _assert_well_formed(events)
    assert _answer(events)["source"] == "template"
    assert _step_results(events)[1] == "no model configured — template"


# --------------------------------------------------------------------------- #
# 6. answer(): the non-streaming path, same revision logic
# --------------------------------------------------------------------------- #

def test_answer_revises_once_and_names_the_bad_number() -> None:
    fake = FakeProvider([UNGROUNDED, GROUNDED])
    with llm.use_provider(fake):
        card = chat_service.answer(QUESTION, FACTS)
    assert [c["method"] for c in fake.calls] == ["complete_json", "complete_json"], fake.calls
    # The forced-schema path: the card schema travels with the call.
    assert fake.calls[0]["schema"] is chat_service.ANSWER_SCHEMA
    assert BAD_NUMBER in fake.calls[1]["messages"][-1].text, fake.calls[1]
    assert card.source == "model" and card.grounding and card.grounding.ok, card
    assert card.grounding.revised is True and card.confidence.level == "Medium", card


def test_answer_keeps_a_residual_ungrounded_number_at_low() -> None:
    fake = FakeProvider([UNGROUNDED, UNGROUNDED])
    with llm.use_provider(fake):
        card = chat_service.answer(QUESTION, FACTS)
    assert card.grounding and not card.grounding.ok and card.grounding.revised, card.grounding
    assert card.confidence.level == "Low", card.confidence


def test_answer_takes_the_json_out_of_prose() -> None:
    """The outermost-{...} repair is in the public layer, so it covers the fake too."""
    fake = FakeProvider([f"Here is the card:\n{json.dumps(GROUNDED)}\nHope that helps."])
    with llm.use_provider(fake):
        card = chat_service.answer(QUESTION, FACTS)
    assert card.source == "model" and card.grounding and card.grounding.ok, card


def test_answer_falls_to_the_template_on_any_provider_failure() -> None:
    for exc in (ProviderUnavailable("none"), LLMError("provider HTTP 500", retryable=True)):
        fake = FakeProvider([exc])
        with llm.use_provider(fake):
            card = chat_service.answer(QUESTION, FACTS)
        assert card.source == "template" and card.grounding is None, card
        assert fake.remaining == 0


# --------------------------------------------------------------------------- #
# 7-9. Resolution, the override seam, and the fake's own strictness
# --------------------------------------------------------------------------- #

def test_anthropic_wins_over_an_openai_setup_and_nothing_raises() -> None:
    with _patched(**dict(OPENAI, anthropic_api_key="sk-test")):
        assert isinstance(llm.get_provider(), llm.AnthropicProvider)
        assert llm.provider_name() == "anthropic"
    with _patched(**OPENAI):
        assert isinstance(llm.get_provider(), llm.OpenAICompatibleProvider)
        assert llm.provider_name() == "openai-compatible"
    with _patched(**dict(OPENAI, llm_model="")):  # a base URL alone is not a provider
        assert llm.provider_name() == "none"
    with _patched(**NO_PROVIDER):
        assert llm.provider_name() == "none"
        try:
            llm.get_provider()
        except ProviderUnavailable:
            pass
        else:
            raise AssertionError("no settings must raise ProviderUnavailable")


def test_every_real_provider_satisfies_the_protocol() -> None:
    for p in (llm.AnthropicProvider(), llm.OpenAICompatibleProvider(), FakeProvider([])):
        assert isinstance(p, llm.Provider), p


def test_the_override_wins_and_does_not_leak() -> None:
    with _patched(**OPENAI):
        fake = FakeProvider([])
        with llm.use_provider(fake):
            assert llm.get_provider() is fake and llm.provider_name() == "fake"
        assert isinstance(llm.get_provider(), llm.OpenAICompatibleProvider)
        # ...and a test that fails inside the block still restores it.
        try:
            with llm.use_provider(fake):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert llm.provider_name() == "openai-compatible"
        # Nesting restores the outer override, not None.
        outer, inner = FakeProvider([]), FakeProvider([])
        with llm.use_provider(outer):
            with llm.use_provider(inner):
                assert llm.get_provider() is inner
            assert llm.get_provider() is outer
    assert llm._override is None


def test_an_unscripted_call_is_a_test_failure_not_an_empty_reply() -> None:
    fake = FakeProvider([GROUNDED])
    with llm.use_provider(fake):
        llm.complete_json(system="s", messages=[], schema={})
        try:
            llm.complete_json(system="s", messages=[], schema={})
        except AssertionError as exc:
            assert "unscripted call #2" in str(exc), exc
        else:
            raise AssertionError("an over-called fake must raise")
    # The wrong shape for the method is a test bug too.
    with llm.use_provider(FakeProvider([GROUNDED])):
        try:
            list(llm.stream_text(system="s", messages=[]))
        except AssertionError as exc:
            assert "stream_text scripted with dict" in str(exc), exc
        else:
            raise AssertionError("a dict scripted for stream_text must raise")


def test_complete_text_goes_through_the_override_for_the_run_explanation() -> None:
    fake = FakeProvider(["The buffer still overflows."])
    with llm.use_provider(fake):
        text = llm.complete_text(system="prose only", prompt="explain")
    assert text == "The buffer still overflows."
    assert fake.calls == [{"method": "complete_text", "system": "prose only",
                           "prompt": "explain", "max_tokens": 400}], fake.calls


# --------------------------------------------------------------------------- #
# 10. Request shape of the real providers, with the wire replaced
# --------------------------------------------------------------------------- #

@contextmanager
def _wire(replies: list, stream_chunks: list[bytes] | None = None):
    """Swap llm._post / _post_stream for canned replies; yield the captured requests.

    Each entry of `replies` is a response dict, or an LLMError to raise -- the
    same two outcomes the real `_post` has.
    """
    sent: list[dict] = []
    queue = list(replies)
    real_post, real_stream = llm._post, llm._post_stream

    def fake_post(url, headers, payload):
        sent.append({"url": url, "headers": dict(headers), "payload": json.loads(json.dumps(payload))})
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def fake_stream(url, headers, payload):
        sent.append({"url": url, "headers": dict(headers), "payload": json.loads(json.dumps(payload))})
        yield from stream_chunks or []

    llm._post, llm._post_stream = fake_post, fake_stream
    try:
        yield sent
    finally:
        llm._post, llm._post_stream = real_post, real_stream


def test_anthropic_forces_the_emit_answer_tool() -> None:
    reply = {"content": [{"type": "text", "text": "ignored"},
                         {"type": "tool_use", "name": "emit_answer", "input": GROUNDED}],
             "usage": {"input_tokens": 11, "output_tokens": 7}}
    schema = {"type": "object", "properties": {"title": {"type": "string"}}}
    with _patched(**ANTHROPIC), _wire([reply]) as sent:
        turn = llm.complete_json(system="SYS", messages=[llm.Message("user", "q")],
                                 schema=schema, max_tokens=123)
    assert len(sent) == 1, sent
    req = sent[0]
    assert req["url"] == llm.ANTHROPIC_URL
    assert req["headers"]["x-api-key"] == "sk-test"
    assert req["headers"]["anthropic-version"] == llm.ANTHROPIC_VERSION
    p = req["payload"]
    assert p["model"] == "claude-test" and p["max_tokens"] == 123 and p["system"] == "SYS", p
    assert p["messages"] == [{"role": "user", "content": "q"}], p
    assert p["tool_choice"] == {"type": "tool", "name": "emit_answer"}, p
    assert p["tools"] == [{"name": "emit_answer", "description": "Emit the answer card.",
                           "input_schema": schema}], p
    assert json.loads(turn.text) == GROUNDED
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (11, 7)


def test_anthropic_stream_parses_text_deltas_only() -> None:
    chunks = [
        b'event: message_start\ndata: {"type":"message_start"}\n\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\nda',
        b'ta: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"x"}}\n\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n',
    ]
    with _patched(**ANTHROPIC), _wire([], chunks) as sent:
        out = list(llm.stream_text(system="SYS", messages=[llm.Message("user", "q")]))
    assert out == ["Hel", "lo"], out
    assert sent[0]["payload"]["stream"] is True and "tools" not in sent[0]["payload"]
    assert sent[0]["payload"]["max_tokens"] == 700


def _chat_reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


def test_openai_auto_mode_drops_response_format_after_a_400() -> None:
    with _patched(**OPENAI), _wire([LLMError("provider HTTP 400"),
                                    _chat_reply(json.dumps(GROUNDED))]) as sent:
        turn = llm.complete_json(system="SYS", messages=[llm.Message("user", "q")], schema={})
    assert len(sent) == 2, sent
    assert sent[0]["payload"]["response_format"] == {"type": "json_object"}, sent[0]
    assert "response_format" not in sent[1]["payload"], sent[1]
    assert sent[0]["url"] == "https://llm.example/v1/chat/completions"
    assert sent[0]["headers"]["authorization"] == "Bearer k-test"
    system = sent[1]["payload"]["messages"][0]
    assert system["role"] == "system" and system["content"] == f"SYS\n\n{llm.json_contract()}"
    assert sent[1]["payload"]["model"] == "m-primary" and sent[1]["payload"]["max_tokens"] == 900
    assert json.loads(turn.text) == GROUNDED and turn.usage.total_tokens == 8


def test_openai_off_mode_never_sends_response_format() -> None:
    with _patched(**dict(OPENAI, llm_json_mode="off")), _wire([_chat_reply("{}")]) as sent:
        llm.complete_json(system="SYS", messages=[], schema={})
    assert len(sent) == 1 and "response_format" not in sent[0]["payload"], sent


def test_openai_walks_the_model_chain_past_a_429_and_an_empty_reply() -> None:
    cfg = dict(OPENAI, llm_json_mode="off", llm_fallback_models="m-two, m-primary ,m-three")
    with _patched(**cfg):
        assert llm.model_chain() == ["m-primary", "m-two", "m-three"]
        with _wire([LLMError("provider HTTP 429", retryable=True), _chat_reply(""),
                    _chat_reply(json.dumps(GROUNDED))]) as sent:
            turn = llm.complete_json(system="SYS", messages=[], schema={})
    assert [s["payload"]["model"] for s in sent] == ["m-primary", "m-two", "m-three"], sent
    assert json.loads(turn.text) == GROUNDED


def test_openai_stream_stops_at_done_and_reassembles_split_lines() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"{\\"ti"}}]}\n',
        b': keep-alive comment\n',
        b'data: {"choices":[{"delta":{"reasoning":"thinking..."}}]}\ndata: {"choi',
        b'ces":[{"delta":{"content":"tle\\"}"}}]}\n',
        b'data: [DONE]\n',
        b'data: {"choices":[{"delta":{"content":"AFTER DONE"}}]}\n',
    ]
    with _patched(**OPENAI), _wire([], chunks) as sent:
        out = list(llm.stream_text(system="SYS", messages=[llm.Message("user", "q")]))
    assert out == ['{"ti', 'tle"}'], out
    p = sent[0]["payload"]
    assert p["stream"] is True and "response_format" not in p, p
    assert p["messages"][0]["content"] == f"SYS\n\n{llm.json_contract()}", p["messages"][0]


def test_openai_complete_text_backs_off_on_the_same_model_before_advancing() -> None:
    sleeps: list[float] = []
    real_sleep = llm.time.sleep
    llm.time.sleep = sleeps.append
    try:
        cfg = dict(OPENAI, llm_fallback_models="m-two")
        rate = LLMError("provider HTTP 429", retryable=True)
        with _patched(**cfg), _wire([rate, rate, rate, _chat_reply("  Prose.  ")]) as sent:
            text = llm.complete_text(system="SYS", prompt="explain")
    finally:
        llm.time.sleep = real_sleep
    assert text == "Prose.", text
    assert [s["payload"]["model"] for s in sent] == ["m-primary"] * 3 + ["m-two"], sent
    assert sleeps == [1.5, 3.0], sleeps
    # Prose: no JSON contract appended, no response_format.
    assert sent[0]["payload"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert "response_format" not in sent[0]["payload"]


def test_anthropic_complete_text_uses_the_forced_tool_with_an_open_schema() -> None:
    reply = {"content": [{"type": "tool_use", "input": {"text": "x"}}]}
    with _patched(**ANTHROPIC), _wire([reply]) as sent:
        out = llm.complete_text(system="SYS", prompt="explain", max_tokens=50)
    assert out == json.dumps({"text": "x"}), out
    assert sent[0]["payload"]["tools"][0]["input_schema"] == {"type": "object"}
    assert sent[0]["payload"]["messages"] == [{"role": "user", "content": "explain"}]


def _run_stats() -> dict:
    """A run's stats block, shaped as `run_scenario` returns it.

    Small on purpose: these tests are about the card path, not the arithmetic,
    and the numbers only need to be quotable so grounding has something to
    accept or reject.
    """
    common = {"unstoredM3": 9056.82, "wetAreaHa": 0.83, "heatBlendedMean": 0.949,
              "surfaceMeanC": 28.5, "airMeanC": 16.5, "runoffM3": 17379.41,
              "floodAreaHa": 0.07}
    return {
        "scenario": dict(common),
        "baseline": dict(common),
        "delta": {k: 0.0 for k in common},
        "ratios": {},
        "temperature": {"observedMeanC": 41.26},
        "tide": {"active": False},
    }


def _data_quality() -> dict:
    """A clean run's data-quality block, which caps confidence at Medium."""
    return {
        "lstAvailable": True,
        "sanityChecksFailed": 0,
        "sanityCheckIds": [],
        "interventionsSkipped": 0,
        "terrainSource": "open3dhk-tiles",
        "landCoverSource": "esa-worldcover",
    }


def test_the_run_explanation_takes_a_model_written_card() -> None:
    """The run's explanation asks for a *card*, not a paragraph, so the run and
    the chat produce one shape. A card the model got wrong is repaired by code:
    `kind` and `eyebrow` are the UI's framing, not the model's to set."""
    from app.services import run_service

    good = {
        "kind": "general",  # deliberately wrong: code must override it
        "eyebrow": "",
        "title": "The run is unchanged from the baseline",
        "sentence": "Unstored runoff is 9057 m3.",
        "cause": "Wet area 0.8 ha.",
        "todo": "Enable a project and re-run.",
        "stats": [{"l": "Unstored runoff", "v": "9057 m3"}],
        "confidence": {"level": "High", "pct": 95, "note": "clear"},
        "caveats": [],
    }
    fake = FakeProvider([json.dumps(good)])
    with llm.use_provider(fake):
        card, source, reason = run_service._llm_explanation(
            _run_stats(), 50.0, 30.0, [], [], _data_quality())

    assert reason is None, reason
    assert source == "fake", source
    assert card.source == "model"
    assert card.kind == "run", "code owns `kind`; the model sent 'general'"
    assert card.eyebrow == "Sandbox run", "code owns `eyebrow`"
    # A clean run caps at Medium, so the model's High is lowered.
    assert card.confidence.level == "Medium", card.confidence


def test_the_run_explanation_reports_an_empty_model_reply() -> None:
    """A reasoning model answers 200 with `content: ""` when its whole budget
    went to the hidden trace. That must name itself rather than surfacing as an
    anonymous parse failure."""
    from app.services import run_service

    fake = FakeProvider([""])
    with llm.use_provider(fake):
        card, source, reason = run_service._llm_explanation(
            _run_stats(), 50.0, 30.0, [], [], _data_quality())

    assert card is None, card
    assert source == "template", source
    assert "returned nothing" in reason, reason


def test_a_slow_card_call_is_cut_off_and_falls_back_to_the_template() -> None:
    """A card that outruns the deadline must not hold the run open.

    The run's water and heat numbers are computed before this call and cannot
    reach the caller until it returns, so a slow provider blocks the whole
    simulation -- which is what made a run that had already succeeded look like a
    timeout in the browser. The deadline turns that into the same labelled
    template every other provider failure produces.
    """
    from app.services import run_service
    from app.agent import llm as _llm

    class SlowProvider:
        name = "slow"

        def complete_json(self, system, messages, schema, max_tokens):  # noqa: ANN001
            time.sleep(5.0)
            return _llm.Turn(text="{}", usage=_llm.Usage(0, 0))

    fake = SlowProvider()
    with llm.use_provider(fake):
        # Patch the module-level budget rather than sleeping for the real 45 s.
        original = _llm.CARD_DEADLINE_S
        _llm.CARD_DEADLINE_S = 0.4
        try:
            started = time.monotonic()
            card, source, reason = run_service._llm_explanation(
                _run_stats(), 50.0, 30.0, [], [], _data_quality())
            elapsed = time.monotonic() - started
        finally:
            _llm.CARD_DEADLINE_S = original

    assert card is None, card
    assert source == "template", source
    assert "deadline" in reason, reason
    # The point of the deadline: the caller returns without waiting the call out.
    assert elapsed < 2.0, f"the run waited {elapsed:.1f}s for a 0.4s deadline"


def test_a_model_written_run_card_still_passes_through_the_number_check() -> None:
    """The run gets no exemption from grounding: a card quoting a figure the run
    never produced is reported, not rendered as fact."""
    from app.services import run_service

    invented = {
        "title": "The run cuts the overflow by 99,999 m3",
        "sentence": "Runoff fell to 12,345 m3.",
        "cause": None,
        "todo": None,
        "stats": [],
        "confidence": {"level": "High", "pct": 90, "note": "clear"},
        "caveats": [],
    }
    fake = FakeProvider([json.dumps(invented)])
    with llm.use_provider(fake):
        card, _, _ = run_service._llm_explanation(
            _run_stats(), 50.0, 30.0, [], [], _data_quality())

    assert card is not None
    assert card.grounding is not None and not card.grounding.ok, card.grounding
    assert card.grounding.ungrounded, "the invented figures must be named"
    assert card.confidence.level == "Low", "an ungrounded card cannot sit at Medium"


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
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    # A fake left installed would silently answer for every later suite.
    if llm._override is not None:
        failed += 1
        print("  FAIL  a provider override leaked out of this suite")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
