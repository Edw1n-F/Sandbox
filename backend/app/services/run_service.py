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
from app.services import run_card  # noqa: E402
from app.services import scene_service  # noqa: E402
from app.services.sandbox_model import (  # noqa: E402
    DEFAULT_TIDE_STATE,
    TIDE_STATES,
    Grids,
    _runoff_and_unstored,
    apply_interventions,
    cn_grid_for,
    load_grids,
    sanity_checks,
    storage_ledger,
    _scenario_ledger,
)

# The renderers and the level-pool solver are the offline pipeline's own,
# imported -- never re-implemented. A second copy of the fill or the colour
# ramp would let a live run and the committed artefact disagree about what the
# same numbers look like.
from make_wetgrid import (  # noqa: E402
    FLOOD_DEPTH_M,
    _fill_to_volume,
    flood_rgba,
    to_rgba,
)
from _overlay_common import HEAT_INDEX, LAND_CLASSES  # noqa: E402
from hydrology import fill_and_spill  # noqa: E402
from app.services.temperature_field import (  # noqa: E402
    SURFACE_TO_AIR_OFFSET_C,
    load_observed_lst,
    temperature_field,
)

RUN_STATUS = "SCREENING_LIVE_RUN_FLOOD_EXTENT"
RUN_WARNING = (
    "Screening-level flood extent: SCS Curve Number runoff routed down the D8 "
    "receiver chain, cells with >= %.2f m standing water shown as flooded. Not "
    "engineering-grade, not a regulatory flood map." % FLOOD_DEPTH_M
)
# The backwater caveat, appended when a non-zero tide state is chosen. The tide
# is a D8-outlet boundary condition -- at high tide the outfall cannot discharge
# and water stays on the grid -- not a hydraulic outfall model. No pipe or
# outfall geometry is invented.
TIDE_WARNING = (
    " Tidal backwater applied as a screening boundary condition (%.2f mCD "
    "tailwater at the outfall); no pipe/outfall hydraulics, so the effect is "
    "indicative, not a regulatory tide-locking assessment."
)

# The diverging ramp for the temperature *anomaly* map (deviation from the site
# mean, in C). Blue = cooler than average, neutral = average, red = hotter than
# average. A well-designed site reads near-neutral; only genuine hot spots go
# red, which is the whole point of showing the signal rather than a wall of
# "it is hot in Hong Kong". Centred on zero by construction.
#
# Neutral is TRANSPARENT, not white: a cell at ~0 deviation fades the overlay to
# nothing so the base map shows through. The white band in the first two renders
# was blinding -- a bright wash over most of the grid -- and reading as "data"
# when it was really "no signal". Alpha ramps from 0 at 0 to full at the band
# edge, and the RGB still follows the diverging stops so the fade stays on-hue.
#
# Scale note: a real site's anomaly distribution is TIGHT (91% of cells within
# +/-2 C). The neutral band is therefore only +/-0.5 C, and the mid colours are
# already saturated by +/-2 C -- otherwise the whole grid lands in the neutral
# band and the map reads as a washed-out blank with a faint tint.
ANOMALY_RAMP = (
    (-6.0, (33, 102, 172)),    # deep blue    - much cooler
    (-4.0, (70, 148, 178)),    # blue         - cooler
    (-2.0, (150, 205, 178)),   # teal-green   - visibly cooler
    (-0.5, (205, 222, 210)),   # pale green-grey - slightly cooler
    (0.0, (228, 222, 208)),    # neutral      - average (alpha -> 0)
    (0.5, (240, 204, 148)),    # tan          - slightly warmer
    (2.0, (242, 166, 90)),     # orange       - visibly warmer
    (4.0, (222, 100, 60)),     # red-orange   - hot spot
    (6.0, (168, 32, 48)),      # dark red     - much warmer
)

# Neutral is TRANSPARENT and WHITE-FREE: the ramp carries no light/white stops,
# so a near-average cell cannot paint a bright patch no matter what alpha it
# gets. On top of that a dead-band (below) makes |anomaly| under the threshold
# fully invisible, and the alpha then ramps in -- so "no signal" shows the base
# map and every visible tint is a saturated hue.
#
# Scale note: a real site's anomaly distribution is TIGHT (median |anomaly|
# 1.1 C, 92% within +/-2 C). The ramp saturates its colours by +/-2 C so the
# structure is readable without exaggerating the extremes.
ANOMALY_RAMP = (
    (-6.0, (33, 102, 172)),    # deep blue    - much cooler
    (-4.0, (70, 148, 178)),    # blue         - cooler
    (-2.0, (118, 196, 178)),   # teal         - visibly cooler
    (-0.5, (168, 214, 196)),   # soft teal    - slightly cooler
    (0.0, (188, 198, 189)),    # grey-green   - average (alpha -> 0)
    (0.5, (222, 194, 138)),    # sand         - slightly warmer
    (2.0, (242, 166, 90)),     # orange       - visibly warmer
    (4.0, (222, 100, 60)),     # red-orange   - hot spot
    (6.0, (168, 32, 48)),      # dark red     - much warmer
)

# Dead-band half-width (C): cells closer to the mean than this are fully
# transparent. Beyond it, alpha ramps to opaque by ANOMALY_OPAQUE_C.
ANOMALY_NEUTRAL_HALF_WIDTH_C = 0.4
ANOMALY_OPAQUE_C = 2.5


def anomaly_rgba(anomaly_c: np.ndarray) -> np.ndarray:
    """Map a deviation-from-mean temperature field (C) to RGBA, diverging at 0.

    RGB follows the diverging stops (no white); alpha is 0 inside the dead-band
    (``ANOMALY_NEUTRAL_HALF_WIDTH_C``) and ramps to fully opaque by
    ``ANOMALY_OPAQUE_C``, so "no signal" cells show the base map and every
    visible cell is a saturated hue rather than a bright wash.
    """
    stops = np.array([s[0] for s in ANOMALY_RAMP], dtype=np.float64)
    colours = np.array([s[1] for s in ANOMALY_RAMP], dtype=np.float64)
    clipped = np.clip(anomaly_c, stops[0], stops[-1])
    out = np.empty(anomaly_c.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = np.interp(clipped, stops, colours[:, channel])
    # Alpha: 0 inside the dead-band, 255 at/after the opaque distance.
    mag = np.abs(anomaly_c)
    span = ANOMALY_OPAQUE_C - ANOMALY_NEUTRAL_HALF_WIDTH_C
    alpha = np.clip((mag - ANOMALY_NEUTRAL_HALF_WIDTH_C) / span, 0.0, 1.0) * 255.0
    out[..., 3] = alpha.astype(np.uint8)
    return out

# The temperature slider sets the *scenario mean surface temperature* (C), the
# temperature analogue of the rainfall slider's intensity: a real input driving
# one honest output (the field re-anchors so its mean matches). The observed
# Landsat scene supplies the default; presets are documented offsets above it.

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


def _heat_terms(grids: Grids, scene_id: str,
                overrides: list[dict[str, Any]] | None = None
                ) -> dict[str, np.ndarray] | None:
    """Sky-view factor, shade, wind shelter and height for a massing, cached.

    These depend on the building geometry, not on the scenario: a retention
    basin or a rainfall change does not move a tower. Recomputing them per run
    would make every slider drag cost the most expensive rasters in the
    project, so they are cached and invalidated only when the city-model file
    changes *or* the planned-project massing does. ``None`` means no city model
    -- the land-class index alone, said plainly in the sidecar.

    ``overrides`` are the planned projects' massing effects for this run: each
    ``{"ring": [[lon, lat], ...], "heightM": h}``. A project that replaces part
    of today's city needs its own terms, because shade, sky view and wind
    shelter are all read off the footprints -- scoring its land-class change
    alone would leave its heat effect invisible.

    The cache key carries the overrides, so a run with no project still hits the
    cache it always hit, and a station run never serves the station-free terms.
    """
    model = scene_service.buildings_path(scene_id)
    mtime = None
    if model and Path(model).exists():
        mtime = Path(model).stat().st_mtime

    # A stable key for the override set. Rings are rounded to ~0.1 m so a float
    # jitter in a stored polygon does not silently miss the cache; the height is
    # rounded to 0.01 m for the same reason.
    signature = tuple(sorted(
        (tuple((round(float(x), 6), round(float(y), 6))
               for x, y in (o.get("ring") or [])),
         round(float(o.get("heightM") or 0.0), 2))
        for o in (overrides or [])
    ))
    key = (mtime, signature)
    if _HEAT_TERMS_CACHE.get("key") == key and "terms" in _HEAT_TERMS_CACHE:
        return _HEAT_TERMS_CACHE["terms"]

    from app.services.design_metrics import (
        SITE_LAT_DEG,
        SITE_LON_DEG,
        HEAT_SUMMER_DAY_OF_YEAR,
        footprints_from_geojson,
        footprint_from_ring,
        rasterise_height,
        shade_mask,
        sky_view_factor_raster,
        cell_fad,
    )
    from app.services.ai_plan import _point_in_ring

    terms: dict[str, np.ndarray] | None = None
    if mtime is not None:
        features = json.loads(Path(model).read_text()).get("features", [])
        fps = footprints_from_geojson(features)

        # ---- planned-project massing -------------------------------------
        # Remove the existing envelopes the project sits on, then add the
        # project's own block. A project footprint that overlaps nothing simply
        # adds; one that covers six buildings replaces them, which is what makes
        # its shade and wind exposure its own rather than the city's.
        for o in (overrides or []):
            ring = o.get("ring") or []
            height = float(o.get("heightM") or 0.0)
            if height <= 0 or len(ring) < 3:
                continue
            fps = [f for f in fps if not _point_in_ring(f.lon, f.lat, ring)]
            block = footprint_from_ring(ring, height)
            if block is not None:
                fps.append(block)

        if fps:
            cell_m = float(grids.cell_area_m2) ** 0.5
            height = rasterise_height(fps, grids)
            terms = {
                "svf": sky_view_factor_raster(height, cell_m),
                # 14:00 on the July anchor day: the same solar inputs the
                # committed heatmap artefact uses, so a live run and the
                # offline overlay differ only in land cover, never in sun.
                "shaded": shade_mask(fps, grids, SITE_LAT_DEG, SITE_LON_DEG,
                                     HEAT_SUMMER_DAY_OF_YEAR, 14.0
                                     ).astype(np.float32),
                # Wind shelter: per-cell frontal-area density for the prevailing
                # NE wind (AVRG73). Higher FAD -> more blocked -> hotter. This is
                # geometry, not a wind speed (FAD is not a velocity ratio).
                "fad": cell_fad(fps, grids, 45.0).astype(np.float32),
                # Building height raster, kept so the wet-grid render can mask
                # cells under a footprint (see the render block): the flood is
                # solved on bare earth, so without this a pond under a tower
                # would paint the tower's roof once classified onto 3D Tiles.
                "height": height,
            }
    _HEAT_TERMS_CACHE.clear()
    _HEAT_TERMS_CACHE.update({"key": key, "terms": terms})
    return terms


def _massing_overrides(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The planned projects' massing effects, ready for :func:`_heat_terms`.

    Reads every ``station_massing``-style feature out of the run's intervention
    list and returns its ring and height. Paired with
    :data:`INTERVENTION_EFFECTS`, whose ``station_massing`` entry has no grid
    effect on purpose: the same feature moves nothing on the land-class grid and
    everything on the geometry rasters, and the two must not be confused.
    """
    out: list[dict[str, Any]] = []
    for feature in features or []:
        kind = str(feature.get("intervention_type")
                   or feature.get("interventionType") or "")
        if kind != "station_massing":
            continue
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        # A Polygon arrives as a list of rings; take the outer one, the same
        # convention `footprint_from_ring` and the ledger already use.
        ring = coords[0] if coords and isinstance(coords[0], (list, tuple)) else coords
        params = feature.get("params") or {}
        try:
            height = float(params.get("heightM", params.get("height_m")))
        except (TypeError, ValueError):
            continue
        out.append({"ring": ring, "heightM": height})
    return out


# --------------------------------------------------------------------------- #
# The temperature field
# --------------------------------------------------------------------------- #

def _land_index(grids: Grids, land_class: np.ndarray) -> np.ndarray:
    """The 0-1 land-class heat index for a grid (the HEAT_INDEX lookup)."""
    lut = np.array([HEAT_INDEX[name] for name in LAND_CLASSES], dtype=np.float32)
    return lut[land_class].astype(np.float32)


def _temperature_fields(grids: Grids, land_class: np.ndarray,
                        terms: dict[str, np.ndarray] | None,
                        lst_base: np.ndarray | None,
                        scenario_mean_c: float | None,
                        ) -> dict[str, Any]:
    """The anchored surface-temperature field (C) for one land-class grid.

    Combines the observed LST base with the geometry terms (land class, sky
    view, shade, wind shelter) as deviation-from-mean, then re-anchors to the
    scenario mean. Returns ``surfaceC`` (absolute) and ``anomalyC`` (deviation),
    plus the summary stats. When ``terms`` is None (no city model) the geometry
    terms collapse to land-class only, and the sidecar says so.
    """
    land_index = _land_index(grids, land_class)
    shape = land_index.shape
    if terms is None:
        svf = np.ones(shape, dtype=np.float32)
        shaded = np.zeros(shape, dtype=np.float32)
        fad = np.zeros(shape, dtype=np.float32)
    else:
        svf = terms["svf"]
        shaded = terms["shaded"]
        fad = terms["fad"]
    return temperature_field(
        land_index=land_index,
        svf=svf,
        shaded=shaded,
        fad=fad,
        lst_base=lst_base,
        scenario_mean_c=scenario_mean_c,
    )


# --------------------------------------------------------------------------- #
# The explanation
# --------------------------------------------------------------------------- #

def _rounded(node: Any) -> Any:
    """Round floats for the prompt so the model quotes readable figures."""
    if isinstance(node, float):
        return round(node) if abs(node) >= 100 else round(node, 3)
    if isinstance(node, dict):
        return {k: _rounded(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_rounded(v) for v in node]
    return node


def _llm_explanation(stats: dict[str, Any], rainfall_mm: float,
                     temperature_c: float,
                     applied: list[dict], skipped: list[dict],
                     data_quality: dict[str, Any] | None = None
                     ) -> tuple[Any, str, str | None]:
    """The run's result card, written by a model, or None.

    Returns ``(card | None, source, reason)``. `reason` is None when a model
    wrote it and otherwise names why code did.

    The model gets the numbers and is forbidden from inventing new ones; the
    arithmetic stays in code, which is the division of labour the whole project
    is built on. A card whose numbers do not trace back is returned with the
    grounding report attached rather than discarded, so a reader can see which
    figure to distrust instead of losing the answer entirely.

    `data_quality` is the run's own report on its inputs (see `build_facts`'s
    `data` block); it caps the confidence the model may claim.
    """
    # The model writes the *card*, not a paragraph, so the run and the chat
    # produce one shape. `complete_json` forces the schema where the provider
    # supports it, and the reply is grounded and ladder-capped exactly as a chat
    # answer is -- the run gets no exemption from the number check.
    from app.agent import confidence, grounding, llm, prompts
    from app.agent.answer import ANSWER_SCHEMA, AnswerCard, GroundingReport

    # The grounding check reads the same fact shape the chat uses, so the two
    # paths cannot disagree about what counts as a number this run produced.
    checkable = {
        "inputs": {"rainfallMm": rainfall_mm, "temperatureC": temperature_c},
        "scenario": stats.get("scenario") or {},
        "baseline": stats.get("baseline") or {},
        "delta": stats.get("delta") or {},
        "ratios": stats.get("ratios") or {},
        "applied": [{"kind": a["kind"], "areaM2": a["areaM2"]} for a in applied],
        "skipped": [{"kind": s["kind"], "why": s["why"]} for s in skipped],
        "data": data_quality or {},
    }
    reason: str | None = None
    try:
        turn = llm.complete_json(
            system=prompts.RUN_SYSTEM,
            messages=[llm.Message(role="user", text=prompts.user_turn(
                "Explain the run that has just completed.", checkable))],
            schema=ANSWER_SCHEMA,
            max_tokens=1500,
        )
        # `complete_json` walks the model chain past empty replies and can still
        # return one: a reasoning model that spends its whole budget on the hidden
        # trace answers 200 with `content: ""`. Parsing that raises, which would
        # land in the generic handler below and lose the real cause, so the empty
        # case is named here.
        if not (turn.text or "").strip():
            reason = run_card.explanation_note(None)
        else:
            draft = json.loads(turn.text)
            if not isinstance(draft, dict):
                reason = "the model returned a card of the wrong shape"
            else:
                card = AnswerCard.model_validate(draft)
                problems = grounding.check_card(card, checkable)
                card.grounding = GroundingReport(ok=not problems, ungrounded=problems, revised=False)
                card.source = "model"
                # The same ladder the chat uses: the model proposes a level, the
                # run's own data quality decides how high it may stand.
                card.confidence = confidence.apply_ladder(
                    card.confidence, confidence.cap_of(checkable["data"]),
                    ungrounded=bool(problems))
                # Code owns the framing fields: the eyebrow is chrome, and `kind`
                # tells the UI this card describes a run rather than answering a
                # question. A model that sets them is not wrong, just not in charge.
                card.kind = "run"
                card.eyebrow = card.eyebrow or "Sandbox run"
                return card, llm.provider_name(), None
    except llm.ProviderUnavailable as exc:
        # No provider at all. Distinct from a provider that answered and failed.
        reason = run_card.explanation_note(exc)
    except llm.LLMError as exc:
        # A 429 is NOT "no model configured", and the difference is the whole
        # reason a planner would retry rather than go looking for a missing key.
        reason = run_card.explanation_note(exc)
    except Exception as exc:  # noqa: BLE001 - the labelled template covers this
        reason = run_card.explanation_note(exc)
    return None, "template", reason


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_scenario(scene_id: str, rainfall_mm: float, temperature_c: float,
                 plan_id: str | None,
                 features: list[dict[str, Any]],
                 plan_ids: list[str] | None = None,
                 tide_state: str = DEFAULT_TIDE_STATE) -> dict[str, Any]:
    """One live run: scenario grid -> overlays on disk -> stats + explanation.

    ``features`` are the ledger-ready intervention dicts for the chosen plans
    (the router unwraps them, merging every selected plan's features into one
    list, as it does for ``GET .../ledger``); an empty list is a baseline run.
    ``plan_ids`` is the full selection, recorded so a cumulative run can say
    which projects it composed -- ``plan_id`` stays for a single-plan caller.

    ``tide_state`` is a screening backwater condition (``low``/``mean``/``high``,
    from :data:`sandbox_model.TIDE_STATES`). It raises the D8-outlet tailwater so
    a high tide holds water on the grid; it does not model pipes or outfall
    geometry, and the caveat is attached to every number it touches.

    The PNGs are written under a per-run name in ``data/rasters/run/`` -- never
    over the committed artefacts: the offline outputs stay the canonical baseline
    and a live run is a render of one scenario. The per-run name is what lets two
    planners run at the same time without overwriting each other.
    """
    scene_service.get_scene(scene_id)
    if not 0.0 <= rainfall_mm <= MAX_RAINFALL_MM:
        raise RunInputsError(
            f"rainfallMm must be 0-{MAX_RAINFALL_MM:.0f}, got {rainfall_mm}"
        )
    if tide_state not in TIDE_STATES:
        raise RunInputsError(
            f"tideState must be one of {sorted(TIDE_STATES)}, got {tide_state!r}"
        )
    tailwater_m = float(TIDE_STATES[tide_state]["tailwaterM"])

    grids = _grids()
    cell_area = grids.cell_area_m2

    # ---- scenario grid -----------------------------------------------------
    trial, cn_over, storage_over, applied, skipped = apply_interventions(
        grids, features)

    # ---- wet grid: the ledger's own per-cell arithmetic, then the fill -----
    cn_grid = cn_grid_for(trial, cn_over) if cn_over else None
    _, unstored_m = _runoff_and_unstored(trial, rainfall_mm, cn_grid=cn_grid,
                                         storage_over=storage_over or None)
    volume_m3 = float(unstored_m.sum() * cell_area)
    depth_field, water_level = _fill_to_volume(
        np.asarray(grids.elevation_5m, dtype=np.float64), volume_m3, cell_area
    )
    wet_cells = int((depth_field > 0).sum())
    wet_area_ha = wet_cells * cell_area / 10_000.0

    # ---- flood extent: route the unstored volume down the D8 cascade --------
    # The flood map is the *routed* depth field (water actually flowing downhill
    # into depressions and outfalls), not the level-pool field. A cell is
    # "flooded" where the routed standing depth reaches FLOOD_DEPTH_M. Falls back
    # to the level-pool depth when routing is unavailable (receiver/raw surface
    # not yet generated), and says so in the sidecar.
    routed_depth: np.ndarray | None = None
    if grids.receiver is not None and grids.raw_elevation_5m is not None:
        cascade = fill_and_spill(
            filled=np.asarray(grids.elevation_5m, dtype=np.float64),
            raw=np.asarray(grids.raw_elevation_5m, dtype=np.float64),
            receiver=grids.receiver,
            runoff_in_m3=unstored_m * cell_area,
            cell_area_m2=cell_area,
            tailwater_m=tailwater_m,
        )
        routed_depth = np.asarray(cascade["depthField"], dtype=np.float32)
    flood_cells = int((routed_depth >= FLOOD_DEPTH_M).sum()) if routed_depth is not None else None
    flood_area_ha = (flood_cells * cell_area / 10_000.0
                     if flood_cells is not None else None)

    # Baseline wet extent under the same rainfall, for the delta. The fill is
    # the cheap part (bisection over a flat array); the volume is the ledger's.
    base_unstored_m3 = storage_ledger(grids, grids.land_class, rainfall_mm)[
        "unstoredM3"]
    base_depth, _ = _fill_to_volume(
        np.asarray(grids.elevation_5m, dtype=np.float64), base_unstored_m3,
        cell_area
    )
    base_wet_area_ha = float((base_depth > 0).sum()) * cell_area / 10_000.0

    # Baseline routed flood extent (same rainfall, today's land cover), for the
    # flood-area delta. Computed the same way as the scenario so the two compare.
    base_flood_area_ha: float | None = None
    if grids.receiver is not None and grids.raw_elevation_5m is not None:
        base_runoff_m = _runoff_and_unstored(grids.land_class, rainfall_mm)[1]
        base_cascade = fill_and_spill(
            filled=np.asarray(grids.elevation_5m, dtype=np.float64),
            raw=np.asarray(grids.raw_elevation_5m, dtype=np.float64),
            receiver=grids.receiver,
            runoff_in_m3=base_runoff_m * cell_area,
            cell_area_m2=cell_area,
            tailwater_m=tailwater_m,
        )
        base_routed = np.asarray(base_cascade["depthField"], dtype=np.float32)
        base_flood_area_ha = float((base_routed >= FLOOD_DEPTH_M).sum()) * cell_area / 10_000.0

    # ---- temperature field ---------------------------------------------------
    # The slider sets the scenario mean surface temperature (C). The observed LST
    # scene supplies the base; the geometry terms (land + shade + sky view + wind
    # shelter) express each cell's deviation from the mean.
    #
    # Baseline and scenario deliberately use DIFFERENT massing terms when a
    # planned project is active. A baseline is "today", so it keeps today's city;
    # the scenario has the project built in its place. Sharing one term set -- as
    # this did before projects could carry a massing effect -- would cancel the
    # project's shade and wind exposure out of the delta, because both sides
    # would subtract the same geometry. That is the whole point of scoring a
    # project, so the two are computed separately.
    overrides = _massing_overrides(features)
    terms = _heat_terms(grids, scene_id)
    scen_terms = _heat_terms(grids, scene_id, overrides) if overrides else terms
    lst_base, lst_meta = load_observed_lst(grids.elevation_5m.shape[0],
                                           grids.elevation_5m.shape[1])
    scenario_mean_c = float(temperature_c)
    scen_temp = _temperature_fields(grids, trial, scen_terms, lst_base, scenario_mean_c)
    base_temp = _temperature_fields(grids, grids.land_class, terms, lst_base,
                                    scenario_mean_c)

    # ---- stats ---------------------------------------------------------------
    scen_ledger = _scenario_ledger(grids, trial, cn_over, rainfall_mm,
                                   storage_over or None)
    base_ledger = storage_ledger(grids, grids.land_class, rainfall_mm)

    stats = {
        "tide": {
            "state": tide_state,
            "label": TIDE_STATES[tide_state]["label"],
            "tailwaterM": tailwater_m,
            "source": TIDE_STATES[tide_state]["source"],
            "active": tailwater_m > 0.0,
        },
        "temperature": {
            "quantity": "surface (skin) temperature",
            "unit": "C",
            "observedMeanC": lst_meta.get("observedMeanC"),
            "scenarioMeanC": scenario_mean_c,
            "surfaceToAirOffsetC": SURFACE_TO_AIR_OFFSET_C,
            "source": lst_meta.get("source"),
            "note": (
                "Surface temperature anchored to an observed Landsat LST scene; "
                "rendered as deviation from the site mean. Air temperature is "
                f"estimated as surface minus {SURFACE_TO_AIR_OFFSET_C:.0f} C."
            ),
        },
        "baseline": {
            **base_ledger,
            "wetAreaHa": round(base_wet_area_ha, 2),
            "floodAreaHa": round(base_flood_area_ha, 2) if base_flood_area_ha is not None else None,
            "heatBlendedMean": round(float(base_ledger.get("heatMean", 0.0)), 4),
            "surfaceMeanC": base_temp["meanSurfaceC"],
            "surfaceMaxC": base_temp["maxSurfaceC"],
            "anomalyMaxC": base_temp["maxAnomalyC"],
            "airMeanC": round(base_temp["meanSurfaceC"] - SURFACE_TO_AIR_OFFSET_C, 3),
        },
        "scenario": {
            **scen_ledger,
            "wetAreaHa": round(wet_area_ha, 2),
            "floodAreaHa": round(flood_area_ha, 2) if flood_area_ha is not None else None,
            "heatBlendedMean": round(float(scen_ledger.get("heatMean", 0.0)), 4),
            "surfaceMeanC": scen_temp["meanSurfaceC"],
            "surfaceMaxC": scen_temp["maxSurfaceC"],
            "anomalyMaxC": scen_temp["maxAnomalyC"],
            "airMeanC": round(scen_temp["meanSurfaceC"] - SURFACE_TO_AIR_OFFSET_C, 3),
        },
        "delta": {
            "unstoredM3": round(
                scen_ledger["unstoredM3"] - base_ledger["unstoredM3"], 1),
            "runoffM3": round(
                scen_ledger["runoffM3"] - base_ledger["runoffM3"], 1),
            "wetAreaHa": round(wet_area_ha - base_wet_area_ha, 2),
            "floodAreaHa": round(
                (flood_area_ha - base_flood_area_ha), 2)
                if (flood_area_ha is not None and base_flood_area_ha is not None) else None,
            "heatMean": round(
                scen_ledger["heatMean"] - base_ledger["heatMean"], 4),
            "heatBlendedMean": round(
                scen_temp["meanSurfaceC"] - base_temp["meanSurfaceC"], 4),
            "surfaceMeanC": round(
                scen_temp["meanSurfaceC"] - base_temp["meanSurfaceC"], 3),
            "surfaceMaxC": round(
                scen_temp["maxSurfaceC"] - base_temp["maxSurfaceC"], 3),
            "greenAreaM2": round(
                scen_ledger["greenAreaM2"] - base_ledger["greenAreaM2"], 1),
            "pavedAreaM2": round(
                scen_ledger["pavedAreaM2"] - base_ledger["pavedAreaM2"], 1),
            # The three figures the brief asks for that the panel was missing. Signed the
            # same way as the rest: capacity is positive when the scenario adds
            # storage, share is the change in impervious fraction, area is the
            # change in green-and-water land.
            "storageCapacityM3": round(
                scen_ledger["storageCapacityM3"]
                - base_ledger["storageCapacityM3"], 1),
            "imperviousShare": round(
                scen_ledger["imperviousShare"] - base_ledger["imperviousShare"],
                4),
            "greenWaterAreaM2": round(
                scen_ledger["greenWaterAreaM2"]
                - base_ledger["greenWaterAreaM2"], 1),
        },
    }

    # ---- derived ratios --------------------------------------------------------
    # A card likes to say "95 % of runoff has nowhere to go", and 95 is not a
    # number the run produced -- it is unstored / runoff. Grounding rightly
    # refused it, which read as a false alarm. The fix is NOT to loosen the
    # check: `app/agent/` does no arithmetic, so the run computes the shares it
    # is willing to stand behind and hands them over verbatim, and grounding then
    # accepts them because they are facts like any other.
    ratios: dict[str, float] = {}
    for name, numerator, denominator in (
        ("unstoredOverRunoff",
         scen_ledger["unstoredM3"], scen_ledger["runoffM3"]),
        ("unstoredOverRunoffBaseline",
         base_ledger["unstoredM3"], base_ledger["runoffM3"]),
        ("unstoredDeltaOverBaseline",
         scen_ledger["unstoredM3"] - base_ledger["unstoredM3"],
         base_ledger["unstoredM3"]),
        ("greenDeltaOverBaseline",
         scen_ledger["greenAreaM2"] - base_ledger["greenAreaM2"],
         base_ledger["greenAreaM2"]),
        ("pavedDeltaOverBaseline",
         scen_ledger["pavedAreaM2"] - base_ledger["pavedAreaM2"],
         base_ledger["pavedAreaM2"]),
    ):
        if denominator:
            ratios[name] = round(numerator / denominator, 6)
    stats["ratios"] = ratios

    # ---- sanity checks (brief step 3g) ---------------------------------------
    # Attached to the run rather than raising: a check that can break the run it
    # is checking would be worse than no check.
    checks = sanity_checks(grids, trial, rainfall_mm, wet_area_ha=wet_area_ha,
                           applied=applied, storage_over=storage_over or None)

    # ---- render ---------------------------------------------------------------
    run_id = uuid.uuid4().hex[:8]
    run_dir = settings.rasters_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Per-run names. A fixed `wetgrid.png` meant two planners running at the same
    # moment silently overwrote each other's overlay, so the second planner saw
    # the first planner's picture under their own numbers. The run id was already
    # in the URL as a cache-buster, which hid the collision rather than fixing it.
    wet_png = run_dir / f"wetgrid_{run_id}.png"
    heat_png = run_dir / f"heatmap_{run_id}.png"
    # Flood map when routing is available (routed depth >= threshold), else the
    # continuous ponding ramp — the fallback says why in the sidecar.
    #
    # Mask cells under a building footprint before painting. The depth is solved
    # on the bare-earth surface, so a pond can be placed where a tower stands;
    # because the overlay is classified onto 3D Tiles (there is no per-tileset
    # way to exempt buildings), that pond would render on the roof. Suppress it
    # in the PNG only — the ledger and the .npy keep every solved cell.
    footprint = (terms["height"] > 0) if (terms and "height" in terms) else None
    wet_render = routed_depth if routed_depth is not None else depth_field
    if footprint is not None:
        wet_render = np.where(footprint, 0.0, wet_render)
    if routed_depth is not None:
        Image.fromarray(flood_rgba(wet_render), mode="RGBA").save(wet_png)
    else:
        Image.fromarray(to_rgba(wet_render), mode="RGBA").save(wet_png)
    Image.fromarray(anomaly_rgba(scen_temp["anomalyC"]), mode="RGBA").save(heat_png)

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tide_active = tailwater_m > 0.0
    (run_dir / f"wetgrid_{run_id}.json").write_text(json.dumps({
        "artefact": "wetgrid-live-run",
        "runId": run_id,
        "generated": generated,
        "status": RUN_STATUS,
        "warning": RUN_WARNING + (TIDE_WARNING % tailwater_m if tide_active else ""),
        "terrainSource": grids.terrain_source,
        "landCoverSource": grids.landcover_source,
        "inputs": {"rainfallMm": rainfall_mm, "planId": plan_id,
                   "planIds": plan_ids or ([plan_id] if plan_id else [])},
        "tide": {
            "state": tide_state,
            "label": TIDE_STATES[tide_state]["label"],
            "tailwaterM": tailwater_m,
            "source": TIDE_STATES[tide_state]["source"],
        },
        "flood": {
            "thresholdM": FLOOD_DEPTH_M,
            "routed": routed_depth is not None,
            "floodedCells": flood_cells,
            "floodAreaHa": round(flood_area_ha, 2) if flood_area_ha is not None else None,
        },
        "results": {
            "unstoredVolumeM3": round(volume_m3, 1),
            "wetAreaHa": round(wet_area_ha, 2),
            "waterLevelM": round(float(water_level), 3),
            "meanDepthInWetAreaM": round(
                volume_m3 / max(wet_cells * cell_area, 1e-9), 4),
        },
    }, indent=2), encoding="utf-8")
    (run_dir / f"heatmap_{run_id}.json").write_text(json.dumps({
        "artefact": "heatmap-live-run",
        "runId": run_id,
        "generated": generated,
        "status": lst_meta["available"] and "SURFACE_TEMPERATURE_ANCHORED_TO_LST"
                  or "SURFACE_TEMPERATURE_SYNTHETIC_FALLBACK",
        "warning": (
            "Surface (skin) temperature rendered as deviation from the site "
            "mean (C). Blue cooler, red hotter than average. Not air "
            "temperature; air is estimated as surface minus "
            f"{SURFACE_TO_AIR_OFFSET_C:.0f} C. Wind shelter is geometry (FAD), "
            "not a wind speed." + (
                "" if terms is not None else
                " No city model: land-class index only, no sky-view, shade or "
                "wind-shelter term."
            )
        ),
        "inputs": {
            "temperatureC": temperature_c,
            "scenarioMeanC": scenario_mean_c,
            "planId": plan_id,
            "planIds": plan_ids or ([plan_id] if plan_id else []),
        },
        "temperature": {
            "surfaceMeanC": scen_temp["meanSurfaceC"],
            "surfaceMaxC": scen_temp["maxSurfaceC"],
            "anomalyMaxC": scen_temp["maxAnomalyC"],
            "airMeanC": round(
                scen_temp["meanSurfaceC"] - SURFACE_TO_AIR_OFFSET_C, 3),
            "observedMeanC": scen_temp["observedMeanC"],
            "lstSource": lst_meta.get("source"),
        },
        "results": {
            "deltaSurfaceMeanC": stats["delta"]["surfaceMeanC"],
            "deltaSurfaceMaxC": stats["delta"]["surfaceMaxC"],
        },
    }, indent=2), encoding="utf-8")

    _prune_run_dir(run_dir)

    # ---- explanation -----------------------------------------------------------
    # What this run stood on. Read from the grids rather than typed here, so the
    # card's provenance and the confidence cap both name the data the numbers
    # actually came from -- and assembled *before* the explanation, because the
    # cap reads it.
    sources = {
        "terrain": grids.terrain_source,
        "landCover": grids.landcover_source,
        "lst": lst_meta.get("source"),
    }
    data_quality = {
        "lstAvailable": bool(sources.get("lst")),
        "sanityChecksFailed": len(checks),
        "sanityCheckIds": [c.get("id") for c in checks if isinstance(c, dict)],
        "interventionsSkipped": len(skipped),
        "terrainSource": sources.get("terrain"),
        "landCoverSource": sources.get("landCover"),
    }

    # Returns (card | None, source, reason). `reason` is None when a model wrote
    # it, and otherwise says WHY code did -- "no model configured" and "the model
    # was rate-limited" are different messages for the reader.
    explanation_card, source, explanation_note = _llm_explanation(
        stats, rainfall_mm, temperature_c, applied, skipped, data_quality)
    if explanation_card is None:
        # Code writes the identical card, labelled with the real cause, so the
        # panel renders one shape whether or not a model answered.
        explanation_card = run_card.run_card(stats, rainfall_mm, temperature_c,
                                             applied, skipped,
                                             reason=explanation_note)
    explanation = run_card.explanation_text(explanation_card, stats.get("tide"))

    wet_url = settings.public_url(f"rasters/run/wetgrid_{run_id}.png")
    heat_url = settings.public_url(f"rasters/run/heatmap_{run_id}.png")
    return {
        "runId": run_id,
        "sceneId": scene_id,
        "inputs": {
            "rainfallMm": rainfall_mm,
            "temperatureC": temperature_c,
            "scenarioMeanC": scenario_mean_c,
            "tideState": tide_state,
            "planId": plan_id,
            "planIds": plan_ids or ([plan_id] if plan_id else []),
        },
        "overlays": {
            "wetGridUrl": f"{wet_url}?v={run_id}",
            "heatmapUrl": f"{heat_url}?v={run_id}",
        },
        # What this run stood on, read from the grids rather than typed here, so a
        # card's provenance receipt names the data the numbers actually came from.
        "sources": sources,
        "stats": stats,
        "sanityChecks": checks,
        "worsens": stats["delta"]["unstoredM3"] > 1.0,
        "interventionsApplied": applied,
        "interventionsSkipped": skipped,
        "explanation": explanation,
        "explanationSource": source,
        # Only present when code wrote the card, and it names the actual cause.
        # The client shows this instead of guessing.
        "explanationNote": explanation_note,
        # The run's result card. Written by the model when one answered, by code
        # otherwise -- the same shape either way, so the panel renders one thing.
        "explanationCard": explanation_card.model_dump(),
        "status": RUN_STATUS,
        "warning": RUN_WARNING + (TIDE_WARNING % tailwater_m if tide_active else ""),
    }


def _prune_run_dir(run_dir: Path, keep: int = 20) -> None:
    """Keep the newest `keep` runs, delete older ones.

    Per-run filenames mean the directory grows without bound on a long demo.
    Twenty is enough that a planner can still flip back through recent runs
    (and enough that the sheets a demo touches stay on disk), while keeping the
    directory small enough to ignore. Deletion is by mtime and best-effort: a
    failed unlink must never fail the run that already produced its overlay.
    """
    for pattern in ("wetgrid_*.png", "heatmap_*.png",
                    "wetgrid_*.json", "heatmap_*.json"):
        files = sorted(run_dir.glob(pattern),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass
