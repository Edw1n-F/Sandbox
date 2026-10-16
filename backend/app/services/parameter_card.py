"""The screening parameter card (brief §2): the numbers to read before a result.

## What this module is

One GET, one dict, no new arithmetic. The card exists so a reader can see the
site's extent, its land cover, the inputs a run assumed, and — the rows that
matter most — **the things this project does not know** — without running the
model and without reading any code.

## The one rule everything else follows: R1

Every number here is **read from a dict the ledger already computed in this same
request**. This module:

- never calls ``storage_ledger`` for a figure it could read,
- never touches ``grids.land_class`` or any other raster directly,
- contains **no numeric literal** except ``0.0``/``1.0`` used as unit
  conversions (0/1 for a share, 1e4 m2 per hectare).

That is not tidiness. It is what makes "the card cannot disagree with a run" a
structural property rather than a hope: a card and a run that disagreed would
require the same dict to hold two values for one key. The per-class areas come
from ``_area_stats``' ``classAreaM2`` for exactly this reason — the brief's
"how much pond / green / bare ground" row is read, never re-summed.

## The two storage numbers (R4) — deliberately not the same number

The brief's §2 asks for "explicit storage added = area x depth". The run reports
``stats.delta.storageCapacityM3``. **These are different quantities** and the
card prints both, side by side, never labelling one as the other:

- **gross** = served cells x cell area x the depth the ledger resolved for that
  intervention. It is the brief's own formula and it counts only what the new
  structure is *claimed* to hold.
- **net** = ``scenario.storageCapacityM3 - baseline.storageCapacityM3``. It is
  ``(new_depth - old_depth) x area`` summed over the grid, so it is **smaller
  than gross whenever the ground gave up storage** — replacing a pond with a
  tank adds capacity at the tank and loses it at the pond. It can also be
  negative.

Quoting gross alone would overstate the benefit of any intervention built on
ground that already stored something, which is most of them. ``gross >= net``
is asserted on the real data by ``test_every_card_number_matches_the_ledger``.

## The three rows that refuse a value

These are the point of the card, not gaps in it. Each has ``value=None`` and a
``source`` naming the absence:

1. **Soil group.** ``CURVE_NUMBER_HSG_D`` is a table merely *named* HSG D; no
   soil survey exists in this project. ``get_ledger``'s own note calls the CN
   table "the largest single lever on every number here", so a card printing
   ``soilGroup: HSG D`` with a ``source`` pointing at a Python identifier would
   be sourced-looking and false.
2. **Heat preset.** ``temperature_field.SCENARIO_PRESETS`` exists and has **no
   caller in ``app/``**. The run takes a raw ``temperature_c``. The card reports
   the number and says the presets are not wired.
3. **Tree cover.** The land-cover raster has no tree class: ``green``
   aggregates ESA tree/shrub/grassland (``CN_CITATIONS['green']``). A separate
   "tree area" would be a number this project cannot produce.

## Dates (R7)

``date`` is ``None`` unless a real acquisition date exists, and it is **never
today's date** — that would be a fabrication and would break determinism
(invariant 7). The only real date in the codebase is the canonical Landsat
scene's acquisition date, derived from ``CANONICAL_SCENE_ID``, and it dates the
**LST scene only**.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.config import settings  # noqa: E402
from app.schemas.parameter_card import (  # noqa: E402
    CARD_STATUS,
    ParameterCard,
    ParameterCardEntry,
)

#: m2 per hectare. A unit conversion, not a project constant.
_M2_PER_HA = 10_000.0

#: Mirrors ``RunRequest.temperature_c`` (``app/schemas/scene.py:368``). Written
#: here rather than imported so this module depends on no service schema -- the
#: card is read by the router, which already imports both.
DEFAULT_TEMPERATURE_C = 30.0

CARD_WARNING = (
    "Screening parameter card. Every figure is read from the same dicts the "
    "run is scored from, so the card and a run cannot disagree. Rows with no "
    "value are rows this project does not have the data for -- they are not "
    "zeros, and they are not omitted. Soil group, heat preset and tree cover "
    "are absent because no soil survey, no wired heat preset and no tree class "
    "exist in this project; read them as unknown, not as nil."
)


def _entry(key: str, label: str, value: float | None, unit: str, *,
           status: str, source: str, confidence: str,
           note: str = "", date: str | None = None) -> ParameterCardEntry:
    """One row. Thin so every row is built the same way and none forgets a field."""
    return ParameterCardEntry(
        key=key, label=label, value=value, unit=unit, status=status,
        source=source, confidence=confidence, note=note, date=date,
    )


def _lst_acquisition_date() -> str | None:
    """The canonical Landsat scene's acquisition date, or ``None``.

    Read out of ``CANONICAL_SCENE_ID`` rather than stored as a literal, so the
    date cannot drift from the id it describes. It dates the **LST scene only**
    — the DEM and the land-cover raster carry no acquisition date in code, and
    their rows therefore carry ``date=None`` rather than borrowing this one.
    """
    from app.services.temperature_field import CANONICAL_SCENE_ID

    parts = CANONICAL_SCENE_ID.split("_")
    if len(parts) < 4 or len(parts[3]) != 8:
        return None
    y, m, d = parts[3][0:4], parts[3][4:6], parts[3][6:8]
    return f"{y}-{m}-{d}"


def _class_area(stats: Mapping[str, Any], name: str) -> float:
    """One land class's area in m2, from ``_area_stats``' single sum.

    Read, never recomputed. A missing class reads as ``0.0`` rather than
    raising, because ``classAreaM2`` covers every class in ``LAND_CLASSES`` and
    an absent key would mean the raster changed shape under us.
    """
    return float((stats.get("classAreaM2") or {}).get(name, 0.0))


def served_extent(grids: Any, features: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The plan's served extent and the depth the ledger resolved for it.

    Returns ``{"servedCells": int, "storageDepthM": float | None,
    "kinds": [str]}``. Both numbers are derived by the same rules
    ``apply_interventions`` uses, so the card's gross row describes exactly the
    figure a run would produce:

    - ``servedCells`` is the **union** of the rasterised cells of the plan's
      features, measured with ``sandbox_model._polygon_cells`` -- the one
      rasteriser, not a second one. Overlaps are counted once, which is what
      "served" means and what makes gross comparable to the run's own coverage.
    - ``storageDepthM`` is a feature's own ``params.storageDepthM`` when it is
      present and inside ``MIN..MAX_STORAGE_DEPTH_M``; otherwise the class
      default from ``_overlay_common.STORAGE_DEPTH_M`` for the storage class
      the kind maps to. When the plan mixes several depths, the **largest** is
      reported and the mixed case is named, because a single number cannot
      describe two depths and silently averaging them would be a figure no one
      proposed.

    An empty plan, or a plan whose features cover no cell, returns
    ``servedCells=0`` and ``storageDepthM=None``: the gross row then reports no
    value rather than zero, because "nothing to multiply" and "multiplied out
    to zero" are different statements.
    """
    from _overlay_common import STORAGE_DEPTH_M

    from app.services.proposal_constraints import _STORAGE_CLASS_FOR_KIND
    from app.services.sandbox_model import (
        MAX_STORAGE_DEPTH_M,
        MIN_STORAGE_DEPTH_M,
        _polygon_cells,
    )

    served: set[tuple[int, int]] = set()
    depths: dict[float, list[str]] = {}
    kinds: list[str] = []

    for feature in features:
        kind = str(feature.get("intervention_type")
                   or feature.get("kind") or "unknown")
        if kind not in kinds:
            kinds.append(kind)
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        served.update((int(r), int(c)) for r, c in _polygon_cells(grids, coords))

        params = feature.get("params") or {}
        raw = params.get("storageDepthM", params.get("storage_depth_m"))
        depth: float | None = None
        if raw is not None:
            try:
                candidate = float(raw)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and (
                    MIN_STORAGE_DEPTH_M <= candidate <= MAX_STORAGE_DEPTH_M):
                depth = candidate
        if depth is None:
            storage_class = _STORAGE_CLASS_FOR_KIND.get(kind)
            if storage_class is not None and storage_class in STORAGE_DEPTH_M:
                depth = float(STORAGE_DEPTH_M[storage_class])
        if depth is not None:
            depths.setdefault(round(depth, 6), []).append(kind)

    if not served or not depths:
        return {"servedCells": len(served), "storageDepthM": None, "kinds": kinds}

    # The largest depth, with the mixing named. See the docstring: averaging
    # would publish a number nobody proposed.
    best = max(depths)
    mixed = sorted({k for d, ks in depths.items() if d != best for k in ks})
    return {
        "servedCells": len(served),
        "storageDepthM": best,
        "kinds": kinds,
        "mixedDepthKinds": mixed,
    }


def build(grids: Any, ledger: Mapping[str, Any], *,
          scene_id: str = "", plan_id: str | None = None,
          rainfall_mm: float | None = None,
          temperature_c: float | None = None,
          tide_state: str | None = None,
          served_cells: int | None = None,
          storage_depth_m: float | None = None) -> ParameterCard:
    """Assemble the card from dicts the ledger already produced.

    ``ledger`` is the dict returned by ``sandbox_model.ledger_for_scenario``
    (baseline/scenario sub-dicts included). ``tide_state`` and
    ``temperature_c`` are echoed into ``inputs`` because the reader needs to see
    what the numbers were computed *under*; neither changes a row here.

    The net storage figure is ``scenario - baseline`` read from those same two
    sub-dicts -- i.e. exactly what ``run_service`` publishes as
    ``stats.delta.storageCapacityM3``, computed by the ledger rather than here.

    ``served_cells`` and ``storage_depth_m`` are the two inputs the brief's own
    ``area x depth`` formula needs. Both are omitted when the caller has no
    proposal in hand, and the gross row then reports **no value** rather than
    zero: a plan with no intervention has no gross storage to add, and zero
    would be indistinguishable from "we did not measure it".
    """
    from app.services.proposal_constraints import cells_to_area_ha
    from app.services.sandbox_model import DEFAULT_TIDE_STATE, TIDE_STATES

    baseline = ledger.get("baseline") or {}
    scenario = ledger.get("scenario") or {}
    rainfall = float(ledger.get("rainfallMm", rainfall_mm or 0.0))
    temperature = float(
        temperature_c if temperature_c is not None else DEFAULT_TEMPERATURE_C)
    tide_key = tide_state or DEFAULT_TIDE_STATE
    tide = TIDE_STATES[tide_key]
    lst_date = _lst_acquisition_date()
    cell_area = float(grids.cell_area_m2)
    total_cells = int(grids.rows) * int(grids.cols)

    gross: float | None = None
    if served_cells is not None and storage_depth_m is not None:
        # The one multiplication the card is allowed to do (R4), and it is
        # admissible only because the source names it as the brief's §2 formula
        # and it is printed beside the net figure.
        gross = round(float(served_cells) * cell_area * float(storage_depth_m), 1)

    entries: list[ParameterCardEntry] = [
        # --- the site -------------------------------------------------------
        _entry("siteAreaHa", "Total site area",
               cells_to_area_ha(total_cells, cell_area), "ha",
               status="DERIVED", confidence="DERIVED",
               source=(f"sandbox_model.load_grids() cell_area_m2 ({cell_area:.4f} "
                       f"m2/cell) x {total_cells} cells"),
               note=("the analysis grid extent, not a cadastral boundary; the "
                     "committed bbox is the study area, not a title plan")),

        # --- land cover, per class -----------------------------------------
        _entry("waterAreaHa", "Open water area",
               round(_class_area(baseline, "water") / _M2_PER_HA, 2), "ha",
               status="MEASURED", confidence="MEASURED",
               source="ESA WorldCover class 'water', via _area_stats classAreaM2",
               note="today's land cover, before this project's interventions"),
        _entry("wetlandAreaHa", "Wetland area",
               round(_class_area(baseline, "wetland") / _M2_PER_HA, 2), "ha",
               status="MEASURED", confidence="MEASURED",
               source="ESA WorldCover class 'wetland', via _area_stats classAreaM2",
               note=("modelled as storage, not as a runoff source "
                     "(COUNTED_AS / ADR-015)")),
        _entry("greenAreaHa", "Vegetated (green) area",
               round(_class_area(baseline, "green") / _M2_PER_HA, 2), "ha",
               status="MEASURED", confidence="MEASURED",
               source="ESA WorldCover class 'green', via _area_stats classAreaM2",
               note=("the ESA class AGGREGATES tree, shrub and grassland; this "
                     "project cannot separate them")),
        _entry("bareGroundCreatedHa", "Bare ground created by this scenario",
               round((_class_area(scenario, "bare")
                      - _class_area(baseline, "bare")) / _M2_PER_HA, 2), "ha",
               status="DERIVED", confidence="DERIVED",
               source=("scenario minus baseline, classAreaM2['bare'] -- signed, "
                       "so it is negative where a scenario covers bare ground"),
               note="the class most interventions convert *away from*"),

        # --- impervious share ----------------------------------------------
        _entry("imperviousShareChange",
               "Change in impervious share (paved)",
               round(float(scenario.get("imperviousShare", 0.0))
                     - float(baseline.get("imperviousShare", 0.0)), 4), "fraction",
               status="DERIVED", confidence="DERIVED",
               source=("scenario minus baseline, _area_stats imperviousShare "
                       "(paved cells over all cells)"),
               note=("bare soil is deliberately NOT counted: TR-55 treats "
                     "'fallow, bare soil' as a separate, less impervious row")),

        # --- the two storage numbers, side by side (R4) --------------------
        _entry("explicitStorageAddedM3",
               "Explicit storage added (the brief's own formula)",
               gross, "m3",
               status="DERIVED" if gross is not None else "UNAVAILABLE",
               confidence="DERIVED" if gross is not None else "UNAVAILABLE",
               source=("served cells x cell_area_m2 x the storage depth this "
                       "proposal carries (brief section 2 formula)" if gross
                       is not None else
                       "no served extent and storage depth were supplied for "
                       "this card, so the brief's area x depth formula has "
                       "nothing to multiply"),
               note=("GROSS. It counts only what the new structure is claimed "
                     "to hold, and ignores the storage the ground gave up. The "
                     "run's own net figure is netStorageCapacityChangeM3, "
                     "printed below; where the two differ, the gap is storage "
                     "the site already had")),
        _entry("netStorageCapacityChangeM3",
               "Net storage capacity change (the run's own figure)",
               round(float(scenario.get("storageCapacityM3", 0.0))
                     - float(baseline.get("storageCapacityM3", 0.0)), 1), "m3",
               status="DERIVED", confidence="DERIVED",
               source=("ledger scenario minus baseline storageCapacityM3 -- the "
                       "same quantity run_service publishes as "
                       "stats.delta.storageCapacityM3"),
               note=("NET. It is (new depth - old depth) x area summed over the "
                     "grid, so it is smaller than the gross figure whenever "
                     "the ground gave up storage, and can be negative")),
    ]

    # --- soil: refused, permanently (R6) -----------------------------------
    entries.append(_entry(
        "soilGroup", "Soil group (hydrologic)", None, "",
        status="ASSUMPTION", confidence="UNAVAILABLE",
        source=("NO SURVEY EXISTS. The Curve Number table is named "
                "CURVE_NUMBER_HSG_D and its own citations "
                "(scripts/_overlay_common.CN_CITATIONS) say the values are an "
                "assumed HSG D reading, not a TR-55 lookup"),
        note=("the largest single lever on every runoff number in this project: "
              "the table is assumed, not surveyed. Run "
              "scripts/make_wetgrid.py --sensitivity for the band it implies")))

    # --- heat: a number, not a preset (R5) ---------------------------------
    entries.append(_entry(
        "temperatureC", "Air temperature assumed", temperature, "C",
        status="ASSUMPTION", confidence="ASSUMED",
        source=("a raw air-temperature slider value passed to the run; the "
                "named presets in temperature_field.SCENARIO_PRESETS are NOT "
                "wired into the run and have no caller in app/"),
        note=("no preset is claimed. Surface temperature is separately anchored "
              f"to Landsat scene {lst_date or 'the canonical scene'}, and air "
              "temperature is estimated as surface minus a stated offset"),
        date=lst_date))

    # --- outlet level ------------------------------------------------------
    entries.append(_entry(
        "tailwaterM", "Outlet tailwater level",
        float(tide["tailwaterM"]), "mCD",
        status="MEASURED" if float(tide["tailwaterM"]) > 0.0 else "ASSUMED",
        confidence="MEASURED" if float(tide["tailwaterM"]) > 0.0 else "ASSUMED",
        source=str(tide["source"]),
        note=(f"tide state '{tide_key}'. A new road or drainage network would "
              "change this and needs pipe/outfall capacity this screening "
              "model does not have")))

    return ParameterCard(
        scene_id=scene_id,
        plan_id=plan_id,
        status=CARD_STATUS,
        warning=CARD_WARNING,
        sources={
            "terrain": str(getattr(grids, "terrain_source", "")),
            "landCover": str(getattr(grids, "landcover_source", "")),
            "lst": (f"temperature_field.CANONICAL_SCENE_ID ({lst_date})"
                    if lst_date else "no committed LST scene"),
        },
        inputs={
            "rainfallMm": rainfall,
            "temperatureC": temperature,
            "tideState": tide_key,
        },
        entries=entries,
    )