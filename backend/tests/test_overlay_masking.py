"""Tests for render-time building masking on the flood overlay.

The overlays are painted onto 3D Tiles as *classified geometry* (Cesium has no
per-tileset way to exempt buildings), so a wet cell whose grid square falls
under a tower paints the tower's roof. The depth field is solved on bare earth,
which has no building in it, so those cells are suppressed before rendering.

The data must be untouched while the picture is masked, which is the property
these tests pin:

1. ``building_footprint_mask`` produces a real boolean raster over the grid.
2. The rendered ``wetgrid_hsk.png`` is fully transparent wherever a footprint
   stands -- the roof artefact cannot come back.
3. ``wetgrid_hsk.npy`` still holds the un-masked solved depth, so a number that
   counts those cells is unaffected by a display decision.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(BACKEND_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from app.config import settings  # noqa: E402
from _overlay_common import building_footprint_mask, load_overlay  # noqa: E402


def test_mask_is_boolean_over_the_grid():
    ov = load_overlay(5.0)
    mask = building_footprint_mask(ov)
    if mask is None:
        # No city model shipped in this checkout: nothing to mask, and the
        # render paths degrade to unmasked by design rather than failing.
        return
    assert mask.dtype == np.bool_
    assert mask.shape == (ov.rows, ov.cols)
    assert mask.any(), "the site has massing, so some cells must be under it"


def test_rendered_flood_is_transparent_under_buildings():
    """The committed flood PNG must be blank wherever a footprint stands.

    The render masks with the *exact* building outline (``_exact_building_mask``,
    the real polygon), not the axis-aligned envelope ``building_footprint_mask``
    rasterises for the proposal gate: an AABB over-covers a rotated footprint's
    diagonals and would demand transparency over street ponding. The assertion
    therefore checks the same mask the render applied, so the two cannot drift.
    """
    ov = load_overlay(5.0)
    from make_wetgrid import _exact_building_mask

    mask = _exact_building_mask(ov)
    png = settings.rasters_dir / "wetgrid_hsk.png"
    if mask is None or not png.exists():
        return
    alpha = np.array(Image.open(png))[..., 3]
    assert alpha.shape == mask.shape
    assert int(alpha[mask].max()) == 0, (
        "a cell under a building footprint rendered opaque; the flood would "
        "paint the building's roof once classified onto 3D Tiles"
    )


def test_depth_data_is_not_masked():
    """The mask is a display transform: the solved depth keeps every cell."""
    ov = load_overlay(5.0)
    mask = building_footprint_mask(ov)
    npy = settings.rasters_dir / "wetgrid_hsk.npy"
    if mask is None or not npy.exists():
        return
    depth = np.load(npy)
    under = depth[mask]
    assert (under > 0).any(), (
        "the bare-earth solver placed water under a footprint, so masking the "
        "data as well as the render would silently delete solved cells"
    )
