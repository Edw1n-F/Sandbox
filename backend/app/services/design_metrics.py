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

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

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


@dataclass(frozen=True)
class Footprint:
    """One massing envelope, in metres, with its centroid."""

    lon: float
    lat: float
    width_m: float
    depth_m: float
    height_m: float

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
                      bearing_deg: float) -> float:
    """Width of a rectangle seen face-on to wind arriving from ``bearing_deg``.

    For an axis-aligned rectangle of sides ``a`` (east-west) and ``b``
    (north-south) the projected width is ``a·|sin φ| + b·|cos φ|``. Our footprints
    are axis-aligned by construction -- they come from a raster grid -- so this
    form is exact for them and the symmetry that implies is disclosed in the
    report rather than papered over.
    """
    phi = math.radians(bearing_deg)
    return (west_east_m * abs(math.sin(phi))
            + north_south_m * abs(math.cos(phi)))


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
        frontal = sum(projected_width_m(f.width_m, f.depth_m, bearing) * f.height_m
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

    tan_alt = math.tan(math.radians(alt))
    # Azimuth is clockwise from north. The grid's row index increases southward,
    # so a shadow thrown northward decrements it.
    drow = -math.cos(math.radians(az)) / tan_alt / cell
    dcol = math.sin(math.radians(az)) / tan_alt / cell

    rows_n, cols_n = grids.rows, grids.cols
    shaded = np.zeros((rows_n, cols_n), dtype=bool)
    max_steps = 400
    capped = 0

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

    if base_r:
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
        reach = np.rint(heights / tan_alt / cell).astype(np.int64)
        max_steps = min(max_steps, int(reach.max()) if reach.size else 0)
        reach = np.minimum(reach, max_steps)

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
        if reach.size and int(heights.max() / tan_alt / cell) + 1 > max_steps:
            capped = 1

    total = float(rows_n * cols_n)
    out.update({
        "shadeFraction": round(float(shaded.sum()) / total, 4),
        "buildingsConsidered": len(base_r),
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
                "Every footprint is an axis-aligned rectangle, so the projected "
                "width depends only on |sin| and |cos| of the bearing and the "
                "result is symmetric about the cardinal axes. NE, SE, SW and NW "
                "score identically. It compares schemes and compares axes; it "
                "cannot distinguish a north-easterly from a south-westerly."
            ),
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
