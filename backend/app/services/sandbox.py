"""The sandbox: what the user has put on the study area between runs (Stage 4).

The loop this module serves:

1. A run finishes and its card offers **three grounded suggestions** -- the
   strongest candidate site on each of the drainage, heat and runoff axes, read
   from the rasters by ``suggest.py``. Nothing about them is templated: a
   suggestion is a real footprint with the estimate it would produce.
2. Or the user writes an intervention in natural language, which goes through
   ``ai_plan`` exactly as before.
3. Either way the user then sees an **input card**: the values the run will
   read for that kind, which they can set themselves or leave to the AI's
   estimate. Which of the two it was is recorded on the feature, so a guessed
   number never reads as a supplied one.
4. Confirming **pins** the feature into the scene's staged plan -- the one place
   interventions live -- and the Interventions panel lists it from there.
5. A removal is resolved against what is actually on the sandbox: a staged
   intervention is unpinned, and a planned project is toggled off. Nothing else
   (existing buildings, today's land cover) is removable.

Run again and the staged plan is composed with the sliders and the toggled
projects, as every run already does.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

__all__ = [
    "InputError",
    "apply_inputs",
    "inputs_for",
    "modify",
    "resolve_modification",
    "resolve_removal",
    "run_suggestions",
    "stage",
]


class InputError(ValueError):
    """A value the user supplied that the run would refuse."""


# --------------------------------------------------------------------------- #
# The input card
# --------------------------------------------------------------------------- #

#: The kinds whose stored ``storageDepthM`` the ledger reads, and the storage
#: class whose default stands in when nobody supplies one.
_DEPTH_KINDS: dict[str, str] = {
    "water_reservoir": "water",
    "flood_storage": "floodable",
    "underground_tank": "tank",
}

_DEPTH_LABEL: dict[str, str] = {
    "water_reservoir": "Usable storage depth of the reservoir",
    "flood_storage": "Designed ponding depth of the floodable ground",
    "underground_tank": "Working depth of the tank",
}


def _server_issued(params: dict[str, Any]) -> bool:
    """Was this feature's depth produced by the server, or typed by the client?

    Only a feature the server itself built carries a ``provenance.source`` it
    wrote (``suggestion`` for a candidate read from the wet grid). A
    ``/ai-plan`` proposal carries no depth at all -- the number lives in the
    response's ``parameters`` display block, never in ``params`` -- so a depth
    that arrives on an apply request was typed by whoever sent it.

    This distinction is the whole point: a server-measured depth may be
    described as measured, and a client's may not.
    """
    provenance = params.get("provenance")
    return isinstance(provenance, dict) and provenance.get("source") == "suggestion"


def _client_typed_depths(feature: dict[str, Any]) -> dict[str, Any]:
    """The depth values a request supplied for one feature, as *its* values.

    Returned in the shape ``apply_inputs`` reads as ``values``, so a hand-set
    depth is recorded ``user`` in ``params.inputSources`` and the served number
    and its provenance label agree. A server-issued feature keeps its own depth
    as the AI estimate instead, because there it really was measured.

    A client value outside the ledger's range is left for ``apply_inputs`` to
    refuse with a 422, rather than being dropped: a number silently replaced by
    the default is the disclosure hole this function exists to close.
    """
    if _server_issued(feature.get("params") or {}):
        return {}
    params = feature.get("params") or {}
    out: dict[str, Any] = {}
    for key in ("storageDepthM", "storage_depth_m"):
        if params.get(key) is not None:
            out["storageDepthM"] = params[key]
    return out


def _without_client_depths(feature: dict[str, Any]) -> dict[str, Any]:
    """The feature with any client-supplied depth removed from ``params``.

    ``apply_inputs`` writes the depth back from ``values`` (labelled ``user``)
    or from the class default (labelled ``ai_estimate``). Leaving the client's
    number in place would have ``inputs_for`` describe it as "measured for this
    site", which is false for a number this server never measured.
    """
    if _server_issued(feature.get("params") or {}):
        return feature
    params = {k: v for k, v in (feature.get("params") or {}).items()
              if k not in ("storageDepthM", "storage_depth_m", "inputSources")}
    return {**feature, "params": params}


def inputs_for(kind: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The values the run reads for ``kind``, with the AI's estimate for each.

    Only fields the ledger **reads** are offered. A curve number for permeable
    paving looks like a lever but ``apply_interventions`` applies a fixed class
    effect and ignores a supplied one, so offering it would be a control that
    does nothing. A kind with no readable value gets an empty list and the card
    says so instead of inventing a question.

    The estimate is the feature's own value when it carries one (a suggestion
    measured from the wet grid, say), else the storage-class default.
    """
    from _overlay_common import STORAGE_DEPTH_M
    from app.services.sandbox_model import MAX_STORAGE_DEPTH_M, MIN_STORAGE_DEPTH_M

    storage_class = _DEPTH_KINDS.get(kind)
    if storage_class is None:
        return []

    params = params or {}
    own = params.get("storageDepthM", params.get("storage_depth_m"))
    origin = (params.get("inputSources") or {}).get("storageDepthM")
    if own is not None and origin == "user":
        # A placed row the planner set by hand: the estimate offered back is
        # their own number, and it says so.
        estimate = float(own)
        basis = "the value you entered"
    elif own is not None and (_server_issued(params) or origin is None):
        estimate = float(own)
        basis = "measured for this site (the wet grid's depth, or the candidate's own value)"
    elif own is not None:
        # A placed row whose depth was the AI's estimate. Calling that
        # "measured" was false: apply_inputs writes the class default into
        # params, so a re-opened card must describe it as the default it is.
        estimate = float(own)
        basis = (f"the '{storage_class}' storage-class default, an assumption, "
                 f"not a survey")
    else:
        estimate = float(STORAGE_DEPTH_M[storage_class])
        basis = f"the '{storage_class}' storage-class default, an assumption, not a survey"
    return [{
        "key": "storageDepthM",
        "label": _DEPTH_LABEL[kind],
        "unit": "m",
        "min": MIN_STORAGE_DEPTH_M,
        "max": MAX_STORAGE_DEPTH_M,
        "estimate": round(estimate, 2),
        "estimateBasis": basis,
    }]


def apply_inputs(kind: str, params: dict[str, Any] | None,
                 values: dict[str, Any] | None) -> dict[str, Any]:
    """``params`` with the input card's answers written in, and their origin.

    ``values`` maps an input key to a number the user typed. A key left out is
    one the user handed to the AI, and the AI's estimate is written in its
    place. ``params.inputSources`` records which was which -- ``user`` or
    ``ai_estimate`` -- per key, so the Interventions panel and the run can say
    whether a number was supplied or guessed.

    Raises :class:`InputError` for a value the ledger would refuse, so the user
    is told at the card rather than finding the feature silently skipped by the
    next run.
    """
    out = dict(params or {})
    values = values or {}
    sources: dict[str, str] = dict(out.get("inputSources") or {})
    for spec in inputs_for(kind, out):
        key = spec["key"]
        raw = values.get(key)
        if raw is None or raw == "":
            out[key] = spec["estimate"]
            sources[key] = "ai_estimate"
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise InputError(f"{spec['label']} must be a number, got {raw!r}") from exc
        if not spec["min"] <= value <= spec["max"]:
            raise InputError(
                f"{spec['label']} must be between {spec['min']} and "
                f"{spec['max']} {spec['unit']}, got {value}")
        out[key] = value
        sources[key] = "user"
    if sources:
        out["inputSources"] = sources
    return out


# --------------------------------------------------------------------------- #
# Route 1: the three suggestions under a run's card
# --------------------------------------------------------------------------- #

#: Plain words for a kind, so a suggestion reads as a sentence and not a key.
KIND_LABEL: dict[str, str] = {
    "water_reservoir": "water reservoir",
    "underground_tank": "underground tank",
    "flood_storage": "floodable ground",
    "green_space": "green space",
    "permeable_paving": "permeable paving",
}


@lru_cache(maxsize=16)
def _ranked(rainfall_key: float, stamp: tuple[float, ...]) -> dict[str, Any]:
    """``suggest.suggest`` for one storm, memoised on the rasters' mtimes.

    The suggester takes 7-15 s, which a run cannot wait for on every press.
    Memoised on the storm *and* on ``siting``'s raster stamp, so a regenerated
    wet grid is a new key rather than a stale answer.
    """
    _ = stamp
    from app.services import suggest

    return suggest.suggest(rainfall_mm=rainfall_key, max_per_axis=6)


def _cells_of(grids: Any, geometry: dict[str, Any] | None) -> set[tuple[int, int]]:
    from app.services.sandbox_model import _polygon_cells

    coords = (geometry or {}).get("coordinates")
    if not coords:
        return set()
    return {(int(r), int(c)) for r, c in _polygon_cells(grids, coords)}


def run_suggestions(scene_id: str, rainfall_mm: float,
                    plan_ids: list[str] | None = None) -> dict[str, Any]:
    """Up to three suggestions for the run just made: the best site per axis.

    Drawn from ``suggest.suggest`` -- real footprints read from the land cover,
    the wet grid and the building footprints -- never from a fixed list. A
    candidate that overlaps anything already on the sandbox (the staged plan,
    or a toggled project) is skipped for the next one on its axis, so the card
    never suggests ground that is already taken.

    Each suggestion carries the feature verbatim and its input card, so a click
    goes straight to the card without re-deriving the geometry from words.
    """
    from app.services import pins, siting
    from app.services.proposal_constraints import claimed_cells
    from app.services.sandbox_model import load_grids

    grids = load_grids()
    if grids is None:
        return {"status": "NO_LEDGER_GRID", "suggestions": [],
                "warning": "no wet grid: run scripts/make_wetgrid.py"}

    ranked = _ranked(round(float(rainfall_mm), 0), siting._stamp())
    taken_plans = [p for p in (plan_ids or []) if p]
    staged = pins.staged_plan_id(scene_id)
    if staged and staged not in taken_plans:
        taken_plans.append(staged)
    taken = claimed_cells(grids, taken_plans)

    out: list[dict[str, Any]] = []
    for axis in ranked.get("axes", []):
        for candidate in axis.get("candidates", []):
            feature = candidate["plan"]["interventions"][0]
            if taken and _cells_of(grids, feature["geometry"]) & taken:
                continue
            kind = candidate["kind"]
            label = KIND_LABEL.get(kind, kind.replace("_", " "))
            out.append({
                "axis": axis["axis"],
                "kind": kind,
                "label": f"Add {label} ({candidate['site']['areaHa']} ha)",
                "evidence": candidate["site"]["evidence"],
                "estimate": candidate["estimate"],
                "feature": {
                    "geometry": feature["geometry"],
                    "interventionType": kind,
                    "params": {**(feature.get("params") or {}),
                               "provenance": {"source": "suggestion",
                                              "axis": axis["axis"],
                                              "evidence": candidate["site"]["evidence"]}},
                    "style": feature.get("style") or {},
                },
                "inputs": inputs_for(kind, feature.get("params")),
            })
            break
    return {
        "status": ranked.get("status", "SITES_FROM_MEASURED_RASTERS"),
        "warning": ranked.get("warning", ""),
        "rainfallMm": round(float(rainfall_mm), 1),
        "suggestions": out,
    }


# --------------------------------------------------------------------------- #
# Confirming: the feature joins the sandbox
# --------------------------------------------------------------------------- #


def stage(scene_id: str, features: list[dict[str, Any]],
          values: list[dict[str, Any] | None] | None = None,
          source: str = "manual") -> dict[str, Any]:
    """Pin confirmed features into the staged plan, with their input answers.

    ``values[i]`` is the input card's answers for ``features[i]`` (absent keys
    go to the AI estimate). Every feature goes through ``pins.pin``, so the
    staged plan stays idempotent by fingerprint and the 25%/40% caps stay the
    ledger's.

    A depth already sitting in a feature's ``params`` is treated as the sender's
    own value unless the server issued it (``_client_typed_depths``), so a
    hand-set number is stored labelled ``user`` and never described as measured.

    Validated as a batch before anything is written: one bad depth refuses the
    whole confirmation rather than leaving half of it on the sandbox.
    """
    from app.services import pins

    if not features:
        raise InputError("nothing to add")
    values = list(values or [])
    prepared: list[dict[str, Any]] = []
    for i, feature in enumerate(features):
        kind = feature.get("interventionType") or feature.get("intervention_type")
        if not kind:
            raise InputError("a feature needs an interventionType")
        # The card's answers win; a depth the request typed into `params` is the
        # sender's own value and joins them, so neither is read as a measurement.
        answers = dict(_client_typed_depths(feature))
        answers.update(values[i] if i < len(values) else None or {})
        params = apply_inputs(kind, _without_client_depths(feature).get("params"),
                              answers)
        prepared.append({
            "geometry": feature["geometry"],
            "interventionType": kind,
            "source": feature.get("source") or source,
            "params": params,
            "style": feature.get("style") or {},
        })

    results = [pins.pin(scene_id, p) for p in prepared]
    return {
        "planId": results[0]["planId"],
        "features": len(results),
        "featureIds": [r["featureId"] for r in results],
        "alreadyPinned": [r["featureId"] for r in results if r["alreadyPinned"]],
        "types": sorted({p["interventionType"] for p in prepared}),
    }


# --------------------------------------------------------------------------- #
# Removal: resolved against what is actually on the sandbox
# --------------------------------------------------------------------------- #

#: Words that name no particular project ("the HSK MTR station" -> mtr, station).
_NAME_STOPWORDS = {"hsk", "the", "a", "an", "of", "and", "project", "new"}


def _name_tokens(label: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(label or "").lower())
    return {w for w in words if w not in _NAME_STOPWORDS and len(w) > 2}


def _in_region(geometry: dict[str, Any] | None, region: str | None,
               bounds: tuple[float, float, float, float]) -> bool:
    """Whether a feature's centroid falls in a named region of the study area."""
    if not region or region == "all":
        return True
    from app.services.ai_plan import REGION_FRACTIONS
    from app.services.intervention_geometry import ring_centroid

    fr = REGION_FRACTIONS.get(region)
    coords = (geometry or {}).get("coordinates") or []
    if fr is None or not coords:
        return True
    lon, lat = ring_centroid(coords[0])
    x0, y0, x1, y1 = bounds
    return (x0 + (x1 - x0) * fr[0] <= lon <= x0 + (x1 - x0) * fr[2]
            and y0 + (y1 - y0) * fr[1] <= lat <= y0 + (y1 - y0) * fr[3])


def resolve_removal(scene_id: str, instruction: str) -> dict[str, Any]:
    """What on the sandbox a removal instruction refers to.

    Only two things are removable, and they are removed differently:

    * a **staged intervention** -- unpinned, so it leaves the Interventions
      panel and the next run;
    * a **planned project** -- toggled off; its seeded plan is never deleted.

    Existing buildings and today's land cover are not on the sandbox, so they
    never match. Every match is returned rather than the "best" one: "remove
    the park" can mean HSK Park or a green space the user added, and choosing
    for them would be a guess. The client asks which.
    """
    from app.services import pins, scene_service
    from app.services.ai_plan import _match_region
    from app.services.sandbox_model import load_grids

    from app.agent.intent import _match_noun

    text = str(instruction or "")
    lowered = text.lower()
    noun = _match_noun(text)
    kind = noun[1] if noun else None
    region = _match_region(text)
    grids = load_grids()
    bounds = grids.bounds if grids is not None else (0.0, 0.0, 1.0, 1.0)

    matches: list[dict[str, Any]] = []

    listed = scene_service.list_plans(scene_id)
    for plan in getattr(listed, "plans", listed) or []:
        params = getattr(plan, "params", None) or {}
        project_id = params.get("projectId")
        if not project_id:
            continue
        tokens = _name_tokens(getattr(plan, "label", "") or project_id)
        if tokens & set(re.findall(r"[a-z0-9]+", lowered)):
            matches.append({
                "type": "project",
                "planId": plan.plan_id,
                "projectId": project_id,
                "label": plan.label or project_id,
                "action": "toggle_off",
            })

    for row in pins.staged_features(scene_id):
        if not row.get("pinned"):
            continue
        row_kind = row.get("intervention_type")
        if kind and row_kind != kind:
            continue
        if not kind and not region:
            continue
        if not _in_region(row.get("geometry"), region, bounds):
            continue
        label = KIND_LABEL.get(row_kind, str(row_kind).replace("_", " "))
        matches.append({
            "type": "intervention",
            "featureId": row.get("id"),
            "interventionType": row_kind,
            "label": label,
            "action": "unpin",
        })

    if not matches:
        message = ("Nothing on the sandbox matches that. Only interventions you "
                   "have added and planned projects can be removed; existing "
                   "buildings and today's land cover cannot.")
    elif len(matches) == 1:
        message = None
    else:
        message = f"{len(matches)} things on the sandbox match. Choose which to remove."
    return {"matches": matches, "message": message}


# --------------------------------------------------------------------------- #
# Modification: change the values of something already placed
# --------------------------------------------------------------------------- #

#: Verbs that ask to change something already on the sandbox, not to add it.
MODIFY_VERBS: tuple[str, ...] = (
    "change", "modify", "edit", "adjust", "update", "set", "make",
    "deepen", "deeper", "shallower", "resize",
)
_MODIFY_RE = re.compile(r"\b(" + "|".join(MODIFY_VERBS) + r")\b", re.IGNORECASE)
_NUMBER_M_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)\b", re.IGNORECASE)


def resolve_modification(scene_id: str, instruction: str) -> dict[str, Any]:
    """Which placed intervention a change instruction refers to, and its card.

    Only a **placed intervention** can be modified: a planned project's values
    are its project file's, not the user's. A depth named in the sentence
    ("make the reservoir 3 m deep") is offered as the user's own value; anything
    unnamed stays on the estimate the feature already carries.

    Returns ``{"isModification": False}`` when the sentence is not a change to
    something placed, so the caller falls through to adding.
    """
    from app.services import pins
    from app.services.ai_plan import _match_region
    from app.services.sandbox_model import load_grids

    from app.agent.intent import _match_noun

    text = str(instruction or "")
    noun = _match_noun(text)
    if not _MODIFY_RE.search(text) or noun is None:
        return {"isModification": False}
    kind = noun[1]
    region = _match_region(text)
    grids = load_grids()
    bounds = grids.bounds if grids is not None else (0.0, 0.0, 1.0, 1.0)

    targets = []
    for row in pins.staged_features(scene_id):
        if not row.get("pinned") or row.get("intervention_type") != kind:
            continue
        if not _in_region(row.get("geometry"), region, bounds):
            continue
        targets.append(row)
    if not targets:
        return {"isModification": False}

    named = _NUMBER_M_RE.search(text)
    out = []
    for row in targets:
        params = row.get("params") or {}
        out.append({
            "featureId": row.get("id"),
            "interventionType": kind,
            "label": KIND_LABEL.get(kind, kind.replace("_", " ")),
            "inputs": inputs_for(kind, params),
            "suggestedValues": ({"storageDepthM": float(named.group(1))}
                                if named and kind in _DEPTH_KINDS else {}),
        })
    return {"isModification": True, "targets": out}


def modify(scene_id: str, feature_id: str,
           values: dict[str, Any] | None) -> dict[str, Any]:
    """Replace one placed intervention's values, keeping its footprint.

    The new values are validated first; only then is the old row unpinned and
    the changed feature pinned in its place, so a refused value leaves the
    sandbox exactly as it was. The footprint, kind, style and provenance carry
    over -- a modification changes what the run reads, not where it is.
    """
    from app.services import pins

    row = next((r for r in pins.staged_features(scene_id)
                if str(r.get("id")) == str(feature_id) and r.get("pinned")), None)
    if row is None:
        raise InputError("that intervention is not on the sandbox")
    kind = row.get("intervention_type")
    params = {k: v for k, v in (row.get("params") or {}).items()
              if k not in ("fingerprint", "inputSources")}
    # Start from what the feature carries: a key the user leaves out keeps its
    # current value rather than falling back to the class default.
    keep = {k: params[k] for k in ("storageDepthM",) if k in params}
    merged = {**keep, **{k: v for k, v in (values or {}).items() if v not in (None, "")}}
    new_params = apply_inputs(kind, params, merged)
    # A value carried over keeps the origin it had; only what the user typed
    # now becomes theirs.
    previous = (row.get("params") or {}).get("inputSources") or {}
    typed = {k for k, v in (values or {}).items() if v not in (None, "")}
    for key in new_params.get("inputSources", {}):
        if key not in typed:
            new_params["inputSources"][key] = previous.get(key, "ai_estimate")

    pins.unpin(scene_id, feature_id)
    result = pins.pin(scene_id, {
        "geometry": row["geometry"],
        "interventionType": kind,
        "source": row.get("source") or "manual",
        "params": new_params,
        "style": row.get("style") or {},
    })
    return {"featureId": result["featureId"], "planId": result["planId"],
            "replaced": feature_id}
