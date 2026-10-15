"""FastAPI application entry point.

Responsibilities beyond wiring:

* **B8** -- serve ``backend/data`` at ``/static`` so overlay URLs actually
  resolve over HTTP.  Without this the viewer gets a ``terrainUrl`` /
  ``imageryUrl`` it cannot fetch, and the chain is broken at the last hop.
* **One error envelope everywhere** (ADR-028).  §3.5 says both "422 returns
  FastAPI's default detail array" and "externally always use
  ``{"error": {...}}``".  Those contradict, so both are satisfied: handlers
  emit ``error`` *and* keep ``detail``, and every body also carries a machine
  ``code``.  The frontend therefore needs one error branch, not three.
"""

import mimetypes

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.routers import chat, citymodel, llm_config, plans, scenes, terrain

# Starlette's StaticFiles uses the Python mimetypes module. 3D Tiles binary
# formats are not registered by default, which causes them to be served as
# text/plain and breaks Cesium's loader.
mimetypes.add_type("application/octet-stream", ".b3dm")
mimetypes.add_type("application/octet-stream", ".pnts")
mimetypes.add_type("application/octet-stream", ".i3dm")
mimetypes.add_type("application/octet-stream", ".cmpt")
from app.services.scene_service import ResourceNotFound

app = FastAPI(
    title=settings.app_title,
    version="0.1.0",
    description=(
        "Scene management and configuration module for the Hack4SDG 3D sandbox. "
        "Single data authority: all reads and writes go through this module "
        "(CLAUDE.md). Store mode is `file`; see docs/ARCHITECTURE.md (Decision 1)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scenes.router, prefix="/api")
app.include_router(plans.router, prefix="/api")
app.include_router(terrain.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(citymodel.router, prefix="/api")
app.include_router(llm_config.router, prefix="/api")


# --------------------------------------------------------------------------- #
# Unified error envelope
# --------------------------------------------------------------------------- #


def _envelope(
    status_code: int, code: str, message: str, details: object = None
) -> JSONResponse:
    body: dict[str, object] = {
        "detail": message,
        "error": {"code": code, "message": message},
    }
    if details is not None:
        body["error"]["details"] = details  # type: ignore[index]
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(ResourceNotFound)
async def _not_found_handler(_: Request, exc: ResourceNotFound) -> JSONResponse:
    return _envelope(status.HTTP_404_NOT_FOUND, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return _envelope(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "VALIDATION_ERROR",
        "request validation failed",
        details=jsonable_errors(exc.errors()),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
        exc.status_code, f"HTTP_{exc.status_code}"
    )
    return _envelope(exc.status_code, code, str(exc.detail))


def jsonable_errors(errors: list[dict]) -> list[dict]:
    """Strip non-serialisable ``ctx`` values out of pydantic error dicts.

    Pydantic attaches the original ``ValueError`` object for custom validators,
    which is not JSON-encodable and would otherwise turn a 422 into a 500.
    """
    cleaned: list[dict] = []
    for err in errors:
        item = {k: v for k, v in err.items() if k != "ctx"}
        item["loc"] = [str(part) for part in err.get("loc", ())]
        cleaned.append(item)
    return cleaned


# --------------------------------------------------------------------------- #
# Static overlays
# --------------------------------------------------------------------------- #

settings.ensure_dirs()
# Mount /static/model before /static: Starlette matches mounts in registration
# order and /static would otherwise greedily consume /static/model/* paths.
if settings.model_dir.exists():
    app.mount(
        "/static/model",
        StaticFiles(directory=str(settings.model_dir)),
        name="static-model",
    )
app.mount(
    "/static",
    StaticFiles(directory=str(settings.data_dir)),
    name="static",
)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness, plus the demo machine's GenAI state.

    `llm` is here so that on the day, whether the demo is running a real model
    or the deterministic fallback is one `curl` away rather than a surprise. It
    reports *configured*, never *working* -- only a real call proves that, and
    `scripts/llm_probe.py` is what proves it. Reporting a green light for an
    unverified path is exactly the overclaiming the spec forbids.
    """
    from app.services.ai_plan import live_providers

    providers = live_providers()
    return {
        "status": "ok",
        "storeMode": settings.store_mode,
        "dataDir": str(settings.data_dir),
        "llm": {
            "configured": bool(providers),
            # Which one `auto` would pick, and what it is.
            "provider": providers[0].name if providers else "heuristic",
            "isAi": bool(providers),
            "model": (
                settings.anthropic_model if providers and providers[0].name == "claude"
                else settings.llm_model if providers else None
            ),
            "baseUrl": (
                None if providers and providers[0].name == "claude"
                else settings.llm_base_url or None
            ),
            "heuristicIsNotAi": not bool(providers),
        },
    }


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "service": settings.app_title,
        "docs": "/docs",
        "health": "/health",
    }
