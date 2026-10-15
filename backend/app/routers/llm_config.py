"""LLM config routes (plan v2 §9).

Runtime, in-memory overrides for the GenAI provider. The server never writes
these back to ``.env``: a restart clears them, which is the correct default for
a prototype where secrets are typed into a form rather than committed.

Policy:
- ``GET`` returns the public fields (``baseUrl``, ``model``, ``jsonMode``,
  ``anthropicModel``, ``configured``). The API key is **never** returned, even
  masked -- a key that reads back is a key the user can leak.
- ``PUT`` accepts any subset of the writable fields. Omitted fields are left
  alone; passing ``null`` for a string clears it. ``apiKey`` is write-only.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from app.config import settings
from app.schemas.scene import WireModel

router = APIRouter(tags=["llm-config"])


class LLMConfigRead(WireModel):
    """Public view of the live LLM configuration (plan v2 §9.2)."""

    # Which provider path currently qualifies as "configured". The frontend
    # status dot uses this directly, so the same source feeds the UI and
    # `/health.llm.configured`.
    configured: bool
    anthropic_model: str = ""
    base_url: str = ""
    model: str = ""
    json_mode: str = "auto"
    allow_no_key: bool = True


class LLMConfigUpdate(WireModel):
    """Writable fields (plan v2 §9.2). All optional; omitted leaves the live
    value in place. Explicit empty string clears a field.
    """

    anthropic_api_key: str | None = Field(default=None)
    anthropic_model: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="OpenAI-compatible key (write-only)")
    model: str | None = None
    json_mode: str | None = None
    allow_no_key: bool | None = None


def _llm_configured() -> bool:
    """The same check `llm.get_providers` applies at construction time, but
    phrased as a boolean so a UI status dot can show it without instantiating
    a provider for every poll.
    """
    if getattr(settings, "anthropic_api_key", "") or "":
        return True
    base = getattr(settings, "llm_base_url", "") or ""
    model = getattr(settings, "llm_model", "") or ""
    return bool(base and model)


@router.get("/llm-config")
def get_llm_config() -> LLMConfigRead:
    """Return the live config, keys stripped."""
    return LLMConfigRead(
        configured=_llm_configured(),
        anthropic_model=settings.anthropic_model,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        json_mode=settings.llm_json_mode,
        allow_no_key=settings.llm_allow_no_key,
    )


@router.put("/llm-config")
def update_llm_config(payload: LLMConfigUpdate) -> LLMConfigRead:
    """Overwrite one or more fields on the live `settings`.

    This is in-memory only -- `.env` is not touched, so the overrides disappear
    on restart. That is deliberate: a demo should not persist secrets without a
    clear write-to-disk opt-in, and running the deploy with the real key in the
    environment is already supported.
    """
    data = payload.model_dump(exclude_none=True)
    if "anthropic_api_key" in data:
        settings.anthropic_api_key = data["anthropic_api_key"]
    if "anthropic_model" in data:
        settings.anthropic_model = data["anthropic_model"]
    if "base_url" in data:
        settings.llm_base_url = data["base_url"]
    if "api_key" in data:
        settings.llm_api_key = data["api_key"]
    if "model" in data:
        settings.llm_model = data["model"]
    if "json_mode" in data:
        settings.llm_json_mode = data["json_mode"]
    if "allow_no_key" in data:
        settings.llm_allow_no_key = data["allow_no_key"]
    return get_llm_config()
