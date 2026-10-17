"""The prompt package: three prompts, one per application scene.

- :mod:`app.agent.prompts.chat` -- the chat, slim: title + sentence + intent.
- :mod:`app.agent.prompts.result_analytics` -- the run-explanation card, full.
- :mod:`app.agent.prompts.intervention` -- site selection, unchanged logic.

This ``__init__`` is a compatibility layer: the module used to be a single
``prompts.py`` and callers import ``SYSTEM``, ``RUN_SYSTEM``, ``render_facts``,
``user_turn`` and friends from here. Those names are re-exported so no caller
needed to change when the single module became a package.
"""

from __future__ import annotations

from app.agent.prompts._shared import render_facts, user_turn
from app.agent.prompts.chat import SYSTEM
from app.agent.prompts.result_analytics import RUN_SYSTEM, RUN_CONTRACT, system_with_contract

#: The full reply contract (the run-explanation card's). Kept under the old
#: name for callers that still reach for it; the chat has its own slim contract.
JSON_CONTRACT = RUN_CONTRACT


def json_system() -> str:
    """`SYSTEM` plus its reply contract: the chat's non-forced prompt."""
    from app.agent.prompts.chat import system_with_contract as _chat_system

    return _chat_system()


def json_system_for(system: str) -> str:
    """A non-forced (streamed or `response_format`-less) call needs the contract."""
    return f"{system}\n\n{JSON_CONTRACT}"


__all__ = [
    "SYSTEM",
    "RUN_SYSTEM",
    "JSON_CONTRACT",
    "RUN_CONTRACT",
    "json_system",
    "json_system_for",
    "system_with_contract",
    "render_facts",
    "user_turn",
]
