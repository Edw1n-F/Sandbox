"""Shared raster plumbing for the overlay scripts (D1-D3).

One working grid, one surface, one land-class raster -- so
``make_wetgrid.py`` and ``make_heatmap.py`` cannot disagree about the extent,
the resolution or the cell size. Everything derives from
``backend/data/scene_hsk.json``; nothing is hard-coded.

**Terrain is synthetic.** The Lands Dept 5 m DTM (~290 MB) was not downloaded:
it needs explicit permission and it is *not* bare-earth (it carries elevated
roads and depicts vegetation at vegetation height), so using it naively would
bias a volume figure in exactly the quantity being computed.

This is the "synthetic elevation + water/wetland mask" fallback from the Oct 6
brief: a *screening view*, not a result.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.geo_import import build_grid, load_study_area, polygon_bounds  # noqa: E402

# Ordered: the first match wins when classes are painted in order.
LAND_CLASSES = ("water", "wetland", "green", "bare", "paved")

# Relative heat-exposure index, 0-1. Paved and bare hold heat; water and
# wetland cool through evaporation. A *relative* screening index, not degrees.
HEAT_INDEX: dict[str, float] = {
    "paved": 1.00,
    "bare": 0.85,
    "green": 0.40,
    "wetland": 0.20,
    "water": 0.05,
}

# SCS Curve Number by land class, hydrologic soil group D (conservative).
# Source: USDA NRCS TR-55 tables. HSG D is assumed for HSK/HT and still needs
# Lia's independent check -- docs/OCT6_STATUS.md 2.3.
CURVE_NUMBER_HSG_D: dict[str, float] = {
    "paved": 96.0,
    "bare": 86.0,
    "green": 68.0,  # "good condition" row; poor condition would be ~79
    "wetland": 90.0,
    "water": 100.0,  # open water: everything falls as runoff
}

# Water and wetland are excluded from the *generation* term and counted in the
# *retention* term instead, so a pond is never counted twice (ADR-015).
COUNTED_AS: dict[str, str] = {
    "paved": "generation",
    "bare": "generation",
    "green": "generation",
    "wetland": "retention",
    "water": "retention",
}


@dataclass
class Overlay:
    """Everything the two scripts need, derived once."""

    bounds: tuple[float, float, float, float]
    rows: int
    cols: int
    cell_m: float
    elevation: np.ndarray  # metres, SYNTHETIC
    land_class: np.ndarray  # index into LAND_CLASSES
    grid: dict[str, Any]

    def class_mask(self, name: str) -> np.ndarray:
        return self.land_class == LAND_CLASSES.index(name)

    def cell_area_m2(self) -> float:
        return float(self.grid["cell_width_m"] * self.grid["cell_height_m"])


def load_overlay(resolution_m: float = 5.0) -> Overlay:
    """Build the working grid, the synthetic surface and the land-class raster."""
    area = load_study_area(settings.data_dir / "scene_hsk.json")
    bounds = polygon_bounds(area["bbox"])
    grid = build_grid(bounds, resolution_m=resolution_m)

    rows, cols = int(grid["rows"]), int(grid["cols"])
    land = _load_or_synthesise_land_class(rows, cols)
    elevation = _synthetic_surface(rows, cols, land)

    return Overlay(
        bounds=bounds,
        rows=rows,
        cols=cols,
        cell_m=float(grid["cell_width_m"]),
        elevation=elevation,
        land_class=land,
        grid=grid,
    )


def runoff_depth(rainfall_mm: float, curve_number: np.ndarray) -> np.ndarray:
    """SCS-CN runoff depth in metres, for a rainfall depth in mm.

    S = (25400 / CN) - 254                 [mm, potential retention]
    Q = (P - 0.2 S)^2 / (P + 0.8 S)        [mm, runoff]

    The constant is **254**, not 2540. Derivation: S_inches = 1000/CN - 10, and
    1 in = 25.4 mm, so S_mm = 25400/CN - 254. The `2540` variant is the
    factor-of-ten error the source documents warn about; it returns *negative*
    retention for any CN above ~91, which then makes the denominator of Q
    negative and the runoff figure astronomically large. It is invisible in a
    picture, so `tests/test_constants.py` pins it against a worked example.
    """
    cn = np.clip(np.asarray(curve_number, dtype=np.float64), 1.0, 100.0)
    s = (25400.0 / cn) - 254.0
    p = max(float(rainfall_mm), 0.0)
    q = np.where(
        p > 0.2 * s,
        ((p - 0.2 * s) ** 2) / np.maximum(p + 0.8 * s, 1e-9),
        0.0,
    )
    return q / 1000.0  # mm -> m


def _lonlat_axes(rows: int, cols: int):
    """Normalised east/north axes, both 0..1, north-up (row 0 = northernmost)."""
    u = np.linspace(0.0, 1.0, cols)
    v = np.linspace(0.0, 1.0, rows)
    return np.broadcast_to(u, (rows, cols)), np.broadcast_to(v[:, None], (rows, cols))


def _synthetic_surface(rows: int, cols: int, land: np.ndarray) -> np.ndarray:
    """A plausible, explicitly-synthetic surface for HSK/HT.

    HSK/HT is inland new-town territory draining generally south-east toward the
    Kam Tin River and Deep Bay, so the surface is tilted that way, with a
    low-amplitude ridge so that "fill from the lowest cells upward" is not a
    trivial single sort. Water and wetland cells are sunk below their
    surroundings, giving the wet grid a believable basin to collect in.

    This is NOT a DEM. It carries no surveyed elevation and must never be
    reported as one.
    """
    un, vn = _lonlat_axes(rows, cols)
    noise = 0.5 * np.sin(9.0 * np.pi * un + 0.7) * np.cos(7.0 * np.pi * vn)

    surface = 18.0 * (1.0 - 0.55 * un - 0.45 * vn)          # regional tilt, ~18 m
    surface += 3.0 * np.sin(3.0 * np.pi * un) * np.cos(2.0 * np.pi * vn)  # ridge
    surface += 1.2 * noise
    surface[land == LAND_CLASSES.index("water")] -= 3.0
    surface[land == LAND_CLASSES.index("wetland")] -= 1.2
    return surface.astype(np.float64)


def _load_or_synthesise_land_class(rows: int, cols: int) -> np.ndarray:
    """Load a land-class grid if one exists, else synthesise a placeholder.

    Lia owns the real land-class grid. Until it exists, a deterministic
    surrogate is used and recorded as a placeholder in the PNG metadata, so the
    heatmap can be demonstrated without anyone mistaking it for measured land
    cover. Drop a `landclass_hsk.npy` of shape (rows, cols) holding indices into
    LAND_CLASSES to replace it.
    """
    cached = settings.data_dir / "landclass_hsk.npy"
    if cached.exists():
        try:
            data = np.load(cached)
            if data.shape == (rows, cols):
                return data.astype(np.int16)
        except (OSError, ValueError):
            pass  # unreadable -> fall through to the surrogate
    land = _synthesise_land_class(rows, cols)
    try:
        np.save(cached, land)
    except OSError:
        pass
    return land


def _synthesise_land_class(rows: int, cols: int) -> np.ndarray:
    """A deterministic, structured placeholder -- NOT measured land cover.

    Structure only: green/bare upland in the north-west, a paved/urban plain in
    the south-east (the NDA's formed land), and water/wetland bands along two
    drainage lines running to the south-east corner. Enough shape for the wet
    grid to pool somewhere believable and for the heatmap to show contrast.
    """
    un, vn = _lonlat_axes(rows, cols)
    field = un * 0.6 + vn * 0.4
    noise = 0.5 * np.sin(9.0 * np.pi * un + 0.7) * np.cos(7.0 * np.pi * vn)
    field = field + 0.22 * noise

    idx = {name: i for i, name in enumerate(LAND_CLASSES)}
    land = np.full((rows, cols), idx["green"], dtype=np.int16)

    urban = field > 0.52
    land[urban] = idx["paved"]
    land[urban & (np.abs(field - 0.52) < 0.06)] = idx["bare"]

    for offset in (0.34, 0.62):
        line = np.abs(field - offset) < 0.022
        land[line] = idx["water"]
        fringe = np.abs(field - offset) < 0.045
        land[fringe & ~line] = idx["wetland"]

    return land


def rgba_from_class(
    land: np.ndarray, colours: dict[str, tuple[int, int, int, int]]
) -> np.ndarray:
    """Map a class-index array to an RGBA image via integer lookup tables."""
    lut = np.zeros((len(LAND_CLASSES), 4), dtype=np.uint8)
    for i, name in enumerate(LAND_CLASSES):
        lut[i] = colours[name]
    return lut[land]


def attach_to_scene(kind: str, png_path: Path) -> str | None:
    """Register a generated overlay against the seeded scene.

    Fills the §4.1 reserved field (`wet_grid_path` / `heatmap_path`) so the API
    starts returning a real URL and the LayerToggle switches light up. This is
    what makes the reserved-field claim testable rather than aspirational.

    Returns the stored path, or None when no seeded scene exists yet.
    """
    from app import db
    from app.services.scene_service import attach_overlay

    if kind not in ("wet_grid_path", "heatmap_path"):
        raise ValueError(f"unknown overlay kind: {kind}")

    for doc_path in db.list_docs(settings.scenes_dir):
        doc = db.read_doc(doc_path)
        if not doc or not doc.get("study_area"):
            continue
        rel = str(Path(png_path).relative_to(settings.data_dir))
        attach_overlay(doc["id"], kind, rel)
        return rel
    return None
