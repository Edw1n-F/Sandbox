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
# Lia's independent check -- docs/DATA_SOURCES.md.
# ---------------------------------------------------------------------------
# Curve Numbers, hydrologic soil group D
#
# Source: USDA NRCS, *Urban Hydrology for Small Watersheds* (TR-55), Table 2-2.
# Each value below cites its row. This table was WRONG until 8 Oct 2026 and the
# correction is worth stating plainly, because it moved the headline numbers:
#
#   green: 68 -> 80    68 is the HSG **A** "open space poor" figure. HSG D's
#                      vegetated rows are 80 (open space good), 78 (meadow) and
#                      77 (woods good). The old value understated runoff on
#                      vegetated land by 12 CN points, and the old comment
#                      claimed it was the "good condition" row while citing a
#                      number that is good-condition in a *different* soil group.
#   bare:  86 -> 94    86 matches no TR-55 row. "Fallow, bare soil" is 94.
#   paved: 96 -> 98    "Paved parking, roofs, driveways, streets" is 98 for
#                      every soil group.
#
# The old values looked reasonable, which is exactly why nothing caught them.
# `tests/test_constants.py` now pins each one against its cited row.
# ---------------------------------------------------------------------------

CN_SOURCE = "USDA NRCS TR-55 (Urban Hydrology for Small Watersheds), Table 2-2"

CN_CITATIONS: dict[str, str] = {
    "paved": "TR-55 Table 2-2, 'Paved parking, roofs, driveways, streets' = 98 (all HSG)",
    "bare": "TR-55 Table 2-2, 'Fallow, bare soil' = 94 (HSG D)",
    "green": (
        "TR-55 Table 2-2, 'Open space, good (>75% grass)' = 80 (HSG D). The ESA "
        "class aggregates tree/shrub/grassland; woods good is 77 and meadow is 78, "
        "so 80 is the conservative end of the vegetated range."
    ),
    "wetland": (
        "No TR-55 row exists for wetland. Retained for transparency only: this "
        "class contributes ZERO generation (see COUNTED_AS and ADR-015), so its "
        "CN is never used. Wetland is modelled as storage, not as a source."
    ),
    "water": "Open water; everything falls as runoff. ~100, the conventional maximum.",
}

CURVE_NUMBER_HSG_D: dict[str, float] = {
    "paved": 98.0,
    "bare": 94.0,
    "green": 80.0,
    "wetland": 90.0,  # unused; see CN_CITATIONS
    "water": 100.0,
}

# Water and wetland are excluded from the *generation* term and counted in the
# *retention* term instead, so a pond is never counted twice (ADR-015).
#
# This was declared but not implemented until 8 Oct 2026: `compute()` zeroed only
# the *water* class, so wetland generated runoff at CN 90 **and** received a
# 0.60 m retention allowance. That is the double count ADR-015 exists to
# prevent -- roughly 2x on the wetland contribution. `make_wetgrid.py` now zeros
# every class listed here as retention, and a test asserts the declaration and the
# implementation agree.
COUNTED_AS: dict[str, str] = {
    "paved": "generation",
    "bare": "generation",
    "green": "generation",
    "wetland": "retention",
    "water": "retention",
}

# Depth (m) a water or wetland cell is assumed to already hold, plus the
# allowance for land that is neither pond nor wetland.
#
# This is the "available storage" term and it is an ASSUMPTION, not a
# measurement: the HSK EIA deliberately does not publish retention volumes for
# the flood storage lakes. DEPRESSION_STORAGE_M is water held in
# micro-topography before runoff leaves a generation cell.
#
# Moved here from make_wetgrid.py on 9 Oct 2026, when the API needed to re-run
# the same retention arithmetic to score a candidate intervention. The service
# and the script have to agree exactly -- a second copy of this table would be a
# silent way to produce a delta the project would not report -- so there is now
# one definition and both import it.
STORAGE_DEPTH_M: dict[str, float] = {"water": 1.50, "wetland": 0.60}
DEPRESSION_STORAGE_M = 0.02


@dataclass
class Overlay:
    """Everything the two scripts need, derived once."""

    bounds: tuple[float, float, float, float]
    rows: int
    cols: int
    cell_m: float
    elevation: np.ndarray  # metres; real DEM when available, else synthetic
    land_class: np.ndarray  # index into LAND_CLASSES
    grid: dict[str, Any]
    terrain_source: str  # "open-meteo" | "synthetic" -- reported, never hidden
    landcover_source: str  # "esa-worldcover" | "synthetic"

    def class_mask(self, name: str) -> np.ndarray:
        return self.land_class == LAND_CLASSES.index(name)

    def cell_of(self, lon: float, lat: float) -> tuple[int, int]:
        """``(row, col)`` for a lon/lat, mirroring ``Grids.cell_of``.

        Deliberately the *same* formula and the same clamping as
        ``app/services/sandbox_model.py:Grids.cell_of`` -- ``col`` from the west
        edge, ``row`` from the north edge, each clamped to ``[0, n-1]``. There is
        one cell-centre map in this project and this is it, read from the overlay
        side; a second copy in the scripts is exactly the drift the "one
        definition of one geometry" rule forbids, and it is what let
        ``make_heatmap.py`` carry its own ``_OverlayGrid`` shim.
        """
        x0, y0, x1, y1 = self.bounds
        col = int((lon - x0) / (x1 - x0) * self.cols)
        row = int((y1 - lat) / (y1 - y0) * self.rows)
        return (max(0, min(self.rows - 1, row)),
                max(0, min(self.cols - 1, col)))

    @property
    def cell_area_m2(self) -> float:
        """The cell area, as a plain float.

        One spelling everywhere since 9 Oct 2026 (STATUS gap #13 closed): the
        attribute form ``ov.cell_area_m2``. The earlier wart -- a ``float``
        subclass that was also callable, bridging the scripts' method-call
        spelling ``ov.cell_area_m2()`` against the canonical attribute spelling
        in ``app/services/design_metrics.py`` -- was collapsed; every caller
        now reads the attribute. It is a ``float`` in every arithmetic and
        JSON sense, and nothing pretends otherwise.
        """
        return float(self.grid["cell_width_m"] * self.grid["cell_height_m"])


def load_overlay(resolution_m: float = 5.0) -> Overlay:
    """Build the working grid, the synthetic surface and the land-class raster."""
    area = load_study_area(settings.data_dir / "scene_hsk.json")
    bounds = polygon_bounds(area["bbox"])
    grid = build_grid(bounds, resolution_m=resolution_m)

    rows, cols = int(grid["rows"]), int(grid["cols"])
    land, land_source = _load_or_synthesise_land_class(rows, cols)
    elevation, terrain_source = _load_or_synthesise_dem(rows, cols, land)

    return Overlay(
        bounds=bounds,
        rows=rows,
        cols=cols,
        cell_m=float(grid["cell_width_m"]),
        elevation=elevation,
        land_class=land,
        grid=grid,
        terrain_source=terrain_source,
        landcover_source=land_source,
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


def _load_or_synthesise_dem(
    rows: int, cols: int, land: np.ndarray
) -> tuple[np.ndarray, str]:
    """Use the fetched DEM when it exists, else the synthetic surface.

    `fetch_dem.py` writes `dem_hsk.npy` over the same bbox as the analysis grid
    but at a coarser posting (1 arc-second, ~30 m, versus 5 m), so it is
    bilinearly resampled. Only continuous fields are resampled this way -- class
    codes never would be, which is exactly the distinction that matters when a
    10 m land-cover product and a ~30 m elevation product share a 5 m analysis
    grid.

    The terrain source is returned so every script reports which one it used,
    rather than the provenance string being hard-coded somewhere and going stale
    the moment real data arrives. (It already went stale once, which is why.)
    """
    dem_file = settings.data_dir / "dem_hsk.npy"
    if dem_file.exists():
        try:
            dem = np.load(dem_file).astype(np.float64)
            if dem.ndim == 2 and dem.shape[0] > 1 and dem.shape[1] > 1:
                if not np.isnan(dem).any():
                    return _bilinear_resample(dem, rows, cols), "open-meteo"
        except (OSError, ValueError):
            pass
    return _synthetic_surface(rows, cols, land), "synthetic"


def _bilinear_resample(src: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Bilinear resample a north-up field onto a finer grid over the same bbox."""
    src_rows, src_cols = src.shape
    fy = np.clip((np.arange(rows) + 0.5) * src_rows / rows - 0.5, 0.0, src_rows - 1.0)
    fx = np.clip((np.arange(cols) + 0.5) * src_cols / cols - 0.5, 0.0, src_cols - 1.0)
    y0 = np.floor(fy).astype(int)
    x0 = np.floor(fx).astype(int)
    y1 = np.minimum(y0 + 1, src_rows - 1)
    x1 = np.minimum(x0 + 1, src_cols - 1)
    wy = (fy - y0)[:, None]
    wx = (fx - x0)[None, :]
    top = src[np.ix_(y0, x0)] * (1 - wx) + src[np.ix_(y0, x1)] * wx
    bottom = src[np.ix_(y1, x0)] * (1 - wx) + src[np.ix_(y1, x1)] * wx
    return top * (1 - wy) + bottom * wy


def _load_or_synthesise_land_class(rows: int, cols: int) -> tuple[np.ndarray, str]:
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
                return data.astype(np.int16), "esa-worldcover"
        except (OSError, ValueError):
            pass  # unreadable -> fall through to the surrogate
    land = _synthesise_land_class(rows, cols)
    try:
        np.save(cached, land)
    except OSError:
        pass
    return land, "synthetic"


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
