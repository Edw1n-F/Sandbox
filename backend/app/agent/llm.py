"""Provider-neutral LLM client for the chat (shape taken from Constellation A3).

The chat only ever asks the model for two things, so this module exposes exactly
two calls and nothing else:

- :func:`complete_json` -- one structured call returning JSON matching a schema.
- :func:`stream_text` -- one call yielding text deltas as the model writes.

Both are provider-neutral: Anthropic Messages API when ``ANTHROPIC_API_KEY`` is
set, any OpenAI-compatible endpoint when ``LLM_BASE_URL``/``LLM_MODEL`` are set,
and :class:`ProviderUnavailable` otherwise. Nothing here is allowed to invent an
answer when no provider is configured -- callers fall back to a template card
and label it, which is the honesty rule the rest of the app is built on.

Structured output is forced, not requested. A schema in the prompt is a
suggestion; a tool the model must call (Anthropic) or a ``response_format``
(OpenAI-compatible) is a guarantee, and the difference matters because the
answer card is parsed by code and rendered by field.

Providers sit behind a :class:`Provider` protocol. :func:`get_provider` resolves
one from settings on every call (Anthropic key first, then base URL + model), and
:func:`use_provider` swaps in another -- in practice
:class:`app.agent.fake_provider.FakeProvider` -- so the chat can be driven end to
end in a test with no key and no network. The module-level functions are thin
delegates, so no caller had to change when the protocol arrived.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

from app.config import settings

__all__ = [
    "AnthropicProvider",
    "LLMError",
    "Message",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderUnavailable",
    "Turn",
    "Usage",
    "complete_json",
    "complete_text",
    "get_provider",
    "provider_name",
    "set_provider",
    "stream_text",
    "use_provider",
]

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_S = 90

# A ceiling on the *whole* answer-card call, not on one HTTP request.
#
# `TIMEOUT_S` bounds a single request, and `complete_json` walks a fallback chain,
# so a run whose provider is slow or throttled can spend `TIMEOUT_S` once per model
# in the chain. Measured here on a free tier: 60 s for one good card, and the
# chain is what turns a slow provider into a four-minute stall.
#
# That matters because of who is waiting. The run's card is an *optional* extra --
# `run_service` already falls back to the code-assembled template and labels it
# `source: "template"` -- but the water and heat numbers are computed before the
# call and cannot reach the caller until it returns. So a slow card does not
# degrade the explanation, it blocks the whole simulation, and the browser's own
# request timeout then kills a run that had already succeeded.
#
# 45 s is chosen to sit under the client's 60 s with room for the rest of the
# response. Exceeding it is not an error: it returns no card, and the run reports
# the labelled template exactly as it does when no provider is configured.
CARD_DEADLINE_S = 45.0

Effort = Literal["low", "medium", "high"]


class ProviderUnavailable(Exception):
    """No provider is configured (no key / no base URL). Not retryable."""


class LLMError(Exception):
    """The provider call failed. `retryable`: worth trying again."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass
class Usage:
    """Token usage for one call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Message:
    """One transcript message. Multi-turn memory, replayed verbatim."""

    role: Literal["user", "assistant"]
    text: str


@dataclass
class Turn:
    """One model response."""

    text: str
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Provider(Protocol):
    """What the chat needs from a model, and nothing more.

    Three calls, all synchronous, all raising ProviderUnavailable / LLMError on
    failure. The outermost-``{...}`` repair on ``complete_json`` is *not* a
    provider's job: it lives in the public :func:`complete_json`, so every
    provider -- the fake included -- gets the same parse behaviour.
    """

    name: str

    def complete_json(self, system: str, messages: list[Message], schema: dict,
                      max_tokens: int) -> Turn: ...

    def stream_text(self, system: str, messages: list[Message], max_tokens: int) -> Iterator[str]: ...

    def complete_text(self, system: str, prompt: str, max_tokens: int) -> str: ...


#: A provider installed by :func:`set_provider` / :func:`use_provider`. When set
#: it wins over settings outright -- that is the point of an override.
_override: Provider | None = None


def set_provider(provider: Provider | None) -> Provider | None:
    """Install `provider` (or clear with None). Returns the previous override.

    Prefer :func:`use_provider` in tests: it restores the previous value even
    when the test fails, so a fake cannot leak into the next test.
    """
    global _override
    previous = _override
    _override = provider
    return previous


@contextmanager
def use_provider(provider: Provider) -> Iterator[Provider]:
    """Install `provider` for the duration of a `with` block, then restore."""
    previous = set_provider(provider)
    try:
        yield provider
    finally:
        set_provider(previous)


def get_provider() -> Provider:
    """The override if one is set, else the provider settings describe, else raise.

    Resolved on every call, not cached: settings are read at call time
    everywhere else in this module, and tests patch them.
    Anthropic wins over an OpenAI-compatible setup -- a leftover
    ``ANTHROPIC_API_KEY`` silently disables an OpenRouter / Ollama config, which
    is a documented trap rather than a bug to fix here.
    """
    if _override is not None:
        return _override
    if getattr(settings, "anthropic_api_key", "") or "":
        return AnthropicProvider()
    if (getattr(settings, "llm_base_url", "") or "") and (getattr(settings, "llm_model", "") or ""):
        return OpenAICompatibleProvider()
    raise ProviderUnavailable("no LLM provider configured")


def provider_name() -> str:
    """Which provider will be used, or 'none'. Drives the UI's status dot."""
    try:
        return get_provider().name
    except ProviderUnavailable:
        return "none"


def _post(url: str, headers: dict[str, str], payload: dict) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # 429 and 5xx are worth one retry; a 400 is a bug in the request.
        raise LLMError(f"provider HTTP {exc.code}", retryable=exc.code in (408, 429, 500, 502, 503, 504)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"provider unreachable: {exc}", retryable=True) from exc


def _post_stream(url: str, headers: dict[str, str], payload: dict) -> Iterator[bytes]:
    """Yield raw response chunks. Streaming, so deltas reach the client as they arrive."""
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            while True:
                chunk = response.read1(4096)
                if not chunk:
                    return
                yield chunk
    except urllib.error.HTTPError as exc:
        raise LLMError(f"provider HTTP {exc.code}", retryable=exc.code in (408, 429, 500, 502, 503, 504)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"provider unreachable: {exc}", retryable=True) from exc


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

def _anthropic_headers(key: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "user-agent": "T1-Sandbox/1.0",
    }


class AnthropicProvider:
    """Anthropic Messages API. Reads its key and model from settings per call."""

    name = "anthropic"

    def complete_json(self, system: str, messages: list[Message], schema: dict,
                      max_tokens: int) -> Turn:
        """Structured output by forcing a tool call whose input_schema is the schema.

        The model cannot reply in prose it cannot be parsed from, because the only
        way to answer is to call the tool. This is the whole reason the answer card
        survives a model that would otherwise wrap JSON in a sentence.
        """
        key = getattr(settings, "anthropic_api_key", "")
        model = getattr(settings, "anthropic_model", "") or "claude-sonnet-4-5"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.text} for m in messages],
            "tools": [{
                "name": "emit_answer",
                "description": "Emit the answer card.",
                "input_schema": schema,
            }],
            "tool_choice": {"type": "tool", "name": "emit_answer"},
        }
        raw = _post(ANTHROPIC_URL, _anthropic_headers(key), payload)
        text = ""
        for block in raw.get("content", []) or []:
            if block.get("type") == "tool_use":
                text = json.dumps(block.get("input", {}))
                break
        usage = raw.get("usage", {}) or {}
        return Turn(
            text=text,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
            ),
        )

    def stream_text(self, system: str, messages: list[Message], max_tokens: int) -> Iterator[str]:
        key = getattr(settings, "anthropic_api_key", "")
        model = getattr(settings, "anthropic_model", "") or "claude-sonnet-4-5"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.text} for m in messages],
            "stream": True,
        }
        buffer = b""
        for chunk in _post_stream(ANTHROPIC_URL, _anthropic_headers(key), payload):
            buffer += chunk
            while b"\n\n" in buffer:
                event, buffer = buffer.split(b"\n\n", 1)
                for line in event.split(b"\n"):
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield str(delta.get("text", ""))

    def complete_text(self, system: str, prompt: str, max_tokens: int) -> str:
        # As before: Anthropic has no model chain, so prose goes through the same
        # forced-tool call with an open schema.
        return self.complete_json(system, [Message("user", prompt)], {"type": "object"}, max_tokens).text


# --------------------------------------------------------------------------- #
# OpenAI-compatible
# --------------------------------------------------------------------------- #

def _openai_headers(key: str) -> dict[str, str]:
    headers = {"content-type": "application/json", "user-agent": "T1-Sandbox/1.0"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    return headers


def _openai_endpoint() -> str:
    base = getattr(settings, "llm_base_url", "").rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def model_chain() -> list[str]:
    """The configured model first, then any fallbacks, de-duplicated.

    Free tiers are shared pools: on OpenRouter a `:free` model being throttled
    upstream is routine, so one model id is one point of failure. Ordering is
    preserved so the cheap/fast model stays first and the slow one is only ever
    the rescue.
    """
    primary = (getattr(settings, "llm_model", "") or "").strip()
    extra = getattr(settings, "llm_fallback_models", "") or ""
    chain = [primary] if primary else []
    for item in extra.split(","):
        name = item.strip()
        if name and name not in chain:
            chain.append(name)
    return chain


def _openai_json_once(model: str, system: str, messages: list[Message], max_tokens: int) -> Turn:
    """One structured call against one model id.

    `response_format: json_object` is requested because it is a guarantee when a
    server supports it -- but a large share of the free OpenRouter models reject
    the parameter outright with a 400, and Ollama and some proxies ignore it. So
    the contract is also in the prompt, and a 400 falls back to the same call
    without the parameter rather than failing the question.
    """
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}]
        + [{"role": m.role, "content": m.text} for m in messages],
    }
    mode = (getattr(settings, "llm_json_mode", "auto") or "auto").lower()
    headers = _openai_headers(getattr(settings, "llm_api_key", "") or "")
    if mode == "json":
        body["response_format"] = {"type": "json_object"}
    if mode == "auto":
        body["response_format"] = {"type": "json_object"}
        try:
            raw = _post(_openai_endpoint(), headers, body)
        except LLMError as exc:
            if "400" not in exc.message:
                raise
            del body["response_format"]
            raw = _post(_openai_endpoint(), headers, body)
    else:
        raw = _post(_openai_endpoint(), headers, body)
    text = ""
    for choice in raw.get("choices", []) or []:
        message = choice.get("message", {}) or {}
        if message.get("content"):
            text = str(message["content"])
            break
    usage = raw.get("usage", {}) or {}
    return Turn(
        text=text,
        usage=Usage(
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        ),
    )


class OpenAICompatibleProvider:
    """Any /chat/completions endpoint (OpenRouter, Ollama, vLLM, ...).

    Reads base URL, key, model chain and JSON mode from settings per call.
    """

    name = "openai-compatible"

    def complete_json(self, system: str, messages: list[Message], schema: dict,
                      max_tokens: int) -> Turn:
        """Structured output, walking the model chain until one answers.

        Two failure shapes are worth advancing past rather than reporting:
        a throttled upstream (429 / 5xx, routine on shared free pools) and an empty
        `content` -- reasoning models routinely route every token to the reasoning
        channel, which is a 200 with nothing in it.
        """
        chain = model_chain()
        last: Exception | None = None
        for index, model in enumerate(chain):
            try:
                turn = _openai_json_once(model, system, messages, max_tokens)
            except LLMError as exc:
                last = exc
                advance = exc.retryable or "400" in exc.message
                if not advance or index == len(chain) - 1:
                    raise
                continue
            if turn.text.strip() or index == len(chain) - 1:
                return turn
        raise last or LLMError("no model answered", retryable=True)

    def stream_text(self, system: str, messages: list[Message], max_tokens: int) -> Iterator[str]:
        model = getattr(settings, "llm_model", "")
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            # The contract must be in `system` (a stream cannot be forced into
            # JSON): the caller passes the already-contracted system prompt.
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.text} for m in messages],
            "stream": True,
        }
        buffer = b""
        for chunk in _post_stream(_openai_endpoint(), _openai_headers(getattr(settings, "llm_api_key", "") or ""), payload):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    return
                try:
                    payload_event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in payload_event.get("choices", []) or []:
                    delta = choice.get("delta", {}) or {}
                    if delta.get("content"):
                        yield str(delta["content"])

    def complete_text(self, system: str, prompt: str, max_tokens: int) -> str:
        """Plain prose, no JSON contract, walking the model chain past 429s and empty replies."""
        headers = _openai_headers(getattr(settings, "llm_api_key", "") or "")
        last: Exception | None = None
        for model in model_chain():
            body = {"model": model, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": prompt}]}
            # Free-tier 429s are usually a shared-pool hiccup of a few seconds, not a
            # hard outage: retry the SAME model briefly before advancing. Advancing
            # to another model does not help when the whole free family is throttled
            # at once (both Gemma ids 429 together), so backoff is the real lever.
            for attempt in range(3):
                try:
                    raw = _post(_openai_endpoint(), headers, body)
                    break
                except LLMError as exc:
                    last = exc
                    if not exc.retryable:
                        raise
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            else:
                continue
            for choice in raw.get("choices", []) or []:
                text = str(((choice.get("message") or {}).get("content")) or "").strip()
                if text:
                    return text
        raise last or LLMError("no model answered", retryable=True)


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #

def complete_json(
    *,
    system: str,
    messages: list[Message],
    schema: dict,
    max_tokens: int = 900,
    deadline_s: float | None = None,
) -> Turn:
    """One structured call. Raises ProviderUnavailable / LLMError.

    ``deadline_s`` caps the call in wall-clock time and is enforced by running it
    on a worker thread: a socket timeout can only bound one request, and this
    bounds the whole provider chain, which is the number a caller waiting on an
    HTTP response actually cares about (see :data:`CARD_DEADLINE_S`). A call that
    overruns raises :class:`LLMError`, so every existing caller's failure path --
    including the run's template fallback -- handles it without a new branch.

    Leaving ``deadline_s`` as None keeps the old behaviour: bounded only by
    ``TIMEOUT_S`` per request.
    """
    if deadline_s is None:
        turn = get_provider().complete_json(system, messages, schema, max_tokens)
    else:
        import threading  # noqa: PLC0415

        # A daemon thread rather than a ThreadPoolExecutor, deliberately. An
        # executor's context manager joins its worker on exit, so a `with` block
        # would wait out the very call the deadline exists to stop -- measured:
        # a 3 s deadline returned after 27.6 s. A daemon thread lets the caller
        # return on time, and being a daemon it cannot hold up interpreter exit.
        #
        # The worker is not cancelled: Python cannot interrupt a blocked socket
        # read, so it finishes and its result is discarded. That is the cost of
        # bounding the caller, and the worker is itself bounded by TIMEOUT_S.
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["turn"] = get_provider().complete_json(
                    system, messages, schema, max_tokens)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
                box["error"] = exc

        worker = threading.Thread(target=_run, name="llm-complete-json", daemon=True)
        worker.start()
        worker.join(deadline_s)
        if worker.is_alive():
            raise LLMError(
                f"the card call exceeded its {deadline_s:.0f} s deadline",
                retryable=False,
            )
        if "error" in box:
            raise box["error"]
        turn = box["turn"]
    # A model that answers in prose around the JSON still parses: take the
    # outermost {...} rather than failing the whole turn.
    if not turn.text.strip().startswith("{"):
        match = re.search(r"\{.*\}", turn.text, re.DOTALL)
        if match:
            turn.text = match.group(0)
    return turn


def stream_text(*, system: str, messages: list[Message], max_tokens: int = 700) -> Iterator[str]:
    """Yield text deltas. Raises ProviderUnavailable / LLMError.

    Still a generator, so -- as before -- the provider is resolved on the first
    ``next()``, not at the call. chat_service relies on that: it wraps the
    iteration, not the call, in its ProviderUnavailable handler.
    """
    yield from get_provider().stream_text(system, messages, max_tokens)


def complete_text(*, system: str, prompt: str, max_tokens: int = 400) -> str:
    """Plain prose, no JSON contract, walking the model chain past 429s and empty replies.

    For callers that want a paragraph (the run explanation), not a card. Raises
    ProviderUnavailable / LLMError when no model in the chain answers.
    """
    return get_provider().complete_text(system, prompt, max_tokens)
