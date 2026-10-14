"""A scripted :class:`~app.agent.llm.Provider` for tests: no key, no network.

Lives beside ``llm.py`` rather than inside it so the production module carries no
test scaffolding; nothing in ``app/`` imports this file.

Each call consumes the next scripted reply, in order, whichever method made it:

    complete_json   str (raw model text) | dict (JSON-encoded) | Turn
    stream_text     list of str deltas; [] is the reasoning-channel failure (a
                    stream that ends with no content); an exception *inside*
                    the list is raised mid-stream, after the deltas before it
    complete_text   str
    any method      an exception instance (LLMError, ProviderUnavailable) to raise

A reply of the wrong shape for the method, or a call past the end of the script,
is an AssertionError: an unscripted call is a bug in the test, and answering it
with an empty string would let the chat's own fallbacks hide that bug.

    fake = FakeProvider([LLMError("provider HTTP 429", retryable=True), {...card...}])
    with llm.use_provider(fake):
        events = list(chat_service.stream_events(question, facts))
    assert [c["method"] for c in fake.calls] == ["stream_text", "complete_json"]
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from app.agent.llm import Message, Turn

__all__ = ["FakeProvider"]


class FakeProvider:
    """Replays a script and records every call in :attr:`calls`."""

    name = "fake"

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        #: One dict per call: method, system, messages (or prompt), max_tokens.
        self.calls: list[dict[str, Any]] = []

    # -- plumbing ----------------------------------------------------------- #

    def _next(self, method: str, record: dict[str, Any]) -> Any:
        record = {"method": method, **record}
        self.calls.append(record)
        index = len(self.calls) - 1
        if index >= len(self.script):
            raise AssertionError(
                f"FakeProvider: unscripted call #{index + 1} to {method} "
                f"(script has {len(self.script)} replies)"
            )
        reply = self.script[index]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def remaining(self) -> int:
        """Scripted replies not yet consumed. A test can assert this is 0."""
        return max(0, len(self.script) - len(self.calls))

    # -- the Provider protocol ---------------------------------------------- #

    def complete_json(self, system: str, messages: list[Message], schema: dict,
                      max_tokens: int) -> Turn:
        reply = self._next("complete_json", {"system": system, "messages": list(messages),
                                             "schema": schema, "max_tokens": max_tokens})
        if isinstance(reply, Turn):
            # A copy: the public layer rewrites turn.text in place.
            return Turn(text=reply.text, usage=reply.usage)
        if isinstance(reply, dict):
            return Turn(text=json.dumps(reply))
        if isinstance(reply, str):
            return Turn(text=reply)
        raise AssertionError(f"FakeProvider: complete_json scripted with {type(reply).__name__}")

    def stream_text(self, system: str, messages: list[Message], max_tokens: int) -> Iterator[str]:
        # A generator, like the real providers: the call is recorded and any
        # scripted error raised on the first next(), not at the call.
        reply = self._next("stream_text", {"system": system, "messages": list(messages),
                                           "max_tokens": max_tokens})
        if not isinstance(reply, (list, tuple)):
            raise AssertionError(f"FakeProvider: stream_text scripted with {type(reply).__name__}")
        for item in reply:
            if isinstance(item, BaseException):
                raise item
            yield str(item)

    def complete_text(self, system: str, prompt: str, max_tokens: int) -> str:
        reply = self._next("complete_text", {"system": system, "prompt": prompt,
                                             "max_tokens": max_tokens})
        if not isinstance(reply, str):
            raise AssertionError(f"FakeProvider: complete_text scripted with {type(reply).__name__}")
        return reply
