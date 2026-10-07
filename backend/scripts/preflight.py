#!/usr/bin/env python
"""Check this machine can actually run the project, before you find out the hard way.

    python scripts/preflight.py

Exists because the most common failure here is an *environment* failure that
looks like a code failure. A stale 3.10 ``.venv`` without numpy raises
``ModuleNotFoundError: numpy`` from inside a raster script, which reads as "the
script is broken" rather than "you are on the wrong interpreter". This says so
in one line, up front.

Exit code is 0 when the project can run, 1 when it cannot. Warnings do not fail
the check.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 13)

errors: list[str] = []
warnings: list[str] = []


def check(label: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> None:
    mark = "ok  " if ok else ("warn" if warn_only else "FAIL")
    line = f"  [{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        (warnings if warn_only else errors).append(label)


print("preflight\n")

# --- interpreter ---------------------------------------------------------- #
version = sys.version_info[:2]
check(
    "Python version",
    version >= MIN_PYTHON,
    f"{version[0]}.{version[1]} at {sys.executable}",
    warn_only=True,
)
if version < RECOMMENDED_PYTHON:
    check(
        "Python is the recommended version",
        False,
        f"recommended {RECOMMENDED_PYTHON[0]}.{RECOMMENDED_PYTHON[1]}+; "
        "raster scripts were last verified on it",
        warn_only=True,
    )

# The trap this script exists for: a venv in the repo that is not this one.
repo_venv = BACKEND_DIR / ".venv"
if repo_venv.exists():
    other = repo_venv / "bin" / "python"
    if other.exists() and other.resolve() != Path(sys.executable).resolve():
        check(
            "repo .venv is not shadowing this interpreter",
            False,
            f"{repo_venv} exists but you are running {sys.executable}. "
            "If a script fails with ModuleNotFoundError, activate the right "
            "one: source backend/.venv/bin/activate",
            warn_only=True,
        )

# --- packages ------------------------------------------------------------- #
for module, why in (
    ("fastapi", "the API"),
    ("pydantic", "validation"),
    ("numpy", "the raster scripts"),
    ("PIL", "PNG output"),
    ("rasterio", "reading ESA WorldCover (fetch_landcover.py only)"),
):
    try:
        importlib.import_module(module)
        check(f"package {module}", True)
    except ImportError:
        check(f"package {module}", False, f"needed for {why} — pip install -r requirements.txt")

# --- configuration -------------------------------------------------------- #
try:
    from app.config import settings

    base = settings.static_base_url
    check(
        "STATIC_BASE_URL matches the /static mount",
        base.rstrip("/") == "/static",
        f"currently {base!r}" if base else "empty — overlay and terrain URLs will 404",
    )
except Exception as exc:  # noqa: BLE001
    check("configuration loads", False, str(exc))

# --- data ----------------------------------------------------------------- #
data = BACKEND_DIR / "data"
for rel, why in (
    ("scene_hsk.json", "the locked study area (seed_scene.py)"),
    ("scenes", "a seeded scene"),
    ("dem_hsk.npy", "real elevation (fetch_dem.py)"),
    ("landclass_hsk.npy", "real land cover (fetch_landcover.py)"),
    ("rasters/wetgrid_hsk.png", "the wet-grid overlay (make_wetgrid.py)"),
    ("rasters/heatmap_hsk.png", "the heat overlay (make_heatmap.py)"),
):
    path = data / rel
    check(
        f"data/{rel}",
        path.exists(),
        "" if path.exists() else f"missing — needed for {why}",
        warn_only=(rel != "scene_hsk.json"),
    )

# --- the seeded scene id the frontend needs ------------------------------- #
scenes = sorted((data / "scenes").glob("*.json")) if (data / "scenes").exists() else []
if scenes:
    print(f"  [ok  ] VITE_SCENE_ID for frontend/.env.local:")
    print(f"         {scenes[0].stem}")
else:
    check(
        "a scene exists",
        False,
        "run: python scripts/seed_scene.py",
    )

# --- summary -------------------------------------------------------------- #
print()
if errors:
    print(f"NOT READY — {len(errors)} blocking problem(s):")
    for item in errors:
        print(f"  - {item}")
    sys.exit(1)

if warnings:
    print(f"Ready, with {len(warnings)} warning(s) above.")
else:
    print("Ready.")
sys.exit(0)
