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
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from app.config import settings

__all__ = [
    "LLMError",
    "Message",
    "ProviderUnavailable",
    "Turn",
    "Usage",
    "complete_json",
    "provider_name",
    "stream_text",
]

def json_contract() -> str:
    """The reply contract, for endpoints that cannot be forced into JSON."""
    from app.agent.prompts import JSON_CONTRACT

    return JSON_CONTRACT


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_S = 90

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


def provider_name() -> str:
    """Which provider will be used, or 'none'. Drives the UI's status dot."""
    if getattr(settings, "anthropic_api_key", ""):
        return "anthropic"
    if getattr(settings, "llm_base_url", "") and getattr(settings, "llm_model", ""):
        return "openai-compatible"
    return "none"


def _require() -> tuple[str, str, str]:
    """(kind, credential, model) for the configured provider, or raise."""
    key = getattr(settings, "anthropic_api_key", "") or ""
    if key:
        return "anthropic", key, getattr(settings, "anthropic_model", "") or "claude-sonnet-4-5"
    base = getattr(settings, "llm_base_url", "") or ""
    model = getattr(settings, "llm_model", "") or ""
    if base and model:
        return "openai", getattr(settings, "llm_api_key", "") or "", model
    raise ProviderUnavailable("no LLM provider configured")


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


def _anthropic_json(system: str, messages: list[Message], schema: dict, max_tokens: int) -> Turn:
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


def _anthropic_stream(system: str, messages: list[Message], max_tokens: int) -> Iterator[str]:
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
        "messages": [{"role": "system", "content": f"{system}\n\n{json_contract()}"}]
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


def _openai_json(system: str, messages: list[Message], schema: dict, max_tokens: int) -> Turn:
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


def _openai_stream(system: str, messages: list[Message], max_tokens: int) -> Iterator[str]:
    model = getattr(settings, "llm_model", "")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        # Same contract as the non-streaming call: a stream cannot be forced
        # into JSON, so it has to be asked for.
        "messages": [{"role": "system", "content": f"{system}\n\n{json_contract()}"}]
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


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #

def complete_json(
    *,
    system: str,
    messages: list[Message],
    schema: dict,
    max_tokens: int = 900,
) -> Turn:
    """One structured call. Raises ProviderUnavailable / LLMError."""
    kind, _, _ = _require()
    if kind == "anthropic":
        turn = _anthropic_json(system, messages, schema, max_tokens)
    else:
        turn = _openai_json(system, messages, schema, max_tokens)
    # A model that answers in prose around the JSON still parses: take the
    # outermost {...} rather than failing the whole turn.
    if not turn.text.strip().startswith("{"):
        match = re.search(r"\{.*\}", turn.text, re.DOTALL)
        if match:
            turn.text = match.group(0)
    return turn


def stream_text(*, system: str, messages: list[Message], max_tokens: int = 700) -> Iterator[str]:
    """Yield text deltas. Raises ProviderUnavailable / LLMError."""
    kind, _, _ = _require()
    if kind == "anthropic":
        yield from _anthropic_stream(system, messages, max_tokens)
    else:
        yield from _openai_stream(system, messages, max_tokens)
