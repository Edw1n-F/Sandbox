"""Application configuration.

Store mode is configuration, not architecture (ADR-020).  ``STORE_MODE=file``
backs the identical API contract with JSON on disk; ``STORE_MODE=postgis`` is
the documented target and is a swap of ``app.db`` + ``app.services.scene_service``
only -- no route, schema or frontend change.

All coordinate values are EPSG:4326 (degrees) on the wire, per CLAUDE.md.
Measurement maths belongs in a projected CRS and is confined to
``scripts/`` -- see docs/OCT6_STATUS.md (ADR-007).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_title: str = "SDG T1 API"

    # "file" today | "postgis" (documented target, not provisionable on this host)
    store_mode: str = "file"

    # Root of the file store.  Holds scenes/, plans/, interventions/ and rasters/.
    data_dir: Path = BACKEND_DIR / "data"

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

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
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
        base = self.static_base_url.rstrip("/")
        return f"{base}/{rel}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
