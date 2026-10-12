"""Live scenario runs: sliders + active plan -> regenerated overlays + stats.

Why this module exists
----------------------

ADR-024 kept the rainfall/temperature sliders local-only because "the physics
engine that would own per-run inputs does not exist". This is that engine's
serving layer. One ``POST /scenes/{id}/run`` call:

1. applies the active plan's interventions to the land-class grid -- the same
   translation the storage ledger scores (:func:`apply_interventions`), so the
   picture and the headline number cannot disagree;
2. re-runs the SCS-CN generation / retention partition per cell
   (:func:`_runoff_and_unstored`, the ledger's own arithmetic);
3. solves the level-pool fill for the scenario's unstored volume and re-renders
   the wet-grid PNG;
4. re-renders the heat-exposure PNG from the scenario land classes, blended
   with the same sky-view/shade terms the offline overlay uses;
5. returns the ledger stats (baseline vs scenario) and a plain-language
   explanation -- an LLM when one is configured, a labelled template otherwise.

What it deliberately does NOT do: recondition the terrain (Priority-Flood runs
offline; the run consumes the conditioned surface the cascade already uses),
recompute SVF/shade per request (they depend on the massing, not the scenario,
so they are cached and invalidated on city-model mtime), or claim to be a
flood model. Every response carries the screening warning.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.config import settings  # noqa: E402
from app.services import scene_service  # noqa: E402
from app.services.sandbox_model import (  # noqa: E402
    Grids,
    _runoff_and_unstored,
    apply_interventions,
    cn_grid_for,
    load_grids,
    storage_ledger,
    _scenario_ledger,
)

# The renderers and the level-pool solver are the offline pipeline's own,
# imported -- never re-implemented. A second copy of the fill or the colour
# ramp would let a live run and the committed artefact disagree about what the
# same numbers look like.
from make_wetgrid import _fill_to_volume, to_rgba  # noqa: E402
from make_heatmap import HEATWAVE_STRETCH, ramp_rgba  # noqa: E402
from _overlay_common import HEAT_INDEX, LAND_CLASSES  # noqa: E402

RUN_STATUS = "SCREENING_LIVE_RUN_NOT_A_FLOOD_PREDICTION"
RUN_WARNING = (
    "Screening-level SCS Curve Number runoff with a level-pool allocation on "
    "real (Priority-Flood conditioned) terrain, plus a relative 0-1 "
    "heat-exposure index. Not a flood prediction, not degrees Celsius, not "
    "engineering-grade."
)

# The slider temperature at which the heatwave *display* preset kicks in. The
# preset is a display transform on the same field (see make_heatmap), never a
# change to the physics: the land-class heat index does not know about air
# temperature, and pretending it does would be the one dishonest number in the
# loop.
HEATWAVE_THRESHOLD_C = 35.0

# Above the slider's rainfall maximum the CN formula's own validity is
# questionable; the schema bounds the request, this is just the labelled cap.
MAX_RAINFALL_MM = 800.0


class RunInputsError(ValueError):
    """A run request the model refuses, with the reason spelled out."""


# --------------------------------------------------------------------------- #
# Cached heavy inputs
# --------------------------------------------------------------------------- #

_GRIDS_CACHE: dict[str, Any] = {}
_HEAT_TERMS_CACHE: dict[str, Any] = {}


def _grids() -> Grids:
    """The shared rasters, loaded once per process.

    ``load_grids()`` re-reads every npy on each call; the reporting endpoints
    can afford that, an interactive run loop cannot. The rasters only change
    when the offline scripts re-run, which restarts nothing -- so the cache
    keys on the wet-grid mtime and reloads if the artefact is regenerated.
    """
    marker = settings.rasters_dir / "wetgrid_hsk.npy"
    mtime = marker.stat().st_mtime if marker.exists() else None
    if _GRIDS_CACHE.get("mtime") != mtime:
        grids = load_grids()
        if grids is None:
            raise RunInputsError(
                "no analysis on disk: run scripts/fetch_dem.py, "
                "scripts/fetch_landcover.py and scripts/make_wetgrid.py"
            )
        _GRIDS_CACHE.clear()
        _GRIDS_CACHE.update({"mtime": mtime, "grids": grids})
    return _GRIDS_CACHE["grids"]


def _heat_terms(grids: Grids, scene_id: str) -> dict[str, np.ndarray] | None:
    """Sky-view factor and shaded fraction for the current massing, cached.

    These depend on the building geometry, not on the scenario: a retention
    basin or a rainfall change does not move a tower. Recomputing them per run
    would make every slider drag cost the most expensive rasters in the
    project, so they are cached and invalidated only when the city-model file
    changes. ``None`` means no city model -- the land-class index alone, said
    plainly in the sidecar.
    """
    model = scene_service.city_model_path(scene_id)
    mtime = None
    if model and Path(model).exists():
        mtime = Path(model).stat().st_mtime
    if _HEAT_TERMS_CACHE.get("mtime") == mtime and "terms" in _HEAT_TERMS_CACHE:
        return _HEAT_TERMS_CACHE["terms"]

    from app.services.design_metrics import (
        HEAT_SUMMER_DAY_OF_YEAR,
        footprints_from_geojson,
        rasterise_height,
        shade_mask,
        sky_view_factor_raster,
    )

    terms: dict[str, np.ndarray] | None = None
    if mtime is not None:
        features = json.loads(Path(model).read_text()).get("features", [])
        if features:
            cell_m = float(grids.cell_area_m2) ** 0.5
            fps = footprints_from_geojson(features)
            height = rasterise_height(fps, grids)
            terms = {
                "svf": sky_view_factor_raster(height, cell_m),
                # 14:00 on the July anchor day: the same solar inputs the
                # committed heatmap artefact uses, so a live run and the
                # offline overlay differ only in land cover, never in sun.
                "shaded": shade_mask(fps, grids, 22.44, 114.01,
                                     HEAT_SUMMER_DAY_OF_YEAR, 14.0
                                     ).astype(np.float32),
            }
    _HEAT_TERMS_CACHE.clear()
    _HEAT_TERMS_CACHE.update({"mtime": mtime, "terms": terms})
    return terms


# --------------------------------------------------------------------------- #
# The heat field
# --------------------------------------------------------------------------- #

def _heat_field(grids: Grids, land_class: np.ndarray,
                terms: dict[str, np.ndarray] | None,
                heatwave: bool) -> np.ndarray:
    """The blended 0-1 heat-exposure field for one land-class grid.

    Same blend as ``make_heatmap._blend_field``: land class + sky view + shade
    when the massing exists, land class alone (and said so) when it does not.
    The heatwave preset applies the offline pipeline's own display stretch --
    a render transform on the same field, never a change to it (ADR-024).
    """
    from app.services.design_metrics import blended_heat_field

    lut = np.array([HEAT_INDEX[name] for name in LAND_CLASSES], dtype=np.float32)
    land_index = lut[land_class].astype(np.float32)
    if terms is None:
        field = land_index
    else:
        field = blended_heat_field(land_index, terms["svf"], terms["shaded"])
    if heatwave:
        field = np.clip(field * HEATWAVE_STRETCH, 0.0, 1.0)
    return field


# --------------------------------------------------------------------------- #
# The explanation
# --------------------------------------------------------------------------- #

def _template_explanation(stats: dict[str, Any], rainfall_mm: float,
                          heatwave: bool, applied: list[dict],
                          skipped: list[dict]) -> str:
    """The no-LLM explanation: the same facts, assembled by code.

    Labelled ``explanationSource: "template"`` in the response -- a demo with
    no key must not read as if a model wrote this (CLAUDE.md's standing rule).
    """
    delta = stats["delta"]["unstoredM3"]
    scen = stats["scenario"]
    base = stats["baseline"]
    if delta > 1.0:
        comparison = f"{abs(delta):,.0f} m3 worse than today"
    elif delta < -1.0:
        comparison = f"{abs(delta):,.0f} m3 better than today"
    else:
        comparison = "essentially unchanged from today"
    parts = [
        f"At {rainfall_mm:.0f} mm of rainfall, this scenario leaves "
        f"{scen['unstoredM3']:,.0f} m3 of runoff with nowhere to go -- "
        f"{comparison} "
        f"(baseline {base['unstoredM3']:,.0f} m3).",
        f"Water ponds over {scen['wetAreaHa']:,.1f} ha of the low ground "
        f"(today: {base['wetAreaHa']:,.1f} ha).",
        f"Mean heat-exposure index {scen['heatMean']:.3f} "
        f"({stats['delta']['heatMean']:+.3f} vs today) on the relative 0-1 "
        "scale -- not a temperature.",
    ]
    if applied:
        kinds = ", ".join(sorted({a["kind"] for a in applied}))
        parts.append(f"{len(applied)} intervention(s) shaped this run: {kinds}.")
    if skipped:
        parts.append(
            f"{len(skipped)} intervention(s) had no land-cover effect the "
            "model can score and were reported, not approximated."
        )
    if heatwave:
        parts.append(
            "The heatwave preset is a display stretch on the same field, not a "
            "different scenario -- compare runs at the same preset."
        )
    parts.append("Screening-level SCS-CN on real terrain; not a flood prediction.")
    return " ".join(parts)


def _llm_explanation(stats: dict[str, Any], rainfall_mm: float,
                     temperature_c: float, heatwave: bool,
                     applied: list[dict], skipped: list[dict]
                     ) -> tuple[str, str] | None:
    """One short planner-facing paragraph from a configured LLM, or None.

    The model gets the numbers and is forbidden from inventing new ones; the
    arithmetic stays in code, which is the division of labour the whole
    project is built on. Any failure -- no key, network, malformed reply --
    returns None and the caller falls back to the template, labelled.
    """
    from app.services import ai_plan

    facts = {
        "rainfallMm": rainfall_mm,
        "temperatureC": temperature_c,
        "heatwaveDisplayPreset": heatwave,
        "baseline": stats["baseline"],
        "scenario": stats["scenario"],
        "delta": stats["delta"],
        "interventionsApplied": [
            {"kind": a["kind"], "areaM2": a["areaM2"]} for a in applied
        ],
        "interventionsSkipped": [
            {"kind": s["kind"], "why": s["why"]} for s in skipped
        ],
    }
    prompt = (
        "You are explaining one screening-level land-use simulation run to an "
        "urban planner. Write at most 110 words. Rules: use ONLY the numbers "
        "in the JSON below -- never invent a figure; say 'unstored runoff' "
        "for unstoredM3; state whether the scenario is better or worse than "
        "today and by how much; mention the wet area and the mean heat-exposure "
        "index; if the heatwave display preset is on, say it is a display "
        "transform, not a different scenario; end by stating this is a "
        "screening-level estimate, not a flood prediction, and that the heat "
        "index is relative, not a temperature.\n\n"
        + json.dumps(facts, indent=1)
    )

    anthropic = ai_plan.LLMProvider()
    if anthropic.available:
        try:
            raw = ai_plan._post_json(
                ai_plan.ANTHROPIC_URL, anthropic.api_key,
                {
                    "model": anthropic.model,
                    "max_tokens": 320,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            text = "".join(
                str(block.get("text", ""))
                for block in raw.get("content", []) or []
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if text:
                return text, "anthropic"
        except Exception:  # noqa: BLE001 - fall through to the next provider
            pass

    compat = ai_plan.OpenAICompatProvider()
    if compat.available:
        try:
            raw = ai_plan._post_json(
                compat._endpoint(), compat.api_key,
                {
                    "model": compat.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 320,
                },
            )
            text = str(((raw.get("choices") or [{}])[0]
                        .get("message", {}) or {}).get("content") or "").strip()
            if text:
                return text, "openai-compatible"
        except Exception:  # noqa: BLE001 - the template covers this
            pass
    return None


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_scenario(scene_id: str, rainfall_mm: float, temperature_c: float,
                 plan_id: str | None,
                 features: list[dict[str, Any]]) -> dict[str, Any]:
    """One live run: scenario grid -> overlays on disk -> stats + explanation.

    ``features`` are the ledger-ready intervention dicts for the chosen plan
    (the router unwraps them, as it does for ``GET .../ledger``); an empty list
    is a baseline run. The PNGs are written beside the committed artefacts in
    ``data/rasters/run/`` -- never over them: the offline outputs stay the
    canonical baseline and a live run is a render of one scenario.
    """
    scene_service.get_scene(scene_id)
    if not 0.0 <= rainfall_mm <= MAX_RAINFALL_MM:
        raise RunInputsError(
            f"rainfallMm must be 0-{MAX_RAINFALL_MM:.0f}, got {rainfall_mm}"
        )

    grids = _grids()
    cell_area = grids.cell_area_m2

    # ---- scenario grid -----------------------------------------------------
    trial, cn_over, applied, skipped = apply_interventions(grids, features)

    # ---- wet grid: the ledger's own per-cell arithmetic, then the fill -----
    cn_grid = cn_grid_for(trial, cn_over) if cn_over else None
    _, unstored_m = _runoff_and_unstored(trial, rainfall_mm, cn_grid=cn_grid)
    volume_m3 = float(unstored_m.sum() * cell_area)
    depth_field, water_level = _fill_to_volume(
        np.asarray(grids.elevation_5m, dtype=np.float64), volume_m3, cell_area
    )
    wet_cells = int((depth_field > 0).sum())
    wet_area_ha = wet_cells * cell_area / 10_000.0

    # Baseline wet extent under the same rainfall, for the delta. The fill is
    # the cheap part (bisection over a flat array); the volume is the ledger's.
    base_unstored_m3 = storage_ledger(grids, grids.land_class, rainfall_mm)[
        "unstoredM3"]
    base_depth, _ = _fill_to_volume(
        np.asarray(grids.elevation_5m, dtype=np.float64), base_unstored_m3,
        cell_area
    )
    base_wet_area_ha = float((base_depth > 0).sum()) * cell_area / 10_000.0

    # ---- heat field ---------------------------------------------------------
    heatwave = temperature_c >= HEATWAVE_THRESHOLD_C
    terms = _heat_terms(grids, scene_id)
    scen_heat = _heat_field(grids, trial, terms, heatwave)
    base_heat = _heat_field(grids, grids.land_class, terms, heatwave)

    # ---- stats ---------------------------------------------------------------
    scen_ledger = _scenario_ledger(grids, trial, cn_over, rainfall_mm)
    base_ledger = storage_ledger(grids, grids.land_class, rainfall_mm)

    stats = {
        "baseline": {
            **base_ledger,
            "wetAreaHa": round(base_wet_area_ha, 2),
            "heatBlendedMean": round(float(base_heat.mean()), 4),
        },
        "scenario": {
            **scen_ledger,
            "wetAreaHa": round(wet_area_ha, 2),
            "heatBlendedMean": round(float(scen_heat.mean()), 4),
        },
        "delta": {
            "unstoredM3": round(
                scen_ledger["unstoredM3"] - base_ledger["unstoredM3"], 1),
            "runoffM3": round(
                scen_ledger["runoffM3"] - base_ledger["runoffM3"], 1),
            "wetAreaHa": round(wet_area_ha - base_wet_area_ha, 2),
            "heatMean": round(
                scen_ledger["heatMean"] - base_ledger["heatMean"], 4),
            "heatBlendedMean": round(
                float(scen_heat.mean() - base_heat.mean()), 4),
            "greenAreaM2": round(
                scen_ledger["greenAreaM2"] - base_ledger["greenAreaM2"], 1),
            "pavedAreaM2": round(
                scen_ledger["pavedAreaM2"] - base_ledger["pavedAreaM2"], 1),
        },
    }

    # ---- render ---------------------------------------------------------------
    run_id = uuid.uuid4().hex[:8]
    run_dir = settings.rasters_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    wet_png = run_dir / "wetgrid.png"
    heat_png = run_dir / "heatmap.png"
    Image.fromarray(to_rgba(depth_field), mode="RGBA").save(wet_png)
    Image.fromarray(ramp_rgba(scen_heat), mode="RGBA").save(heat_png)

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (run_dir / "wetgrid.json").write_text(json.dumps({
        "artefact": "wetgrid-live-run",
        "runId": run_id,
        "generated": generated,
        "status": RUN_STATUS,
        "warning": RUN_WARNING,
        "terrainSource": grids.terrain_source,
        "landCoverSource": grids.landcover_source,
        "inputs": {"rainfallMm": rainfall_mm, "planId": plan_id},
        "results": {
            "unstoredVolumeM3": round(volume_m3, 1),
            "wetAreaHa": round(wet_area_ha, 2),
            "waterLevelM": round(float(water_level), 3),
            "meanDepthInWetAreaM": round(
                volume_m3 / max(wet_cells * cell_area, 1e-9), 4),
        },
    }, indent=2), encoding="utf-8")
    (run_dir / "heatmap.json").write_text(json.dumps({
        "artefact": "heatmap-live-run",
        "runId": run_id,
        "generated": generated,
        "status": RUN_STATUS,
        "warning": RUN_WARNING + (
            "" if terms is not None else
            " No city model: land-class index only, no sky-view or shade term."
        ),
        "inputs": {
            "temperatureC": temperature_c,
            "heatwaveDisplayPreset": heatwave,
            "planId": plan_id,
        },
        "results": {
            "meanIndex": round(float(scen_heat.mean()), 4),
            "deltaVsBaseline": stats["delta"]["heatBlendedMean"],
        },
    }, indent=2), encoding="utf-8")

    # ---- explanation -----------------------------------------------------------
    llm = _llm_explanation(stats, rainfall_mm, temperature_c, heatwave,
                           applied, skipped)
    if llm is not None:
        explanation, source = llm
    else:
        explanation = _template_explanation(stats, rainfall_mm, heatwave,
                                            applied, skipped)
        source = "template"

    wet_url = settings.public_url("rasters/run/wetgrid.png")
    heat_url = settings.public_url("rasters/run/heatmap.png")
    return {
        "runId": run_id,
        "sceneId": scene_id,
        "inputs": {
            "rainfallMm": rainfall_mm,
            "temperatureC": temperature_c,
            "heatPreset": "heatwave" if heatwave else "normal",
            "planId": plan_id,
        },
        "overlays": {
            "wetGridUrl": f"{wet_url}?v={run_id}",
            "heatmapUrl": f"{heat_url}?v={run_id}",
        },
        "stats": stats,
        "worsens": stats["delta"]["unstoredM3"] > 1.0,
        "interventionsApplied": applied,
        "interventionsSkipped": skipped,
        "explanation": explanation,
        "explanationSource": source,
        "status": RUN_STATUS,
        "warning": RUN_WARNING,
    }
