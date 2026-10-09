"""Air-ventilation screening by frontal area density, against published HSK data.

## Why this is not a wind tunnel

It is not, and it does not pretend to be. A wind tunnel or a CFD solve is the
published way to get a velocity ratio; this module does geometry.

What it computes is **frontal area density** (FAD) per wind direction: the total
building face presented perpendicular to the wind, over the site area. Low FAD
means the wind can penetrate; high FAD means the site is sheltered from that
direction. It needs nothing but the building footprints and heights, which the
project already has for all 2,639 envelopes.

**FAD is not a velocity ratio and the two are never interchanged.** VR is
Vp / V-inf and comes from a wind tunnel or CFD. The relationship between the two
depends on upstream wind profile, roughness and flow separation, none of which we
model. So this module reports FAD in its own units, and reports the published
HSK velocity ratios beside it purely as a *reference range* to read against.

## Where the wind directions come from

The one published fact this uses: the AVRG73 air ventilation assessment for
Hung Shui Kiu Area 13 states the dominant wind directions for the site are **NE,
ENE and E, together 40.8% of wind frequency**. No frequency is invented for any
other direction, and every direction is still computed and reported so the shape
of the site is visible even where the frequency is unknown.

## There is no threshold, and that is the finding

An earlier version of this note said the pass/fail bands "have not been
extracted". They were never there to extract. The policy document behind Hong
Kong's own guidance -- Ng & Cheng, *Policies and technical guidelines for urban
planning of high-density cities - air ventilation assessment (AVA) of Hong Kong*,
Building and Environment 44 (2009) 1478-1488 -- states:

    "A design having a higher velocity ratio would be considered as a better
    design than one having a lower velocity ratio. At this stage, only the better
    design option is known, but whether the better design option meets a standard
    cannot be known."

It does publish *empirical reference values*, which are not criteria: velocity
ratios of **0.3** near the waterfront and in open spaces, and **0.05-0.1** in
streets and congested urban spaces. Those live in
:mod:`app.services.design_metrics` as ``VR_OPEN_SPACE`` and
``VR_CONGESTED_STREET``.

**So this module still asserts no threshold, but now for the opposite reason:**
not because we could not find one, but because the guidance says none exists yet.
That is a stronger position than the old note claimed, and it is cited.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.config import settings

# The geometry primitives are defined once, in design_metrics, and re-exported
# here so this module's callers and tests are unaffected. Two copies of
# projected_width_m existed between these modules, and two implementations of one
# formula is how a screening starts disagreeing with itself -- which is the one
# failure this module exists to rule out.
from app.services.design_metrics import (  # noqa: F401
    CARDINALS,
    VR_COMFORTABLE_PEDESTRIAN,
    VR_CONGESTED_STREET,
    VR_OPEN_SPACE,
    Footprint,
    projected_width_m,
)

REFERENCE_PATH = "reference/hsk_ava_reference.json"

# Meteorological convention: the direction a wind comes FROM. Named explicitly
# because "wind from the east" and "wind towards the east" differ by 180 degrees,
# and getting that backwards points every building at the wrong side.

# M_PER_DEG_LAT used to be defined here and is now unused in this module: the one
# function that needed it moved to design_metrics. Left behind as a second source
# of a physical constant is exactly the drift this consolidation removed.


def load_reference() -> dict[str, Any] | None:
    path: Path = settings.data_dir / REFERENCE_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def frontal_area_density(
    footprints: list[dict[str, Any]],
    site_area_m2: float,
    directions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """FAD for each wind direction, plus a frequency-weighted prevailing figure.

    ``footprints`` items need ``widthM``, ``depthM`` and ``heightM``.
    """
    dirs = directions or CARDINALS
    if site_area_m2 <= 0:
        return {"error": "site area must be positive"}

    per_direction: dict[str, float] = {}
    for name, bearing in dirs.items():
        frontal = 0.0
        for fp in footprints:
            frontal += projected_width_m(
                float(fp["widthM"]), float(fp["depthM"]), bearing
            ) * float(fp["heightM"])
        per_direction[name] = frontal / site_area_m2

    values = sorted(per_direction.values())
    return {
        "byDirection": {k: round(v, 4) for k, v in per_direction.items()},
        "mean": round(sum(values) / len(values), 4) if values else 0.0,
        "max": round(values[-1], 4) if values else 0.0,
        "min": round(values[0], 4) if values else 0.0,
        "leastExposedDirection": min(per_direction, key=per_direction.get),
        "mostExposedDirection": max(per_direction, key=per_direction.get),
    }


def ventilation_screening(
    footprints: list[dict[str, Any]],
    site_area_m2: float,
    prevailing: list[str] | None = None,
    combined_frequency_percent: float | None = None,
) -> dict[str, Any]:
    """FAD screening, with the published prevailing directions weighted.

    ``prevailing`` defaults to the AVRG73 directions for Hung Shui Kiu. Only those
    directions carry a frequency; every other direction is computed but reported
    without one, because inventing a wind rose is how a screening turns into a
    fiction.
    """
    dirs = CARDINALS
    fad = frontal_area_density(footprints, site_area_m2, dirs)
    if "error" in fad:
        return fad

    wind = prevailing or ["NE", "ENE", "E"]
    known = [d for d in wind if d in fad["byDirection"]]
    prevailing_fad = (sum(fad["byDirection"][d] for d in known) / len(known)
                      if known else None)
    least = min(known, key=lambda d: fad["byDirection"][d]) if known else None

    return {
        "metric": "frontal area density",
        "units": "dimensionless (projected frontal area / site area)",
        "siteAreaM2": round(site_area_m2, 1),
        "buildingCount": len(footprints),
        "frontalAreaDensity": fad,
        "prevailingDirections": wind,
        "combinedFrequencyPercent": combined_frequency_percent,
        "prevailingMeanFad": (None if prevailing_fad is None
                               else round(prevailing_fad, 4)),
        "leastExposedPrevailingDirection": least,
        "limitation": (
            "Every footprint in this project is an axis-aligned rectangle, "
            "generated from the land-cover raster. For a box the projected width "
            "depends only on |sin| and |cos| of the bearing, so the result is "
            "symmetric about north-south and east-west: NE, SE, SW and NW all "
            "score identically. This screening therefore compares SCHEMES and site "
            "layouts, and compares axes (a north-south wind corridor against an "
            "east-west one), but it CANNOT distinguish a north-easterly from a "
            "south-westerly -- which matters, because the published prevailing "
            "wind here is from the north-east. Distinguishing them needs real "
            "building orientations, and our massing model has none."
        ),
        "note": (
            "Low frontal area density means the wind can penetrate the site; "
            "high means it is sheltered from that direction. This is geometry, "
            "not flow. It is NOT a velocity ratio and no pass/fail threshold is "
            "asserted -- the bands are in Technical Circular 1/06 and its Annex A "
            "Technical Guide, which we have not extracted. Read it as a shape "
            "indicator for comparing schemes, never as an assessment."
        ),
    }
