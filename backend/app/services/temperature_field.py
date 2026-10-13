"""Observed-LST-anchored surface-temperature field, in degrees Celsius.

## What this replaces

The heat overlay used to be a hand-authored 0-1 *relative index* (land class +
sky view + shade). It was honest but legible only as "this cell is hotter than
that one". This module builds the same *spatial* signal but anchors it to a real
quantity -- observed Landsat Land Surface Temperature -- so the number a planner
reads is degrees Celsius, and the field responds to interventions through the
same geometry terms as before (plus wind shelter).

## The two temperatures, kept apart

Landsat ST_B10 is a **skin** (surface) temperature: the pavement, the roof, the
grass. It is NOT air temperature, and conflating them is the one error the PRD
warns about. On a clear Hong Kong summer day the surface runs 40-52 C while the
air is ~32-34 C. So this module reports surface temperature, and offers an
**air-temperature estimate** (surface minus a documented offset) as a separate,
clearly-labelled quantity -- an estimate, never a measurement.

## Why deviation, not absolute

Hong Kong summer surfaces are uniformly hot. Rendering absolute C saturates the
map red everywhere, which is physically true but useless for comparison -- the
whole point of the tool. A planner needs the *signal*: which cells are hotter
than the site mean, which cooler. So the canonical output is the **anomaly**
(deviation from the site mean) in C, with the absolute field carried beside it
for the stats. A well-designed scheme reads mostly near-neutral; only genuine
hot spots (unshaded paving, wind-blocked, un-vegetated) spike.

## Ventilation as wind shelter

``cell_fad`` (per-cell frontal area density) is folded in as a wind-shelter term:
a cell whose wind is blocked by upwind massing cannot shed heat by convection and
runs hotter. This is geometry (FAD is not a velocity ratio) and is labelled as
such -- it is a screening signal, not a wind-speed or a convective solve.

## Anchoring and the scenario mean

The slider sets a **scenario mean surface temperature**; the whole field shifts
so its mean matches. The observed scene supplies the default (its own mean), and
the presets (mean summer / hot day / extreme heatwave) are documented offsets
above the observed base -- the same shape as the rainfall slider's Amber/Red/
Black, one real input driving one honest output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

# The offset from surface (skin) temperature to a screen-level air-temperature
# estimate. A documented screening value, not a site measurement: on clear summer
# days urban surfaces run ~10-15 C above the air temperature. Taken as a fixed
# midpoint; stated plainly everywhere it is used.
SURFACE_TO_AIR_OFFSET_C = 12.0

# Scenario presets: offsets above the observed scene mean, in C. These are the
# temperature analogue of the rainfall slider's Amber/Red/Black rainstorm-signal
# buttons -- named, documented steps, not a physics model.
SCENARIO_PRESETS: dict[str, dict[str, Any]] = {
    "mean_summer": {"label": "Mean summer", "offsetC": 0.0,
                    "note": "The observed Landsat scene, unchanged."},
    "hot_day": {"label": "Hot day", "offsetC": 3.0,
                "note": "+3 C above the observed mean: a hotter-than-average day."},
    "extreme_heatwave": {"label": "Extreme heatwave", "offsetC": 6.0,
                         "note": "+6 C above the observed mean: an extreme event."},
}

# The canonical anchor: the single fully-clear, 100%-finite Landsat scene over
# the site. Chosen because it is the only scene with zero NaN cells and ~1% cloud
# (see data/reference/lst_scenes.json); the others are partial or fully clouded
# and are carried as corroboration only.
CANONICAL_SCENE_ID = "LC08_L2SP_122044_20220903_02_T1"

LST_STATUS = "SCREENING_SURFACE_TEMPERATURE_ANCHORED_TO_OBSERVED_LST"
LST_WARNING = (
    "Surface (skin) temperature anchored to an observed Landsat LST scene, "
    "rendered as deviation from the site mean. Not air temperature, not a "
    "surface-energy-balance simulation; wind shelter is geometry (FAD), not a "
    "wind speed."
)


def _load_lst_scenes() -> tuple[dict[str, Any], np.ndarray | None]:
    """The observed LST scene index and the canonical scene's C field.

    Returns ``(index, canonical_field)``. ``canonical_field`` is None when the
    data is absent or the canonical scene has no finite cells -- the caller then
    falls back to a synthetic base rather than fabricating temperatures.
    """
    meta_path: Path = settings.data_dir / "reference" / "lst_scenes.json"
    npz_path: Path = settings.data_dir / "reference" / "lst_scenes.npz"
    index: dict[str, Any] = {}
    if meta_path.exists():
        try:
            index = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            index = {}
    field: np.ndarray | None = None
    if npz_path.exists():
        try:
            data = np.load(npz_path)
            if CANONICAL_SCENE_ID in data:
                raw = np.asarray(data[CANONICAL_SCENE_ID], dtype=np.float32)
                finite = np.isfinite(raw)
                if finite.any():
                    # Fill any residual NaN (should be none for the canonical
                    # scene) from the scene mean, so the field is dense.
                    field = np.where(finite, raw, float(np.nanmean(raw)))
        except (OSError, ValueError):
            field = None
    return index, field


def resample_to_grid(src: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Bilinear-resample a north-up field onto a target grid over the same bbox.

    Mirrors ``_overlay_common._bilinear_resample`` but standalone, so the LST
    loader does not import the overlay scripts. ``src`` is the 30 m scene
    (20x24); ``rows``/``cols`` the 5 m analysis grid (117x148).
    """
    src = np.asarray(src, dtype=np.float32)
    if src.ndim != 2 or src.shape[0] < 2 or src.shape[1] < 2:
        raise ValueError(f"LST source must be a 2D field, got {src.shape}")
    sr, sc = src.shape
    fy = np.clip((np.arange(rows) + 0.5) * sr / rows - 0.5, 0.0, sr - 1.0)
    fx = np.clip((np.arange(cols) + 0.5) * sc / cols - 0.5, 0.0, sc - 1.0)
    y0 = np.floor(fy).astype(int)
    x0 = np.floor(fx).astype(int)
    y1 = np.minimum(y0 + 1, sr - 1)
    x1 = np.minimum(x0 + 1, sc - 1)
    wy = (fy - y0)[:, None]
    wx = (fx - x0)[None, :]
    top = src[np.ix_(y0, x0)] * (1 - wx) + src[np.ix_(y0, x1)] * wx
    bottom = src[np.ix_(y1, x0)] * (1 - wx) + src[np.ix_(y1, x1)] * wx
    return (top * (1 - wy) + bottom * wy).astype(np.float32)


def temperature_field(
    *,
    land_index: np.ndarray,
    svf: np.ndarray,
    shaded: np.ndarray,
    fad: np.ndarray,
    lst_base: np.ndarray | None = None,
    scenario_mean_c: float | None = None,
) -> dict[str, Any]:
    """The anchored surface-temperature field, absolute and as an anomaly.

    ``land_index`` is the 0-1 land-class heat index (the existing HEAT_INDEX
    lookup); ``svf``/``shaded`` the sky-view and shade terms; ``fad`` the per-cell
    frontal-area density (wind shelter). ``lst_base`` is the resampled observed
    scene (None -> a synthetic mean-neutral base); ``scenario_mean_c`` shifts the
    whole field so its mean matches (None -> keep the observed mean).

    Returns ``surfaceC`` (absolute), ``anomalyC`` (deviation from mean), and the
    metadata block. The geometry terms express *deviation from the site mean*:
    ``land_index``/``(1-svf)``/``(1-shaded)``/``fad`` are each centred before
    blending, so a well-designed, uniform site reads near-zero everywhere.
    """
    shape = np.asarray(land_index, dtype=np.float32).shape
    svf_a = np.broadcast_to(np.asarray(svf, dtype=np.float32), shape)
    shaded_a = np.broadcast_to(np.asarray(shaded, dtype=np.float32), shape)
    fad_a = np.broadcast_to(np.asarray(fad, dtype=np.float32), shape)
    land_a = np.asarray(land_index, dtype=np.float32)

    # Each geometry term, centred on its own mean, so it contributes an
    # *anomaly* (positive = hotter than the site's typical cell). FAD enters
    # positively: more blockage -> more sheltered -> hotter.
    def _centre(a: np.ndarray) -> np.ndarray:
        return a - float(a.mean())

    delta = (
        0.40 * _centre(land_a)
        + 0.25 * _centre(1.0 - svf_a)
        + 0.20 * _centre(1.0 - shaded_a)
        + 0.15 * _centre(fad_a)
    ).astype(np.float32)

    # Base: observed scene resampled, else a neutral synthetic base at 0 C
    # (the anomaly is then purely the geometry). The caller always supplies
    # lst_base in the live path.
    if lst_base is not None:
        base = np.broadcast_to(
            np.asarray(lst_base, dtype=np.float32), shape).astype(np.float32)
    else:
        base = np.zeros(shape, dtype=np.float32)

    surface_c = (base + delta).astype(np.float32)

    # Scenario re-anchor: shift so the mean matches the requested scenario mean.
    observed_mean = float(base.mean())
    target_mean = observed_mean if scenario_mean_c is None else float(scenario_mean_c)
    shift = target_mean - float(surface_c.mean())
    surface_c = (surface_c + shift).astype(np.float32)

    anomaly_c = (surface_c - float(surface_c.mean())).astype(np.float32)
    air_estimate_c = (surface_c - SURFACE_TO_AIR_OFFSET_C).astype(np.float32)

    return {
        "surfaceC": surface_c,
        "anomalyC": anomaly_c,
        "airEstimateC": air_estimate_c,
        "meanSurfaceC": round(float(surface_c.mean()), 3),
        "maxSurfaceC": round(float(surface_c.max()), 3),
        "minSurfaceC": round(float(surface_c.min()), 3),
        "meanAnomalyC": round(float(anomaly_c.mean()), 3),
        "maxAnomalyC": round(float(anomaly_c.max()), 3),
        "observedMeanC": round(observed_mean, 3),
        "scenarioMeanC": round(target_mean, 3),
        "surfaceToAirOffsetC": SURFACE_TO_AIR_OFFSET_C,
        "status": LST_STATUS,
        "warning": LST_WARNING,
    }


def load_observed_lst(rows: int, cols: int) -> tuple[np.ndarray | None, dict[str, Any]]:
    """The resampled observed LST base field, or None when unavailable.

    Returns ``(base, meta)``; ``base`` is None when the data is absent so the
    caller can fall back honestly. ``meta`` carries the provenance for the
    sidecar/stats.
    """
    index, field = _load_lst_scenes()
    meta: dict[str, Any] = {
        "available": field is not None,
        "sceneId": CANONICAL_SCENE_ID,
        "source": "Landsat 8/9 C2 L2 ST_B10 (Planetary Computer)",
        "nativeResolutionM": 30,
        "observedMeanC": (round(float(np.nanmean(field)), 3)
                          if field is not None else None),
    }
    if field is None:
        return None, meta
    return resample_to_grid(field, rows, cols), meta
