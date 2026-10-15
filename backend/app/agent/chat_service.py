"""The chat: run facts -> a grounded answer card, with visible steps.

Shape follows Constellation's loop minus the parts a sandbox run does not have:
no skills, no tools the model can call, no clarification cards. What is kept is
the part that matters for trust:

  steps      the reader sees what is happening while it happens
  grounding  every number is checked against the run, and a card that fails is
             sent back to the model once with the problems named
  honesty    no provider configured means a template card, labelled "template"

The revision loop is one round on purpose. Constellation loops until the model
converges; here a second failure is reported rather than retried, because a
planner waiting on a spinner is worse served than one shown an ungrounded
number with a note on it.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from app.agent import confidence, grounding, llm
from app.agent.answer import ANSWER_SCHEMA, AnswerCard, GroundingReport, template_card
from app.agent.intent import classify_turn
from app.agent.prompts import SYSTEM, json_system, user_turn

__all__ = ["answer", "build_facts", "stream_events"]
# Not a style choice: reasoning models bill their thinking against the same
# budget as the answer. Measured on nemotron-3-ultra: a 900-token cap was
# consumed by 382 reasoning tokens, so the reply came back `finish_reason:
# length` with the card truncated mid-object -- a 200 that parses to nothing.
# 1600 completes; 2000 leaves headroom for a long `cause`.
MAX_TOKENS = 2000

#: The visible steps. `tool` is what Constellation calls the step's instrument;
#: here it names the code path, which is what the UI shows while it runs.
STEPS: list[tuple[str, str, str]] = [
    ("Read the run", "Collect the scenario, baseline and delta numbers", "facts"),
    ("Draft the answer", "Write the card from those numbers", "llm"),
    ("Ground the numbers", "Check every figure against the run", "validate"),
]


def build_facts(run_result: dict[str, Any] | None) -> dict[str, Any]:
    """The numbers a model may quote, from a run result (or empty if none).

    Nothing is computed here. The chat explains a run that already happened;
    recomputing one inside a Q&A turns a fast reply into a 0.7 s simulation and
    lets the answer drift from the numbers the user is looking at.

    The `data` block is the exception that proves that rule: it is not a
    measurement the model may quote, it is what the run reports about its own
    inputs. It is read here so the confidence ladder can cap a claim on evidence
    that actually exists rather than on how sure the model felt.
    """
    if not run_result:
        return {}
    stats = run_result.get("stats") or {}
    sources = run_result.get("sources") or {}
    checks = run_result.get("sanityChecks") or []
    return {
        "inputs": run_result.get("inputs") or {},
        "scenario": stats.get("scenario") or {},
        "baseline": stats.get("baseline") or {},
        "delta": stats.get("delta") or {},
        # Forwarded, never computed here: the run derives these shares so a card
        # can quote "95 % unstored" and still pass grounding.
        "ratios": stats.get("ratios") or {},
        "applied": run_result.get("interventionsApplied") or [],
        "skipped": run_result.get("interventionsSkipped") or [],
        # Read, never quoted: the confidence ladder's inputs. Keys are booleans
        # and small counts, so a card that echoed one would still fail grounding
        # or land inside the 0-12 free range -- neither can smuggle a number in.
        "data": {
            "lstAvailable": bool(sources.get("lst")),
            "sanityChecksFailed": len(checks),
            "sanityCheckIds": [c.get("id") for c in checks if isinstance(c, dict)],
            "interventionsSkipped": len(run_result.get("interventionsSkipped") or []),
            "terrainSource": sources.get("terrain"),
            "landCoverSource": sources.get("landCover"),
            # Why the run's own explanation was written by code, when it was.
            # Carried through so the chat's template card names the same cause
            # the run did, instead of asserting a missing model.
            "reason": run_result.get("explanationNote"),
        },
    }


def _history_messages(history: list[dict[str, str]] | None) -> list[llm.Message]:
    """Prior turns, so the model can answer "and at 300 mm?" without being told
    what 300 mm refers to."""
    out: list[llm.Message] = []
    for item in history or []:
        role = item.get("role")
        text = item.get("text")
        if role in ("user", "assistant") and text:
            out.append(llm.Message(role=role, text=str(text)))
    return out[-8:]


def _parse_card(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _draft(
    facts: dict[str, Any],
    question: str,
    history: list[dict[str, str]] | None,
    *,
    on_delta: Any = None,
) -> dict[str, Any] | None:
    """One structured draft. `on_delta` receives text as it streams, if given.

    Streaming and structured output pull in opposite directions: a forced tool
    call guarantees parseable JSON but does not stream, and a streamed reply
    streams but can arrive wrapped in prose. So the streaming path asks for raw
    JSON and parses the outermost object, and the non-streaming path forces the
    schema. Both end up here with a dict or None.
    """
    messages = _history_messages(history) + [llm.Message(role="user", text=user_turn(question, facts))]
    if on_delta is not None:
        # Streaming cannot force a JSON tool call, so the contract has to be in
        # the prompt: without it a streamed reply is prose and `_parse_card`
        # returns None, which reads as "the model said nothing".
        chunks: list[str] = []
        for delta in llm.stream_text(system=json_system(), messages=messages, max_tokens=MAX_TOKENS):
            chunks.append(delta)
            on_delta(delta)
        return _parse_card("".join(chunks))
    turn = llm.complete_json(
        system=SYSTEM, messages=messages, schema=ANSWER_SCHEMA, max_tokens=MAX_TOKENS
    )
    return _parse_card(turn.text)


def _card_from(draft: dict[str, Any], facts: dict[str, Any], *, revised: bool) -> AnswerCard:
    """Validate a draft against the model, then check its numbers, then cap it.

    The cap runs on the *interim* verdict (did the revision happen at all), not
    on who is applying it; `_cap_residual` re-runs once the revision loop has
    settled, which is the only point at which "still ungrounded" is knowable.
    """
    try:
        card = AnswerCard.model_validate(draft)
    except Exception:  # pydantic ValidationError and friends: fall back to a template
        return template_card(facts)
    problems = grounding.check_card(card, facts)
    card.grounding = GroundingReport(ok=not problems, ungrounded=problems, revised=revised)
    card.source = "model"
    card.confidence = confidence.apply_ladder(
        card.confidence, confidence.cap_of(facts.get("data") or {})
    )
    return card


def _cap_residual(card: AnswerCard, facts: dict[str, Any]) -> AnswerCard:
    """Re-cap once grounding is final: a surviving ungrounded number is a Low.

    Called after the revision loop, because `_card_from` cannot know whether a
    failed check will be repaired on the retry. Re-applying is idempotent -- the
    ladder reads the card's current level as the proposal -- so a card that was
    already capped stays where it is.
    """
    if card.source != "model":
        return card
    residual = bool(card.grounding and not card.grounding.ok)
    card.confidence = confidence.apply_ladder(
        card.confidence, confidence.cap_of(facts.get("data") or {}), ungrounded=residual
    )
    return card


def _no_run_card() -> AnswerCard:
    """The card for "ask me before anything has run".

    `template_card({})` is the right shape but the wrong note: it explains itself
    as "no language model is configured", which is not why *this* card was
    assembled. A model may be configured and reachable; there is simply nothing
    to explain yet. The distinction matters because STATUS.md's honesty claim is
    that a card never misattributes why it looks the way it does.
    """
    card = template_card({})
    card.kind = "general"
    card.sentence = ("Run the scenario first: adjust the sliders, pick a project, and press "
                     "Run. Then I can answer questions about it.")
    card.confidence = card.confidence.model_copy(update={
        "note": "No run in scope yet: there is nothing to explain until a scenario has been run.",
    })
    return card


def answer(question: str, facts: dict[str, Any],
           history: list[dict[str, str]] | None = None) -> AnswerCard:
    """One answer, no streaming. Used by the plain POST endpoint and by tests."""
    if not facts:
        return classify_turn(question, _no_run_card())
    try:
        draft = _draft(facts, question, history)
    except llm.ProviderUnavailable:
        return template_card(facts)
    except llm.LLMError:
        return template_card(facts)
    if not draft:
        return template_card(facts)
    card = _card_from(draft, facts, revised=False)
    # One revision: name the offending numbers and ask again.
    if card.grounding and not card.grounding.ok:
        retry = f"{question}\n\nYour last answer contained numbers this run did not produce:\n" + \
            "\n".join(f"- {p}" for p in card.grounding.ungrounded) + \
            "\nRewrite it using only the numbers in the FACTS block."
        try:
            second = _draft(facts, retry, history)
        except (llm.ProviderUnavailable, llm.LLMError):
            second = None
        if second:
            card = _card_from(second, facts, revised=True)
    # Deterministic routing (DESIGN §2.5): the classifier runs after the card is
    # built and may only raise the intent, so a proposal-shaped instruction
    # routes to the gate even when no model answered.
    return _cap_residual(classify_turn(question, card), facts)


def stream_events(question: str, facts: dict[str, Any],
                  history: list[dict[str, str]] | None = None) -> Iterator[dict[str, Any]]:
    """Yield the run as stream events: steps, deltas, grounding, answer, done.

    Mirrors Constellation's `StreamEvent` set (`app/schemas/stream.py`) with the
    four this app has: run_started, step_started/step_finished, delta, answer,
    grounding, error, done.
    """
    started = time.time()
    yield {"event": "run_started", "run_id": f"chat-{int(started * 1000)}"}

    def step(index: int, title: str, desc: str, tool: str, work):
        yield {"event": "step_started", "index": index, "title": title, "desc": desc, "tool": tool}
        t0 = time.time()
        result = work()
        yield {"event": "step_finished", "index": index, "title": title, "desc": desc,
               "tool": tool, "ms": int((time.time() - t0) * 1000), "result": result}
        return result

    # 1. facts
    yield from step(0, *STEPS[0], lambda: f"{len(facts.get('scenario', {}))} scenario measures")

    if not facts:
        # The no-run branch is the one that matters today (DESIGN R1): with no
        # model key the card is code-assembled, and only `classify_turn` can lift
        # a proposal-shaped instruction out of the default "question".
        card = classify_turn(question, _no_run_card())
        yield {"event": "answer", "answer": card.model_dump()}
        yield {"event": "done", "status": "done", "ms": int((time.time() - started) * 1000)}
        return

    # 2. draft, streaming deltas as they arrive
    card: AnswerCard | None = None
    draft_title, draft_desc, draft_tool = STEPS[1]
    yield {"event": "step_started", "index": 1, "title": draft_title, "desc": draft_desc,
           "tool": draft_tool}
    t_draft = time.time()

    def retry_unstreamed():
        """One non-streaming attempt. Reasoning models (nemotron ultra, inkling)
        frequently send every token down the reasoning channel, so a stream can
        complete with zero `content` deltas -- no exception, just nothing. The
        non-streaming response carries `content` regardless, so it recovers the
        same call the stream silently dropped.
        """
        try:
            return _draft(facts, question, history)
        except (llm.ProviderUnavailable, llm.LLMError):
            return None

    try:
        deltas: list[str] = []
        draft = _draft(facts, question, history, on_delta=deltas.append)
        for chunk in deltas:
            yield {"event": "delta", "text": chunk}
        if draft:
            card = _card_from(draft, facts, revised=False)
            draft_result = "card drafted"
        else:
            yield {"event": "error", "message": "the model returned no card on the stream",
                   "recoverable": False}
            draft = retry_unstreamed()
            if draft:
                card = _card_from(draft, facts, revised=False)
                draft_result = "card drafted (unstreamed)"
            else:
                draft_result = "no card returned"
    except llm.ProviderUnavailable:
        # Still a finished step: the stepper shows three ticks either way, and
        # the result line says which path produced the card.
        draft_result = "no model configured — template"
        yield {"event": "error", "message": "no language model configured; using the template card",
               "recoverable": True}
    except llm.LLMError as exc:
        # Several free OpenAI-compatible models (a fair share of OpenRouter's
        # :free tier among them) answer /chat/completions but reject
        # `stream: true`. Falling straight to a template card there would look
        # like a broken model, so retry once without streaming: slower, no
        # deltas, but still the model's own words.
        yield {"event": "error", "message": exc.message, "recoverable": exc.retryable}
        draft = retry_unstreamed()
        if draft:
            card = _card_from(draft, facts, revised=False)
            draft_result = "card drafted (unstreamed)"
        else:
            draft_result = f"failed: {exc.message}"
    yield {"event": "step_finished", "index": 1, "title": draft_title, "desc": draft_desc,
           "tool": draft_tool, "ms": int((time.time() - t_draft) * 1000), "result": draft_result}

    # 3. grounding (and at most one revision, reported as a step)
    ground_title, ground_desc, ground_tool = STEPS[2]
    yield {"event": "step_started", "index": 2, "title": ground_title, "desc": ground_desc,
           "tool": ground_tool}
    t_ground = time.time()

    if card is not None and card.grounding and not card.grounding.ok:
        yield {"event": "grounding", "ok": False, "ungrounded": card.grounding.ungrounded,
               "revised": False}
        retry = f"{question}\n\nYour last answer contained numbers this run did not produce:\n" + \
            "\n".join(f"- {p}" for p in card.grounding.ungrounded) + \
            "\nRewrite it using only the numbers in the FACTS block."
        try:
            second = _draft(facts, retry, history)
            if second:
                card = _card_from(second, facts, revised=True)
                yield {"event": "grounding", "ok": card.grounding.ok if card.grounding else True,
                       "ungrounded": card.grounding.ungrounded if card.grounding else [],
                       "revised": True}
        except (llm.ProviderUnavailable, llm.LLMError):
            pass
    elif card is not None:
        yield {"event": "grounding", "ok": True, "ungrounded": [], "revised": False}

    # A template card is grounded by construction -- every number in it is one
    # the run produced -- which is the honest reason to skip the check rather
    # than to run it and report nothing.
    if card is None:
        card = template_card(facts)
        ground_result = "template card — no check needed"
    else:
        card = _cap_residual(card, facts)
        ground_result = ("all numbers trace to the run" if (card.grounding and card.grounding.ok)
                         else f"{len(card.grounding.ungrounded) if card.grounding else 0} ungrounded")
    yield {"event": "step_finished", "index": 2, "title": ground_title, "desc": ground_desc,
           "tool": ground_tool, "ms": int((time.time() - t_ground) * 1000), "result": ground_result}

    # Deterministic routing, wired once for every path that reaches here --
    # the model draft, the template card and the unstreamed retry alike. It can
    # only raise the intent, never lower it, so the model's own classification
    # still counts when the parser recognises nothing.
    card = classify_turn(question, card)
    yield {"event": "answer", "answer": card.model_dump()}
    yield {"event": "done", "status": "done", "ms": int((time.time() - started) * 1000)}
