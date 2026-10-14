"""Geospatial ingest helpers -- the vector half of §4.3.

Scope for Oct 6 is deliberately narrow: normalise a GeoJSON geometry to
EPSG:4326, validate it, and read a study-area definition off disk.  The PostGIS
ingest path (ogr2ogr / ST_Transform) and the raster COG path (gdal_translate +
MinIO upload) are the documented target and are *not* implemented, because
neither toolchain can be provisioned on this machine (ADR-012).

What survives the swap is the contract: everything downstream keeps consuming
``scene.terrain_url`` / ``scene.imagery_url`` as opaque references.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.config import settings


class GeoValidationError(ValueError):
    """Raised when ingested geometry violates the spec's own rules."""


def load_study_area(path: Path | None = None) -> dict[str, Any]:
    """Load ``backend/data/scene_hsk.json`` (or another study-area file)."""
    target = path or (settings.data_dir / "scene_hsk.json")
    if not target.exists():
        raise FileNotFoundError(
            f"Study-area file not found: {target}. "
            "It is the single source of truth for the bbox -- see docs/DATA_SOURCES.md."
        )
    with target.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _iter_positions(coords: Any):
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        yield float(coords[0]), float(coords[1])
        return
    if isinstance(coords, (list, tuple)):
        for item in coords:
            yield from _iter_positions(item)


def polygon_bounds(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return (lon_min, lat_min, lon_max, lat_max) for a Polygon/Point geometry."""
    positions = list(_iter_positions(geometry.get("coordinates", [])))
    if not positions:
        raise GeoValidationError("Geometry has no positions to derive bounds from")
    lons = [p[0] for p in positions]
    lats = [p[1] for p in positions]
    return min(lons), min(lats), max(lons), max(lats)


def close_ring(geometry: dict[str, Any]) -> dict[str, Any]:
    """Return a Polygon whose outer ring is explicitly closed (ADR-027)."""
    out = dict(geometry)
    rings = [list(ring) for ring in out.get("coordinates", [])]
    if not rings or not rings[0]:
        raise GeoValidationError("Polygon has no coordinates")
    outer = rings[0]
    if len(outer) >= 3 and outer[0] != outer[-1]:
        outer.append(list(outer[0]))
    out["coordinates"] = rings
    return out


def build_grid(
    bounds: tuple[float, float, float, float], resolution_m: float = 5.0
) -> dict[str, Any]:
    """Derive a metric working grid from a lon/lat extent.

    §4.4 mandates EPSG:4326 on the wire; ADR-007 carves out an explicit
    exception for *measurement*.  This is that choke point: degrees are
    converted to metres exactly once, here, using a local equirectangular
    approximation about the extent centre.  At a 3 km extent the error against
    HK1980 Grid is far below the 5 m cell size, and nothing projected ever
    leaves this function.
    """
    lon_min, lat_min, lon_max, lat_max = bounds
    lat0 = math.radians((lat_min + lat_max) / 2.0)

    m_per_deg_lat = 111_132.92 - 559.82 * math.cos(2 * lat0) + 1.175 * math.cos(4 * lat0)
    m_per_deg_lon = 111_412.84 * math.cos(lat0) - 93.5 * math.cos(3 * lat0)

    width_m = (lon_max - lon_min) * m_per_deg_lon
    height_m = (lat_max - lat_min) * m_per_deg_lat

    cols = max(1, int(round(width_m / resolution_m)))
    rows = max(1, int(round(height_m / resolution_m)))

    return {
        "bounds": bounds,
        "resolution_m": resolution_m,
        "rows": rows,
        "cols": cols,
        "cell_width_m": width_m / cols,
        "cell_height_m": height_m / rows,
        "width_m": width_m,
        "height_m": height_m,
        "area_m2": width_m * height_m,
        "crs_note": (
            "Metres via local equirectangular approximation about the extent "
            "centre. For volume reporting, swap HK1980 Grid (EPSG:2326)."
        ),
    }
