"""LLM config routes (plan v2 §9).

Runtime overrides for the GenAI provider. A ``PUT`` updates the live `settings`
**and** persists the edited fields to ``data/llm_config.json`` (see
:func:`app.config.persist_llm_overrides`), so a key typed into the panel survives
a restart instead of silently reverting to ``.env``.

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

from app.config import persist_llm_overrides, settings
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
    """Overwrite one or more fields on the live `settings` and persist them.

    The edited fields are written to ``data/llm_config.json`` so the panel's
    choices survive a restart; ``.env`` stays the deployment baseline for fields
    the user never touched.
    """
    # Wire (camelCase) field -> settings attribute name, in one place so the
    # Pydantic model, the live settings and the on-disk override cannot drift.
    field_map = {
        "anthropic_api_key": "anthropic_api_key",
        "anthropic_model": "anthropic_model",
        "base_url": "llm_base_url",
        "api_key": "llm_api_key",
        "model": "llm_model",
        "json_mode": "llm_json_mode",
        "allow_no_key": "llm_allow_no_key",
    }
    data = payload.model_dump(exclude_none=True)
    updates: dict[str, object] = {}
    for wire_field, setting_name in field_map.items():
        if wire_field in data:
            setattr(settings, setting_name, data[wire_field])
            updates[setting_name] = data[wire_field]
    persist_llm_overrides(updates)
    return get_llm_config()
