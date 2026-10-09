"""Urban design metrics derived from geometry, each tied to a published rule.

Every metric here answers a rule that Hong Kong's own air ventilation guidance
states, and computes it from the 2,639 envelopes and their heights. None of them
is a comfort prediction and none is a pass/fail verdict.

## Sources, and what they actually say

Ng & Cheng, *Policies and technical guidelines for urban planning of high-density
cities - air ventilation assessment (AVA) of Hong Kong*, Building and Environment
44 (2009) 1478-1488. This is the document behind Hong Kong's AVA policy. The
relevant rules, verbatim:

- "**Site coverage has a greater impact than that of building height** on [air
  quality]." (6.2 Podium/site coverage)
- "The podia with large site coverage not only block most of the [airflow]"
  (6.2)
- "it is better to have **varying heights rather than similar/uniform height**"
  (6.4 Building heights)
- "a probable strategy for improving air ventilation is by **varying building
  heights for diverting winds to the lower levels**" (6.4)
- "The provision of **permeability/gaps nearer to the pedestrian level is far
  more important than that at high levels**. Create permeability in the housing
  blocks. Try to create **voids at ground level**." (6.5 Building permeability)
- "For air ventilation assessment, **sixteen wind directions** are considered.
  The VR of each direction (VRi) will then be factored with the probability of
  wind coming from that direction." (6, Fig. 6)
- "A design having a **higher velocity ratio** would be considered as a better
  design than one having a lower velocity ratio... whether the better design
  option meets a standard cannot be known." (9 Policy implementation)

## The comfort conversion, and where it comes from

Three quantities from section 7 of the same document compose into a usable
threshold:

- comfortable pedestrian breeze under shade at Hong Kong's summer 28 degC:
  **1.0 to 1.5 m/s**, midpoint 1.25
- mean roof-top (~100 m) Hong Kong urban canopy layer wind: ~2.5 m/s
- extrapolated boundary-layer (500 m) wind: **6 to 8 m/s**, midpoint 7

so comfortable VR ~= 1.25 / 7 ~= **0.18**. The same document gives the empirical
reference bands: **0.3** near the waterfront and in open spaces, **0.05-0.1** in
streets and congested urban spaces.

This is a screening band derived from three cited figures, not a standard. Nobody
publishes a standard; the document says so itself. It is used to describe a
scheme's ventilation alongside its drainage and shade, never to pass or fail it.

## What these are not

- **Not CFD.** Frontal area density is geometry. No flow is solved.
- **Not a comfort index.** No UTCI, no PET, no SET.
- **Not a shadow simulation.** :func:`shade_fraction` casts real shadows from real
  geometry at a real solar position, but it is a single instant, not a daily
  integral, and it says so.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

M_PER_DEG_LAT = 110_540.0

# Meteorological convention: the direction a wind comes FROM. Named explicitly
# because "wind from the east" and "wind towards the east" differ by 180 degrees,
# and getting that backwards points every building at the wrong side.
CARDINALS: dict[str, float] = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5, "E": 90.0, "ESE": 112.5,
    "SE": 135.0, "SSE": 157.5, "S": 180.0, "SSW": 202.5, "SW": 225.0,
    "WSW": 247.5, "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}

# Published reference bands, from the AVA policy document. Empirical expectations
# from wind tunnel and CFD studies of Hong Kong, NOT acceptance criteria -- the
# same document says whether a scheme meets a standard cannot be known.
VR_OPEN_SPACE = 0.30
VR_CONGESTED_STREET = (0.05, 0.10)
VR_COMFORTABLE_PEDESTRIAN = 1.25 / 7.0   # ~0.179, derived in the module docstring

# Height of the sun-bearing facade and the shadow it casts, per metre of building
# height, at a given solar altitude. Used only for the geometry in
# `shadow_reach_m`, and only where the building's own shade reaches the ground.
GROUND_LEVEL_BAND_M = 12.0

RULE_SOURCES: dict[str, str] = {
    "heightVariationCv":
        "AVA policy 6.4: 'it is better to have varying heights rather than "
        "similar/uniform height'",
    "siteCoverage":
        "AVA policy 6.2: 'site coverage has a greater impact than that of "
        "building height on [air quality]'",
    "groundLevelVoidFraction":
        "AVA policy 6.5: 'permeability/gaps nearer to the pedestrian level is "
        "far more important than that at high levels'",
    "frontalAreaDensity":
        "AVA policy 6, Fig. 6: sixteen wind directions, each factored by the "
        "probability of wind from that direction",
    "shadeFraction":
        "AVA policy 7: designers should 'minimize solar radiation' to achieve "
        "comfort; shade is the solar lever",
}

# --------------------------------------------------------------------------- #
# Sky-view factor and the heat blend
# --------------------------------------------------------------------------- #
#
# The AVA method carries sixteen wind directions (policy 6, Fig. 6); the same
# sixteen-azimuth convention is reused here so "one geometry is read again"
# rather than a second angular grid invented. 300 m is about 2.3x the tallest
# envelope in the district (131.1 m), enough that the tallest building still
# occludes at the horizon while keeping the fully vectorised search affordable.
SVF_AZIMUTH_BINS: int = 16
SVF_SEARCH_RADIUS_M: float = 300.0

# The shadow sweep's hard cap, in cells. Kept as a named constant now that the
# sweep lives in shade_mask(), so the cap is stated once instead of twice.
SHADOW_MAX_STEPS: int = 400

# The blend: land-class index, sky exposure (1 - SVF) and unshaded ground
# (1 - shade) into one relative field. The weights are a display convention and
# are echoed in every response; they are normalised to sum 1 before use.
HEAT_BLEND_WEIGHTS: dict[str, float] = {"land": 0.5, "svf": 0.3, "shadow": 0.2}

# The one status every heat surface carries. Heat is not a temperature, and no
# anchoring claim is made until a real Landsat LST scene or the HKO normals are
# actually ingested (PRD P2-1).
HEAT_STATUS: str = "RELATIVE_INDEX_NOT_A_TEMPERATURE"
HEAT_ANCHOR_STATUS: str = "ANCHORED_TO_PUBLISHED_REFERENCE"
HEAT_CALIBRATION_PATH: str = "reference/heat_anchor.json"

HEAT_WARNING: str = (
    "Heat is not a temperature. This is a relative heat-exposure index on a 0-1 "
    "scale derived from land class, sky view and shade -- not degrees Celsius "
    "and not a modelled air-temperature field. Screening-level; relative, not "
    "absolute."
)
HEAT_ANCHOR_WARNING: str = (
    "No real LST/HKO calibration is present; the scale is not anchored to "
    "observed data. Screening-level; relative, not absolute."
)

_SVF_METHOD: str = (
    "Per-cell sky view factor from a horizon search over the massing height "
    "raster: SVF = 1 - (1/N) sum_i sin(gamma_i), where gamma_i is the horizon "
    "elevation angle in azimuth bin i (16 bins), searched to a 300 m (60-cell) "
    "radius. A cell under massing is inside the building and scores 0."
)
_SVF_LIMITATION: str = (
    "Geometry, not radiation. SVF is a sky-exposure fraction -- how much sky a "
    "ground point sees -- not a temperature. No sun, no surface energy balance, "
    "no air temperature. The horizon is sampled on 16 azimuth rays, so a narrow "
    "slot between two buildings can fall between rays and be missed."
)
_HEAT_NOTE: str = (
    "The field is w_land*landIndex + w_svf*(1 - SVF) + w_shadow*(1 - shaded), "
    "weights normalised to sum 1. The land-class index is hand-authored and "
    "unverified; the SVF and shade are geometry. Nothing here is a temperature."
)


@dataclass(frozen=True)
class Footprint:
    """One massing envelope, in metres, with its centroid."""

    lon: float
    lat: float
    width_m: float
    depth_m: float
    height_m: float
    bearing_deg: float = 0.0

    @property
    def area_m2(self) -> float:
        return self.width_m * self.depth_m


def _metres_per_degree_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def footprints_from_geojson(features: list[dict[str, Any]]) -> list[Footprint]:
    """Our GeoJSON city model -> metric footprints.

    Deliberately tolerant: a ring with float coordinates, a missing height, or a
    degenerate extent produces a skipped envelope rather than an exception,
    because one bad feature should not cost the caller the other 2,638.
    """
    out: list[Footprint] = []
    for f in features:
        ring = ((f.get("geometry") or {}).get("coordinates") or [[]])[0]
        pts = [p for p in (ring or [])
               if isinstance(p, (list, tuple)) and len(p) >= 2
               and all(isinstance(v, (int, float)) for v in p[:2])]
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        n = len(pts)
        lon = sum(xs[:-1]) / (n - 1)
        lat = sum(ys[:-1]) / (n - 1)
        width = (max(xs) - min(xs)) * _metres_per_degree_lon(lat)
        depth = (max(ys) - min(ys)) * M_PER_DEG_LAT
        try:
            height = float((f.get("properties") or {}).get("height_m") or 0.0)
        except (TypeError, ValueError):
            height = 0.0
        if width <= 0 or depth <= 0:
            continue
        out.append(Footprint(lon, lat, width, depth, height))
    return out


def height_variation_cv(footprints: list[Footprint], index: int,
                        radius_m: float = 150.0) -> float:
    """Coefficient of variation of building heights around ``index``.

    Directly scores the AVA rule that varying heights ventilate better than
    uniform heights. A uniform skyline gives 0.0, which the guidance calls the
    worse case; a strongly varied one gives a higher figure.

    Reported as a plain number with no target band, because the document states
    the preference but no threshold.
    """
    if not footprints:
        return 0.0
    here = footprints[index]
    kx = _metres_per_degree_lon(here.lat)
    dlon = radius_m / kx
    dlat = radius_m / M_PER_DEG_LAT
    heights = [f.height_m for f in footprints
               if abs(f.lon - here.lon) <= dlon and abs(f.lat - here.lat) <= dlat]
    if len(heights) < 2:
        return 0.0
    mean = sum(heights) / len(heights)
    if mean <= 0:
        return 0.0
    var = sum((h - mean) ** 2 for h in heights) / len(heights)
    return math.sqrt(var) / mean


def site_coverage(footprints: list[Footprint]) -> float:
    """Fraction of the district footprint that is covered by a building.

    The AVA guidance ranks this **above** building height for air quality, which
    is worth surfacing because the most-used massing operation in this project is
    a height change.

    Computed over the bounding box of the massing rather than a cadastral
    boundary: we do not have site boundaries, and a bounding box is a
    conservative (higher) estimate of coverage. It is therefore a *relative*
    number for comparing schemes, and the direction of any bias is stated rather
    than left for the reader to work out.
    """
    if not footprints:
        return 0.0
    lon0 = min(f.lon - f.width_m / _metres_per_degree_lon(f.lat) / 2 for f in footprints)
    lon1 = max(f.lon + f.width_m / _metres_per_degree_lon(f.lat) / 2 for f in footprints)
    lat0 = min(f.lat - f.depth_m / M_PER_DEG_LAT / 2 for f in footprints)
    lat1 = max(f.lat + f.depth_m / M_PER_DEG_LAT / 2 for f in footprints)
    mid_lat = (lat0 + lat1) / 2
    area = ((lon1 - lon0) * _metres_per_degree_lon(mid_lat)
            * (lat1 - lat0) * M_PER_DEG_LAT)
    if area <= 0:
        return 0.0
    return min(1.0, sum(f.area_m2 for f in footprints) / area)


def ground_level_void_fraction(footprints: list[Footprint]) -> float:
    """Open fraction at pedestrian level, from the low-rise band only.

    The AVA rule is specific: gaps *nearer the pedestrian level* matter far more
    than the same gaps higher up. So this measures the void fraction of the
    buildings that actually form the street wall -- those up to
    ``GROUND_LEVEL_BAND_M`` -- and not the towers above them.

    A tall tower with a small footprint can therefore score well while a
    continuous low podium scores badly, which is exactly the distinction the
    guidance draws between a tower and a podium.
    """
    if not footprints:
        return 0.0
    lat0 = min(f.lat - f.depth_m / M_PER_DEG_LAT / 2 for f in footprints)
    lat1 = max(f.lat + f.depth_m / M_PER_DEG_LAT / 2 for f in footprints)
    mid_lat = (lat0 + lat1) / 2
    lon0 = min(f.lon - f.width_m / _metres_per_degree_lon(f.lat) / 2 for f in footprints)
    lon1 = max(f.lon + f.width_m / _metres_per_degree_lon(f.lat) / 2 for f in footprints)
    area = ((lon1 - lon0) * _metres_per_degree_lon(mid_lat)
            * (lat1 - lat0) * M_PER_DEG_LAT)
    if area <= 0:
        return 0.0
    street_wall = sum(f.area_m2 for f in footprints
                      if f.height_m <= GROUND_LEVEL_BAND_M)
    return max(0.0, min(1.0, 1.0 - street_wall / area))


def projected_width_m(west_east_m: float, north_south_m: float,
                      bearing_deg: float,
                      footprint_bearing_deg: float = 0.0) -> float:
    """Width of a rectangle seen face-on to wind arriving from ``bearing_deg``.

    For a rectangle of sides ``a`` (east-west) and ``b`` (north-south) rotated by
    ``footprint_bearing_deg``, the projected width normal to the wind is
    ``a·|sin(φ − θ)| + b·|cos(φ − θ)|``.

    **This cannot break the NE≡SW symmetry, for any orientation.** The absolute
    value makes each footprint's contribution 180°-periodic, so wind from 45° and
    wind from 225° always sum to the same total over any set of footprints. That is
    exact geometry rather than an approximation error -- a convex body presents the
    same face to wind from either side -- and it means the oriented closed form is
    not a route to a directionally-sensitive screen. See
    :func:`directional_exposure`, which marches rays over the height raster and is
    sensitive to the *arrangement* rather than to any one footprint.
    """
    rel = math.radians(bearing_deg - footprint_bearing_deg)
    return (west_east_m * abs(math.sin(rel))
            + north_south_m * abs(math.cos(rel)))


def frontal_area_density(
    footprints: list[Footprint],
    site_area_m2: float,
    directions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Frontal area density per wind direction. The AVA 16-direction method."""
    dirs = directions or CARDINALS
    if site_area_m2 <= 0:
        return {"error": "site area must be positive"}
    per_dir: dict[str, float] = {}
    for name, bearing in dirs.items():
        frontal = sum(projected_width_m(f.width_m, f.depth_m, bearing,
                                        f.bearing_deg) * f.height_m
                      for f in footprints)
        per_dir[name] = frontal / site_area_m2
    values = sorted(per_dir.values())
    return {
        "byDirection": {k: round(v, 4) for k, v in per_dir.items()},
        "mean": round(sum(values) / len(values), 4) if values else 0.0,
        "min": round(values[0], 4) if values else 0.0,
        "max": round(values[-1], 4) if values else 0.0,
        "leastExposedDirection": min(per_dir, key=per_dir.get),
        "mostExposedDirection": max(per_dir, key=per_dir.get),
        "directionsComputed": len(per_dir),
        "rotatedFootprints": sum(1 for f in footprints
                                 if abs(f.bearing_deg) > 1e-9),
        "neSwDifference": round(abs(per_dir.get("NE", 0.0)
                                    - per_dir.get("SW", 0.0)), 6),
    }


def solar_position(lat_deg: float, lon_deg: float, day_of_year: int,
                   hour_local: float) -> tuple[float, float]:
    """Solar altitude and azimuth (degrees) for Hong Kong.

    Standard NOAA solar-position equations. Hong Kong is at 22.3 degN with no
    daylight saving, and the project has no solar geometry anywhere else, so this
    is the one place that geometry is computed rather than assumed.

    Returned as ``(altitude, azimuth)`` where azimuth is measured clockwise from
    north -- the meteorological convention, matching :data:`CARDINALS`, which is
    a different convention from the solar one and the usual source of an
    east/west mix-up.
    """
    decl = 23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0))
    # Solar time: Hong Kong is UTC+8, longitude 114 degE, so solar noon is about
    # 12:00 minus 24 minutes.
    solar_time = hour_local + 4.0 * (lon_deg - 120.0) / 60.0
    hour_angle = 15.0 * (solar_time - 12.0)
    lat = math.radians(lat_deg)
    dec = math.radians(decl)
    ha = math.radians(hour_angle)
    sin_alt = (math.sin(lat) * math.sin(dec)
               + math.cos(lat) * math.cos(dec) * math.cos(ha))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)
    cos_az = ((math.sin(dec) - math.sin(alt) * math.sin(lat))
              / (math.cos(alt) * math.cos(lat) or 1e-9))
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.degrees(math.acos(cos_az))
    if hour_angle > 0:
        az = 360.0 - az
    return math.degrees(alt), az


def _shadow_reach(heights: np.ndarray, tan_alt: float, cell: float
                  ) -> tuple[np.ndarray, int, bool]:
    """Per-building shadow reach in cells, the step cap, and whether the cap bit.

    The reach clamp is the fix for the saturation bug documented in
    :func:`shade_mask`: each building is swept only as far as its OWN shadow
    reaches. With no buildings the cap stays at :data:`SHADOW_MAX_STEPS`, which
    is exactly the value ``shade_fraction`` reported before the sweep was
    extracted, so its output is unchanged.
    """
    reach = np.rint(heights / tan_alt / cell).astype(np.int64)
    if reach.size == 0:
        return reach, SHADOW_MAX_STEPS, False
    max_steps = min(SHADOW_MAX_STEPS, int(reach.max()))
    reach = np.minimum(reach, max_steps)
    capped = bool(int(heights.max() / tan_alt / cell) + 1 > max_steps)
    return reach, max_steps, capped


def shade_mask(footprints: list[Footprint], grids: Any, lat_deg: float,
               lon_deg: float, day_of_year: int,
               hour_local: float) -> np.ndarray:
    """The shaded-ground mask at one instant. One sweep, one definition.

    Extracted verbatim from the body of :func:`shade_fraction`, which now reads
    this: the blend needs the raster, the report needs the scalar, and both must
    come from the same sweep. Returns a ``bool [rows, cols]`` array -- True where
    the ground is in shadow.

    Below the horizon there is no shade, so an all-False mask is returned rather
    than dividing by a negative tangent.
    """
    alt, az = solar_position(lat_deg, lon_deg, day_of_year, hour_local)
    rows_n, cols_n = grids.rows, grids.cols
    shaded = np.zeros((rows_n, cols_n), dtype=bool)
    if alt <= 0.5:
        return shaded

    cell = float(grids.cell_area_m2) ** 0.5
    tan_alt = math.tan(math.radians(alt))
    # Azimuth is clockwise from north. The grid's row index increases southward,
    # so a shadow thrown northward decrements it.
    drow = -math.cos(math.radians(az)) / tan_alt / cell
    dcol = math.sin(math.radians(az)) / tan_alt / cell

    # Each building's shadow is its own footprint swept along the sun vector, not
    # a one-cell line. The first version walked a single cell per step and reported
    # 3.0% shade on a winter afternoon in a district whose mean building is 35 m --
    # an artefact of the walk, not a finding. Sweeping the footprint is vectorised
    # over the whole district at once: one fancy-index assignment per shadow step.
    base_r, base_c, spans = [], [], []
    for f in footprints:
        if f.height_m <= 0:
            continue
        r0, c0 = grids.cell_of(f.lon, f.lat)
        half_r = max(1, int(round(f.depth_m / 2 / cell)))
        half_c = max(1, int(round(f.width_m / 2 / cell)))
        base_r.append(r0)
        base_c.append(c0)
        spans.append((half_r, half_c))

    if not base_r:
        return shaded

    br = np.asarray(base_r, dtype=np.float64)
    bc = np.asarray(base_c, dtype=np.float64)
    heights = np.asarray([f.height_m for f in footprints
                          if f.height_m > 0], dtype=np.float64)
    # Offsets of each building's own cells, relative to its centroid cell.
    # Group by footprint size before rasterising. The naive version issued one
    # fancy-index assignment *per building* per step -- 2,639 NumPy calls per
    # shadow step, which is why a winter morning took 22 seconds. Our footprints
    # come from a grid pitch, so they cluster into a handful of distinct spans
    # and grouping collapses the work to a few calls per step.
    groups: dict[tuple[int, int], list[int]] = {}
    for idx, span in enumerate(spans):
        groups.setdefault(span, []).append(idx)

    offsets = {}
    for span in groups:
        hr, hc = span
        offsets[span] = (
            np.arange(-hr, hr + 1, dtype=np.int64)[:, None]
            * np.ones((1, 2 * hc + 1), dtype=np.int64)
            + np.ones((2 * hr + 1, 1), dtype=np.int64)
            * np.arange(-hc, hc + 1, dtype=np.int64)[None, :]
        )

    # Each building is swept only as far as ITS OWN shadow reaches. An earlier
    # version ran every building out to the *tallest* building's shadow length,
    # so a 5 m shed had its footprint painted along the sun vector right
    # across the district. The shaded set saturated, and the tell was that
    # scaling the western 1,232 envelopes gave byte-identical shade to scaling
    # all 2,639 -- a physically impossible result that every ordering check
    # still passed, because a saturated sweep is monotone in the right
    # directions while being wrong about the magnitude.
    reach, max_steps, _ = _shadow_reach(heights, tan_alt, cell)

    for i in range(max_steps + 1):
        for span, members in groups.items():
            m = np.asarray(members, dtype=np.int64)
            off = offsets[span]
            rr = np.rint(br[m][:, None] + drow * i + off[0]).astype(np.int64)
            cc = np.rint(bc[m][:, None] + dcol * i + off[1]).astype(np.int64)
            ok = ((rr >= 0) & (rr < rows_n) & (cc >= 0) & (cc < cols_n)
                  & (i <= reach[m][:, None]))
            if ok.any():
                shaded[rr[ok], cc[ok]] = True
    return shaded


def shade_fraction(footprints: list[Footprint], grids: Any, lat_deg: float,
                   lon_deg: float, day_of_year: int,
                   hour_local: float) -> dict[str, Any]:
    """Fraction of the study area shaded at one instant, by real shadow casting.

    Each building casts a shadow of length ``H / tan(solar altitude)`` along the
    solar azimuth, walked across the ground grid cell by cell. This is geometry,
    not a land-cover lookup -- which is the point, because the existing heat index
    cannot tell you that raising a tower two blocks away shades this street.

    **One instant, not a daily integral.** A daily mean radiant temperature needs
    the whole solar path and a surface energy balance; this is a snapshot at one
    hour of one day, and it is labelled that way wherever it surfaces. The two
    hours worth reporting are the ones that matter in Hong Kong: 14:00, when
    solar altitude peaks, and 09:00, when east-facing facades are still in sun.

    Below the horizon there is no shade, and that is returned with its reason
    rather than by dividing by a negative tangent.

    The raster itself comes from :func:`shade_mask`; this function only adds the
    scalar and the sweep metadata, so the two can never disagree about the shade.
    """
    alt, az = solar_position(lat_deg, lon_deg, day_of_year, hour_local)
    cell = float(grids.cell_area_m2) ** 0.5
    out: dict[str, Any] = {
        "solarAltitudeDeg": round(alt, 2),
        "solarAzimuthDeg": round(az, 2),
        "dayOfYear": day_of_year,
        "hourLocal": hour_local,
        "metric": "shaded fraction of the study area at one instant",
    }
    if alt <= 0.5:
        out.update({"shadeFraction": 0.0,
                    "note": "the sun is at or below the horizon; no shade"})
        return out

    shaded = shade_mask(footprints, grids, lat_deg, lon_deg, day_of_year, hour_local)
    tan_alt = math.tan(math.radians(alt))
    heights = np.asarray([f.height_m for f in footprints
                          if f.height_m > 0], dtype=np.float64)
    _, max_steps, capped = _shadow_reach(heights, tan_alt, cell)

    total = float(grids.rows * grids.cols)
    out.update({
        "shadeFraction": round(float(shaded.sum()) / total, 4),
        "buildingsConsidered": int(heights.size),
        "shadowSteps": max_steps + 1,
        "shadowCapped": bool(capped),
        "shadowCellsPerMetreHeight": round(1.0 / tan_alt / cell, 4),
        "note": ("A single instant, not a daily mean radiant temperature. Each "
                 "building's own footprint is swept along the sun vector, so this "
                 "is the shaded ground a 5 m raster can resolve; sub-cell shadow "
                 "edges are not represented."),
    })
    return out


def design_report(
    footprints: list[Footprint],
    grids: Any,
    lat_deg: float = 22.44,
    lon_deg: float = 114.01,
    sun_hours: tuple[int, ...] = (9, 14),
    day_of_year: int = 355,
) -> dict[str, Any]:
    """Every geometry-derived design metric, each with its published rule.

    This is Stage 1 of ``docs/HEAT_AND_WIND_PROGRESS.md``: the measurements that
    make a design choice legible. It reports **no verdict**. The AVA policy is
    explicit that whether a scheme meets a standard cannot be known, so the
    comparison against other schemes -- Stage 3 -- is the only honest reading of
    these numbers, and a single "score" would be the one thing nobody can defend.
    """
    site_area = float(grids.rows * grids.cols * grids.cell_area_m2)
    fad = frontal_area_density(footprints, site_area)

    exposure = (directional_exposure(rasterise_height(footprints, grids))
                if grids is not None else {})

    shades = []
    for hour in sun_hours:
        r = shade_fraction(footprints, grids, lat_deg, lon_deg, day_of_year, hour)
        shades.append({
            "hourLocal": hour,
            "solarAltitudeDeg": r["solarAltitudeDeg"],
            "solarAzimuthDeg": r["solarAzimuthDeg"],
            "shadeFraction": r["shadeFraction"],
        })
    mean_shade = (sum(s["shadeFraction"] for s in shades) / len(shades)
                  if shades else 0.0)

    heights = [f.height_m for f in footprints]
    mean_h = sum(heights) / len(heights) if heights else 0.0
    global_cv = (math.sqrt(sum((h - mean_h) ** 2 for h in heights) / len(heights))
                 / mean_h) if len(heights) > 1 and mean_h > 0 else 0.0

    # Reference bands, for reading the ventilation figure against something.
    bands = {
        "openSpaceOrWaterfront": VR_OPEN_SPACE,
        "congestedStreet": list(VR_CONGESTED_STREET),
        "comfortablePedestrianShadeDerived": round(VR_COMFORTABLE_PEDESTRIAN, 3),
        "basis": (
            "Empirical expectations from wind tunnel and CFD studies of Hong Kong "
            "(AVA policy). The comfortable figure is derived in this module from "
            "1.25 m/s comfortable breeze / 7 m/s boundary-layer wind. None is an "
            "acceptance criterion; the AVA policy states that whether a scheme "
            "meets a standard cannot be known."
        ),
    }

    return {
        "footprintCount": len(footprints),
        "siteAreaM2": round(site_area, 1),
        "siteAreaHa": round(site_area / 10_000.0, 1),
        "height": {
            "meanM": round(mean_h, 2),
            "variationCv": round(global_cv, 4),
            "rule": RULE_SOURCES["heightVariationCv"],
        },
        "siteCoverage": {
            "value": round(site_coverage(footprints), 4),
            "rule": RULE_SOURCES["siteCoverage"],
            "caveat": ("Measured over the massing's own bounding box, because we "
                       "have no cadastral site boundaries. A bounding box "
                       "understates the true site area, so this reads HIGH."),
        },
        "groundLevelVoidFraction": {
            "value": round(ground_level_void_fraction(footprints), 4),
            "rule": RULE_SOURCES["groundLevelVoidFraction"],
            "definition": (f"Open fraction of the band up to {GROUND_LEVEL_BAND_M:.0f} m, "
                           "i.e. the buildings that actually form the street wall."),
        },
        "frontalAreaDensity": {
            **fad,
            "rule": RULE_SOURCES["frontalAreaDensity"],
            "limitation": (
                "Frontal area density cannot distinguish a north-easterly from a "
                "south-westerly, and no amount of footprint orientation will fix "
                "it: a·|sin(φ−θ)| + b·|cos(φ−θ)| is even in (φ−θ), so wind from 45 "
                "and wind from 225 always sum to the same total. That is exact "
                "geometry -- a convex body presents the same face to either side. "
                "See directionalExposure for a measure that can, because it is "
                "sensitive to the arrangement rather than to any one footprint."
            ),
        },
        "directionalExposure": {
            **(exposure if grids is not None else {}),
            "rule": ("The published prevailing wind here is NE/ENE/E, together "
                     "40.8% of the year (AVRG73). A screen that cannot tell a "
                     "north-easterly from a south-westerly is blind along that "
                     "axis, so first-hit height is measured per direction."),
        },
        "shade": {
            "instants": shades,
            "meanShadeFraction": round(mean_shade, 4),
            "rule": RULE_SOURCES["shadeFraction"],
            "caveat": ("A single instant at each hour, not a daily mean radiant "
                       "temperature. No surface energy balance, no air "
                       "temperature, no humidity."),
        },
        "referenceBands": bands,
        "sources": RULE_SOURCES,
        "verdict": None,
        "whyNoVerdict": (
            "The AVA policy document behind Hong Kong's own guidance states that "
            "while a higher velocity ratio is the better design, 'whether the "
            "better design option meets a standard cannot be known'. Printing a "
            "pass or fail here would invent a standard that does not exist."
        ),
    }


# Travel direction (downwind) per principal wind direction, as (d_row, d_col).
#
# Grid convention, which the whole table depends on: **row index increases
# southward, column index increases eastward.** A wind *from* the east therefore
# travels toward decreasing columns.
#
# The first version of this table had E and W swapped and all four diagonals
# mirrored, and the symptom was quiet rather than loud: a district with a tall
# western edge reported a high first-hit height for the *easterly*, which is
# exactly backwards. A convention error in a wind screen does not crash -- it just
# quietly points the prevailing wind at the wrong side of the district, so it is
# pinned by a test with a tall-north-and-west layout.
_TRAVEL: dict[str, tuple[int, int]] = {
    "N": (1, 0),    # from the north, travels south
    "NE": (1, -1),  # from the north-east, travels south-west
    "E": (0, -1),   # from the east, travels west
    "SE": (-1, -1), # from the south-east, travels north-west
    "S": (-1, 0),   # from the south, travels north
    "SW": (-1, 1),  # from the south-west, travels north-east
    "W": (0, 1),    # from the west, travels east
    "NW": (1, 1),   # from the north-west, travels south-east
}


def rasterise_height(footprints: list[Footprint], grids: Any) -> np.ndarray:
    """Massing as a height raster on the analysis grid. Max, not mean.

    Overlapping envelopes take the taller value, because a ray-march asks how tall
    the massing is here, not how many blocks average out.

    This is the shared input for the directional exposure screen here and for the
    sky-view factor Stage 4 of the roadmap needs, so it is built once.
    """
    h = np.zeros((grids.rows, grids.cols), dtype=np.float32)
    side = float(grids.cell_area_m2) ** 0.5
    for f in footprints:
        if f.height_m <= 0:
            continue
        r0, c0 = grids.cell_of(f.lon, f.lat)
        hr = max(0, int(round(f.depth_m / 2 / side)))
        hc = max(0, int(round(f.width_m / 2 / side)))
        ra, rb = max(0, r0 - hr), min(grids.rows, r0 + hr + 1)
        ca, cb = max(0, c0 - hc), min(grids.cols, c0 + hc + 1)
        if ra < rb and ca < cb:
            block = h[ra:rb, ca:cb]
            np.maximum(block, np.float32(f.height_m), out=block)
    return h


def directional_exposure(height: np.ndarray) -> dict[str, Any]:
    """Mean height of the **first** building the wind meets, per direction.

    This is the measure that breaks the NE≡SW symmetry the published prevailing
    wind (NE/ENE/E, 40.8% of the year at Hung Shui Kiu, AVRG73) runs straight
    into, and it does so for a reason worth stating: the obvious fix cannot.

    The obvious fix is the oriented closed form ``a·|sin(φ−θ)| + b·|cos(φ−θ)|``.
    It is **even in (φ−θ) for any orientation**, so a north-easterly and a
    south-westerly wind always sum to the same total. Two rows of blocks rotated
    +30° and −30° still score identically, and so does any arrangement. That is
    exact geometry, not a coding error — a convex body presents the same face to
    wind from either side.

    What does break it is that wind from the north reaches a *different building
    first* than wind from the south. That is a property of the arrangement, not of
    any footprint, so it needs a raster and a march. For each direction this walks
    the grid along the travel vector and records the height of the first non-empty
    cell on each ray.

    **Exposure, not flow.** No velocity, no turbulence, no separation, and not a
    velocity ratio. What it captures is obstruction ordering, which is the part a
    geometric screen can honestly carry.
    """
    rows, cols = height.shape
    out: dict[str, Any] = {}
    for name, (dr, dc) in _TRAVEL.items():
        # A ray enters along one edge and marches with the travel vector. When
        # dr != 0 the entry ROW is fixed (top or bottom) and each ray is told apart
        # by its entry COLUMN; when dr == 0 it is the other way round. Deriving
        # both index grids from one step vector keeps them broadcastable to the
        # full ray-by-step matrix.
        step = np.arange(max(rows, cols), dtype=np.int64)[:, None]
        if dr != 0:
            rr = (0 if dr > 0 else rows - 1) + dr * step
            cc = np.arange(cols, dtype=np.int64)[None, :] + dc * step
        else:
            # Pure E/W travel: each ROW is a ray, so rr runs down the rows while
            # cc must be a row vector too. Left as a column it broadcast against
            # rr into a single ray, and the screen reported "E: 12.80 m, 1 ray
            # considered" -- a number computed from one line of the district.
            rr = np.arange(rows, dtype=np.int64)[:, None]
            cc = ((0 if dc > 0 else cols - 1) + dc * step).T
        ok = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
        vals = np.where(ok, height[np.clip(rr, 0, rows - 1),
                                    np.clip(cc, 0, cols - 1)], 0.0)
        hit = (vals > 0) & ok
        # Reduce along the *step* axis to leave one value per ray. Which axis that
        # is depends on the branch: with dr != 0 the rays are columns, so steps run
        # down axis 0; with dr == 0 (east and west) the rays are rows, so steps run
        # along axis 1. Reducing over axis 0 unconditionally left E and W with one
        # result per column instead of one per row, which is why they reported 370
        # rays hit on a district where 518 rows contain a building.
        axis = 0 if dr != 0 else 1
        any_hit = hit.any(axis=axis)
        first = np.argmax(hit, axis=axis)
        if axis == 0:
            first_h = np.where(any_hit, vals[first, np.arange(vals.shape[1])], 0.0)
        else:
            first_h = np.where(any_hit, vals[np.arange(vals.shape[0]), first], 0.0)
        out[name] = {
            "meanFirstHitHeightM": (round(float(first_h[any_hit].mean()), 3)
                                    if any_hit.any() else 0.0),
            "raysWithBuilding": int(any_hit.sum()),
            "raysConsidered": int(any_hit.size),
        }
    ne = out["NE"]["meanFirstHitHeightM"]
    sw = out["SW"]["meanFirstHitHeightM"]
    out["symmetryBroken"] = abs(ne - sw) > 1e-6
    out["neMinusSwM"] = round(ne - sw, 4)
    out["method"] = (
        "First-hit height along a ray marched over the massing height raster, per "
        "principal direction. Breaks the NE/SW symmetry because wind from the north "
        "meets a different building first than wind from the south -- a property of "
        "the arrangement. The oriented closed form cannot do this: |sin| and |cos| "
        "make every footprint 180-degree periodic."
    )
    out["limitation"] = (
        "Obstruction ordering only. No velocity, no turbulence, no flow separation, "
        "and not a velocity ratio. A high value means the wind meets a tall building "
        "early, which is exposure, not necessarily stagnation."
    )
    return out


# --------------------------------------------------------------------------- #
# Sky-view factor (Stage 4)
# --------------------------------------------------------------------------- #


def _shifted(h: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """``out[r, c] = h[r + dr, c + dc]``, zero outside the grid. No wrap-around.

    A shift is how the horizon search samples the cell at a fixed offset from
    every observer at once. ``np.roll`` would wrap the far edge onto the near one
    and invent a building at the opposite horizon, so the out-of-bounds region is
    left as zero height instead.
    """
    rows, cols = h.shape
    out = np.zeros_like(h)
    r_lo, r_hi = max(0, -dr), rows - max(0, dr)
    c_lo, c_hi = max(0, -dc), cols - max(0, dc)
    if r_lo < r_hi and c_lo < c_hi:
        out[r_lo:r_hi, c_lo:c_hi] = h[r_lo + dr:r_hi + dr, c_lo + dc:c_hi + dc]
    return out


def sky_view_factor_raster(
    height: np.ndarray,
    cell_m: float,
    azimuth_bins: int = SVF_AZIMUTH_BINS,
    search_radius_m: float = SVF_SEARCH_RADIUS_M,
) -> np.ndarray:
    """Per-cell sky view factor, by a horizon search over the height raster.

    ``SVF = 1 - (1/N) sum_i sin(gamma_i)``, where ``gamma_i`` is the horizon
    elevation angle in azimuth bin ``i`` and the observer stands at ground. The
    horizon is the maximum, over all cells within the search radius in that bin,
    of ``atan(height / distance)``; since ``sin(atan(x))`` is monotone in ``x``,
    the search tracks the maximum of ``height / distance`` and converts once at
    the end.

    **Fully vectorised.** One shifted-array maximum per (bin, step); there is no
    Python loop over the 314,736 cells. That is what keeps a whole-grid search
    under a second -- a per-cell loop is minutes.

    A cell under massing is inside the building, not standing in the street, and
    scores 0.0. Deterministic: no RNG, no wall-clock.

    The offset for a bin is ``(-cos phi, +sin phi)`` in rows/columns because the
    grid row index increases southward and the column index eastward. SVF is
    azimuth-symmetric, so a sign error in that convention surfaces as the
    monotonicity acceptance check failing rather than as a crash.
    """
    h = np.asarray(height, dtype=np.float32)
    rows, cols = h.shape
    cell_m = float(cell_m)
    bins = int(azimuth_bins)
    radius_cells = max(1, int(round(float(search_radius_m) / cell_m)))
    building = h > 0.0

    sin_sum = np.zeros((rows, cols), dtype=np.float32)
    for b in range(bins):
        phi = math.radians(360.0 * b / bins)
        dr_unit = -math.cos(phi)
        dc_unit = math.sin(phi)
        horizon = np.zeros((rows, cols), dtype=np.float32)
        seen: set[tuple[int, int]] = set()
        for s in range(1, radius_cells + 1):
            dr = int(round(dr_unit * s))
            dc = int(round(dc_unit * s))
            if dr == 0 and dc == 0:
                continue
            if (dr, dc) in seen:
                # Near-axis bins revisit the same cell at several radii; skipping
                # the repeat is pure saving with no change to the maximum.
                continue
            seen.add((dr, dc))
            dist = cell_m * math.hypot(dr, dc)
            np.maximum(horizon, _shifted(h, dr, dc) / np.float32(dist),
                       out=horizon)
        # sin(gamma) = x / sqrt(1 + x^2), monotone in the horizon tangent x.
        sin_sum += horizon / np.sqrt(1.0 + horizon * horizon)

    svf = 1.0 - sin_sum / np.float32(bins)
    np.clip(svf, 0.0, 1.0, out=svf)
    svf[building] = 0.0
    return svf.astype(np.float32)


def _svf_summary(svf: np.ndarray, building_cells: int, azimuth_bins: int,
                 search_radius_m: float,
                 search_radius_cells: int | None) -> dict[str, Any]:
    """The JSON-safe summary of an SVF raster (no raster in the dict).

    ``meanOpenFieldSvf`` is the mean over cells that are NOT under massing. A cell
    under massing is exactly 0.0 (see :func:`sky_view_factor_raster`), so the open
    field is ``svf > 0``; when the whole grid is built over, that mean is
    undefined and reported as 0.0 with the building count making the reason clear.
    """
    open_field = svf > 0.0
    mean_open = float(svf[open_field].mean()) if open_field.any() else 0.0
    return {
        "meanSvf": round(float(svf.mean()), 4),
        "meanOpenFieldSvf": round(mean_open, 4),
        "minSvf": round(float(svf.min()), 4),
        "maxSvf": round(float(svf.max()), 4),
        "azimuthBins": int(azimuth_bins),
        "searchRadiusM": float(search_radius_m),
        "searchRadiusCells": (None if search_radius_cells is None
                              else int(search_radius_cells)),
        "buildingCells": int(building_cells),
        "method": _SVF_METHOD,
        "limitation": _SVF_LIMITATION,
        "status": HEAT_STATUS,
        "warning": HEAT_WARNING,
    }


def sky_view_factor(
    height: np.ndarray,
    cell_m: float,
    azimuth_bins: int = SVF_AZIMUTH_BINS,
    search_radius_m: float = SVF_SEARCH_RADIUS_M,
) -> dict[str, Any]:
    """Summary of :func:`sky_view_factor_raster` -- no raster in the dict.

    Returns ``meanSvf``, ``meanOpenFieldSvf``, ``minSvf``, ``maxSvf``,
    ``azimuthBins``, ``searchRadiusM``, ``searchRadiusCells``, ``buildingCells``,
    ``method``, ``limitation``, ``status`` and ``warning``, all JSON-safe.
    """
    h = np.asarray(height, dtype=np.float32)
    cell_m = float(cell_m)
    radius_cells = max(1, int(round(float(search_radius_m) / cell_m)))
    svf = sky_view_factor_raster(h, cell_m, azimuth_bins, search_radius_m)
    return _svf_summary(svf, int((h > 0.0).sum()), int(azimuth_bins),
                        float(search_radius_m), radius_cells)


# --------------------------------------------------------------------------- #
# The heat blend and its anchoring (Stage 4)
# --------------------------------------------------------------------------- #


def blended_heat_field(
    land_index: np.ndarray,
    svf: np.ndarray,
    shaded: np.ndarray,
    weights: dict[str, float] | None = None,
) -> np.ndarray:
    """The one relative heat field: land class + sky exposure + unshaded ground.

    ``w_land*land_index + w_svf*(1 - svf) + w_shadow*(1 - shaded)``, weights
    normalised to sum 1. Grid-agnostic (arrays only) so the same function serves a
    ``Grids`` caller and the ``Overlay`` caller in ``make_heatmap.py`` -- the
    overlay holds no ``cell_of``/``cell_area_m2``, which is why this takes arrays
    rather than a grid.

    The ``HEAT_INDEX`` table is NOT duplicated here: the caller maps the class grid
    through the one definition in ``_overlay_common`` and passes the resulting
    0-1 array in. That keeps one definition of the land index, as the project
    requires.

    A cell that is both greener and more shaded moves down; a cell under new
    massing loses sky (``svf`` falls) and gains shade, so it also moves down. That
    is the whole point of the blend: both levers the planner has move the field.
    """
    w = dict(HEAT_BLEND_WEIGHTS if weights is None else weights)
    total = float(sum(w.values()))
    if total <= 0.0:
        raise ValueError("heat blend weights must sum to a positive value")
    land = np.asarray(land_index, dtype=np.float32)
    svf_a = np.asarray(svf, dtype=np.float32)
    shaded_a = np.asarray(shaded, dtype=np.float32)
    field = (
        np.float32(w.get("land", 0.0) / total) * land
        + np.float32(w.get("svf", 0.0) / total) * (1.0 - svf_a)
        + np.float32(w.get("shadow", 0.0) / total) * (1.0 - shaded_a)
    )
    return np.clip(field, 0.0, 1.0).astype(np.float32)


def load_heat_calibration() -> dict[str, Any] | None:
    """Read ``data/reference/heat_anchor.json``, or None when it is absent.

    The calibration is what maps the 0-1 relative scale to a stated, cited
    reference range. Its schema names the physical quantity explicitly --
    ``quantity`` is ``"LST_skin"`` or ``"HKO_air"``, never both -- because LST is
    a *skin* temperature and the HKO normals are *air* temperature and conflating
    them is the error the PRD warns about.

    Returns None rather than a hard-coded range when the file is missing or
    unreadable, so the response can say honestly that nothing is anchored. Mirrors
    :func:`app.services.ventilation.load_reference`.
    """
    path: Path = settings.data_dir / HEAT_CALIBRATION_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _anchoring_block(calibration: dict[str, Any] | None) -> dict[str, Any]:
    """The anchoring sub-dict, from a loaded calibration (or its absence).

    ``heatIndexSource`` describes the ``HEAT_INDEX`` land-class table, not the
    anchoring, so it stays "Hand-authored screening index; awaiting verification"
    on every path -- the table is hand-authored whether or not a reference range
    has been ingested.
    """
    heat_index_source = "Hand-authored screening index; awaiting verification"
    if calibration is None:
        return {
            "anchorSource": "none",
            "anchorRangeC": None,
            "calibrationPath": None,
            "quantity": None,
            "heatIndexSource": heat_index_source,
            "status": HEAT_STATUS,
            "warning": HEAT_ANCHOR_WARNING,
        }
    quantity = calibration.get("quantity")
    source = {"LST_skin": "landsat_lst", "HKO_air": "hko_normals"}.get(
        quantity, "unknown")
    rng = calibration.get("rangeC")
    return {
        "anchorSource": source,
        "anchorRangeC": (None if rng is None else [float(rng[0]), float(rng[1])]),
        "calibrationPath": HEAT_CALIBRATION_PATH,
        "quantity": quantity,
        "heatIndexSource": heat_index_source,
        "status": HEAT_ANCHOR_STATUS,
        "warning": (
            f"Anchored to a published reference range ({quantity}: {rng}). This is "
            "a stated band, not a measurement of this site; screening-level."
        ),
    }


def heat_field_report(
    land_index: np.ndarray,
    svf: np.ndarray,
    shaded: np.ndarray,
    calibration: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
    cell_m: float | None = None,
) -> dict[str, Any]:
    """Assemble the served heat dict from the three component arrays.

    Returns the mean/max blended index, the effective weights, the per-component
    means (each a plain mean of its raw input: land index, SVF, shaded fraction),
    the SVF summary, the anchoring block, the status and the warning. **Never
    converts to a temperature** while ``anchorSource == 'none'``.

    ``cell_m`` is optional and only used to state ``searchRadiusCells`` in the SVF
    summary; when it is unknown that one field is reported as ``None`` rather than
    guessed from a resolution this function was not told.
    """
    land = np.asarray(land_index, dtype=np.float32)
    svf_a = np.asarray(svf, dtype=np.float32)
    shaded_a = np.asarray(shaded, dtype=np.float32)
    w = dict(HEAT_BLEND_WEIGHTS if weights is None else weights)
    total = float(sum(w.values())) or 1.0
    norm = {k: v / total for k, v in w.items()}
    field = blended_heat_field(land, svf_a, shaded_a, w)
    srad_cells = (max(1, int(round(SVF_SEARCH_RADIUS_M / float(cell_m))))
                  if cell_m else None)
    svf_block = _svf_summary(svf_a, int((svf_a == 0.0).sum()), SVF_AZIMUTH_BINS,
                             SVF_SEARCH_RADIUS_M, srad_cells)
    return {
        "meanHeatIndex": round(float(field.mean()), 4),
        "maxHeatIndex": round(float(field.max()), 4),
        "weights": {k: round(v, 6) for k, v in norm.items()},
        "components": {
            "land": round(float(land.mean()), 4),
            "svf": round(float(svf_a.mean()), 4),
            "shadow": round(float(shaded_a.mean()), 4),
        },
        "svf": svf_block,
        "anchoring": _anchoring_block(calibration),
        "status": HEAT_STATUS,
        "warning": HEAT_WARNING,
        "note": _HEAT_NOTE,
    }


# --------------------------------------------------------------------------- #
# Per-cell frontal area and FAD (Stage 5b)
# --------------------------------------------------------------------------- #


def frontal_area_raster(footprints: list[Footprint], grids: Any,
                        bearing_deg: float) -> np.ndarray:
    """Per-cell frontal area in m^2, for wind arriving from ``bearing_deg``.

    Each footprint's oriented frontal area
    ``projected_width_m(width, depth, bearing, footprint_bearing) * height`` is
    **spread evenly** over the cells the footprint covers, and overlapping
    footprints are combined by ``max`` -- matching :func:`rasterise_height`.

    Spreading, not stamping, is what makes the per-cell surface the same quantity
    as the site-level FAD at a finer resolution: ``sum_cell frontal * A_cell``
    equals ``sum_f projected_width_f * height_f`` exactly on a non-overlapping
    layout (the generator's case), and is a stated lower bound where footprints
    overlap. Stamping the whole area at the centroid would break that identity.

    **Do not re-derive the projected width here.** This calls the one canonical
    :func:`projected_width_m`; a second copy of that formula is the exact drift the
    project forbids.
    """
    out = np.zeros((grids.rows, grids.cols), dtype=np.float32)
    side = float(grids.cell_area_m2) ** 0.5
    for f in footprints:
        if f.height_m <= 0:
            continue
        r0, c0 = grids.cell_of(f.lon, f.lat)
        hr = max(0, int(round(f.depth_m / 2 / side)))
        hc = max(0, int(round(f.width_m / 2 / side)))
        ra, rb = max(0, r0 - hr), min(grids.rows, r0 + hr + 1)
        ca, cb = max(0, c0 - hc), min(grids.cols, c0 + hc + 1)
        if ra >= rb or ca >= cb:
            continue
        n_cells = (rb - ra) * (cb - ca)
        frontal = (projected_width_m(f.width_m, f.depth_m, bearing_deg,
                                     f.bearing_deg) * f.height_m)
        per_cell = np.float32(frontal / n_cells)
        block = out[ra:rb, ca:cb]
        np.maximum(block, per_cell, out=block)
    return out


def cell_fad(footprints: list[Footprint], grids: Any,
             bearing_deg: float) -> np.ndarray:
    """Per-cell frontal area density: ``frontal_area_raster / cell_area_m2``.

    The denominator is the cell's OWN area, not the site area. FAD is frontal area
    per unit *ground* area, so the per-cell analogue must divide the same cell's
    frontal area by the same cell's ground area for the two resolutions to be the
    same quantity. Dividing by the site area instead would scale the cost surface
    wrongly while leaving it plausible -- which is exactly why the mean-
    preservation identity is the test that catches it.
    """
    raster = frontal_area_raster(footprints, grids, bearing_deg)
    return (raster / np.float32(grids.cell_area_m2)).astype(np.float32)
