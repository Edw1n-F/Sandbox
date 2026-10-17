"""Application configuration.

Store mode is configuration, not architecture (ADR-020).  ``STORE_MODE=file``
backs the identical API contract with JSON on disk; ``STORE_MODE=postgis`` is
the documented target and is a swap of ``app.db`` + ``app.services.scene_service``
only -- no route, schema or frontend change.

All coordinate values are EPSG:4326 (degrees) on the wire, per CLAUDE.md.
Measurement maths belongs in a projected CRS and is confined to
``scripts/`` -- see docs/ARCHITECTURE.md (Decision 1, ADR-007).
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    app_title: str = "SDG T1 API"

    # "file" today | "postgis" (documented target, not provisionable on this host)
    store_mode: str = "file"

    # Root of the file store.  Holds scenes/, plans/, interventions/ and rasters/.
    data_dir: Path = BACKEND_DIR / "data"

    # Root of the 3D Tiles model store.  Holds city_model/ and terrain(TB)/.
    model_dir: Path = BACKEND_DIR / "model"

    # Prefix the frontend uses to resolve terrain_url / imagery_url / overlay
    # URLs.  Empty string means "same origin as the API" -- which is what the
    # Vite dev proxy and a single-container deploy both want.
    static_base_url: str = ""

    # Plan/intervention data directories, derived so callers never hand-build paths.
    scenes_dirname: str = "scenes"
    plans_dirname: str = "plans"
    interventions_dirname: str = "interventions"
    rasters_dirname: str = "rasters"

    # GenAI provider. Empty by default: the heuristic provider needs no
    # credentials and the demo must not die for want of a key. Set
    # ANTHROPIC_API_KEY to switch the AI path on; the backend never ships one.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Any OpenAI-compatible endpoint. This is how a live run happens with NO
    # vendor key: Ollama serves an OpenAI-compatible API at /v1, so
    #   LLM_BASE_URL=http://localhost:11434/v1  LLM_MODEL=<model>
    # is a complete live configuration. OpenRouter / DeepSeek / Moonshot /
    # DashScope / Groq / vLLM all speak the same shape.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # Ollama needs a placeholder key but does not check it.
    llm_allow_no_key: bool = True
    # How to ask an OpenAI-compatible endpoint for JSON.
    #   auto -- send response_format, and drop it if the server 400s (default;
    #           most free OpenRouter models reject the parameter)
    #   json -- always send it
    #   off  -- never send it (rely on the contract in the prompt)
    llm_json_mode: str = "auto"
    # Comma-separated model ids tried in order when the primary one fails with a
    # retryable error (429 / 5xx) or answers with empty content. The free tiers
    # are shared pools and are rate-limited without warning, so a single model
    # id is a single point of failure: on OpenRouter ":free" one of them being
    # throttled is the normal case, not the exceptional one.
    llm_fallback_models: str = ""

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    @field_validator("data_dir", mode="after")
    @classmethod
    def _anchor_data_dir(cls, v: Path) -> Path:
        """Resolve a relative DATA_DIR against ``backend/``, never against cwd.

        ``DATA_DIR=./data`` in ``.env`` is the natural thing to write, but
        ``uvicorn app.main:app`` is routinely launched from the repo root or a
        sibling directory.  Anchoring here means the store, the ``/static``
        mount and the seed script all agree no matter where the process starts.
        """
        return v if v.is_absolute() else (BACKEND_DIR / v).resolve()

    @field_validator("model_dir", mode="after")
    @classmethod
    def _anchor_model_dir(cls, v: Path) -> Path:
        """Resolve a relative MODEL_DIR against ``backend/``, never against cwd."""
        return v if v.is_absolute() else (BACKEND_DIR / v).resolve()

    @property
    def scenes_dir(self) -> Path:
        return self.data_dir / self.scenes_dirname

    @property
    def plans_dir(self) -> Path:
        return self.data_dir / self.plans_dirname

    @property
    def interventions_dir(self) -> Path:
        return self.data_dir / self.interventions_dirname

    @property
    def rasters_dir(self) -> Path:
        return self.data_dir / self.rasters_dirname

    def ensure_dirs(self) -> None:
        for path in (
            self.scenes_dir,
            self.plans_dir,
            self.interventions_dir,
            self.rasters_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def public_url(self, relative: str) -> str:
        """Turn a store-relative path such as ``rasters/x.png`` into a URL."""
        rel = relative.lstrip("/")
        # Windows stores ``rasters\wetgrid_hsk.png``; a backslash is not a URL
        # separator, so the browser cannot resolve it. Normalise before joining.
        rel = rel.replace("\\", "/")
        base = self.static_base_url.rstrip("/")
        return f"{base}/{rel}"

    def public_model_url(self, relative: str) -> str:
        """Turn a model-relative path such as ``city_model/tileset.json`` into a URL."""
        rel = relative.lstrip("/")
        rel = rel.replace("\\", "/")
        base = self.static_base_url.rstrip("/")
        return f"{base}/model/{rel}"


#: Settings fields the runtime UI may override and persist to disk. Stored under
#: ``data/llm_config.json`` using these snake_case names; ``.env`` remains the
#: deployment baseline the overrides layer on top of.
LLM_OVERRIDE_FIELDS: tuple[str, ...] = (
    "anthropic_api_key",
    "anthropic_model",
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_json_mode",
    "llm_allow_no_key",
)


def _llm_config_file(s: Settings) -> Path:
    """The on-disk location of runtime LLM overrides, inside the file store."""
    return s.data_dir / "llm_config.json"


def _read_llm_overrides(s: Settings) -> dict[str, object]:
    """Load persisted runtime overrides, ignoring unknown keys and bad files."""
    try:
        raw = json.loads(_llm_config_file(s).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in LLM_OVERRIDE_FIELDS}


def persist_llm_overrides(updates: dict[str, object]) -> None:
    """Merge `updates` into ``data/llm_config.json`` so they survive a restart.

    Only the fields named in `updates` are touched: a field the user never edited
    keeps its ``.env`` source. A failed write is logged, not raised -- the
    in-memory value is already live, and the next save retries the write.
    """
    path = settings.data_dir / "llm_config.json"
    merged = _read_llm_overrides(settings)
    merged.update(updates)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        _logger.warning("could not persist LLM config to %s", path, exc_info=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Runtime overrides (from the config UI) win over the `.env` baseline, so a
    # key typed into the panel is still there after the process restarts.
    for key, value in _read_llm_overrides(s).items():
        setattr(s, key, value)
    return s


settings = get_settings()
