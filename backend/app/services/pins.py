"""The staged scenario: what the user has pinned (P0.3 of the loop plan).

The loop is **read -> suggest -> author -> parameterise -> pin -> run**. This
module is the pin: the place a suggestion or a catalog phase becomes part of the
scenario the next run scores.

## One staged plan per scene

Pins are kept in a single plan whose ``params.stagedScenario`` is true, so:

- the run needs no new composition rule -- the staged plan is added to the
  plan-id list the run already takes, and its features compose like any
  others;
- the coverage caps (25% one intervention, 40% cumulative) are the *ledger's*
  and already apply to whatever list it is handed, so pinning is subject to the
  same arithmetic as everything else rather than a parallel secret budget.

## Idempotence is the requirement

Pinning the same footprint twice must not put it in the scenario twice. Two
copies of one polygon would double its coverage in the guard while changing
nothing on the grid -- a silent way to trip the cumulative cap with a single
physical feature. So a pin is identified by a hash of what it *is* (kind,
geometry, parameters) and a repeat returns the feature that is already there.

Unpinning keeps the row and marks it ``pinned: false``. Removing a feature from
the study area entirely is a different operation (the ``remove`` op, P1.3); this
is "not in this run", which is a different statement and one the user can undo
by pinning again.

## What is guaranteed

Pin A and B, run; unpin B, run; that run equals a fresh run with only A pinned.
The equality is the property, and ``tests/test_pins.py`` asserts it rather than
asserting a summary statistic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app import db


STAGED_KEY = "stagedScenario"


class PinError(RuntimeError):
    """A pin request that cannot be honoured."""


def _plan_path_if_staged(plan_id: str) -> bool:
    doc = db.read_doc(db.plan_path(plan_id))
    if doc is None:
        return False
    return bool((doc.get("params") or {}).get(STAGED_KEY))


def staged_plan_id(scene_id: str) -> str | None:
    """The scene's staged plan, or None if it does not exist yet."""
    for plan in _plans(scene_id):
        if (plan.get("params") or {}).get(STAGED_KEY):
            return str(plan.get("id") or plan.get("plan_id"))
    return None


def _plans(scene_id: str) -> list[dict[str, Any]]:
    from app.services import scene_service

    listed = scene_service.list_plans(scene_id)
    plans = getattr(listed, "plans", listed) or []
    return [p if isinstance(p, dict) else p.model_dump(by_alias=False)
            for p in plans]


def _ensure_staged_plan(scene_id: str) -> dict[str, Any]:
    """Find or create the scene's staged plan."""
    existing = staged_plan_id(scene_id)
    if existing:
        doc = db.read_doc(db.plan_path(existing))
        if doc is not None:
            return doc

    from app.services import scene_service
    from app.schemas.plan import PlanCreate

    created = scene_service.create_plan(scene_id, PlanCreate(
        plan_type="custom",
        label="Staged scenario",
        source="manual",
        params={STAGED_KEY: True, "note": "pinned interventions for the next run"},
    ))
    return db.read_doc(db.plan_path(created.plan_id))


def fingerprint(kind: str, geometry: dict[str, Any],
                params: dict[str, Any]) -> str:
    """A stable identity for a physical feature.

    The pin state is *not* part of the fingerprint: unpinning and re-pinning the
    same footprint must find the same row, not create a twin. Neither is the
    provenance: a suggestion and a catalog phase that describe the same cells
    are the same ground, and two rows for it would double its coverage.
    """
    payload = {
        "kind": str(kind),
        "geometry": geometry,
        # Only the parameters that change what the ledger does.
        "params": {k: v for k, v in (params or {}).items()
                   if k in ("storageDepthM", "storage_depth_m", "curveNumber",
                            "curve_number")},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _feature_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise one pin request.

    The geometry is validated by the same schema as any other intervention
    (``InterventionIn``), so a pin cannot introduce a shape the rest of the
    pipeline would refuse -- and the run would then silently ignore it.
    """
    from app.schemas.intervention import InterventionIn

    try:
        item = InterventionIn(
            geometry=payload["geometry"],
            intervention_type=payload["interventionType"],
            source=payload.get("source", "manual"),
            params=payload.get("params") or {},
            style=payload.get("style") or {},
        )
    except KeyError as exc:
        raise PinError(f"a pin needs a geometry and a type (missing {exc})") from exc
    except Exception as exc:  # noqa: BLE001 - pydantic's message is the useful one
        raise PinError(str(exc)) from exc

    return {
        "geometry": item.geometry,
        "intervention_type": item.intervention_type,
        "source": item.source,
        # Provenance is carried in params because the stored row has nowhere
        # else to put it, and a footprint that cannot say where it came from is
        # a footprint nobody can defend.
        "params": dict(item.params),
        "style": dict(item.style),
    }


def pin(scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Pin one feature. Idempotent: the same physical feature pins once."""
    feature = _feature_from_request(payload)
    plan = _ensure_staged_plan(scene_id)
    rows: list[dict[str, Any]] = plan.get("interventions") or []
    mark = fingerprint(feature["intervention_type"], feature["geometry"],
                       feature["params"])

    for row in rows:
        if (row.get("params") or {}).get("fingerprint") == mark:
            row["pinned"] = True
            _write_plan(plan, rows)
            return {"featureId": row["id"], "planId": plan["id"],
                    "pinned": True, "alreadyPinned": True}

    feature["params"] = {**feature["params"], "fingerprint": mark}
    record = {
        "id": db.new_id(),
        "plan_id": plan["id"],
        "geometry_type": "Polygon" if feature["geometry"].get("type") == "Polygon" else feature["geometry"].get("type"),
        "geometry": feature["geometry"],
        "intervention_type": feature["intervention_type"],
        "source": feature["source"],
        "params": feature["params"],
        "style": feature["style"],
        "status": "draft",
        "pinned": True,
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
    }
    db.write_doc(db.intervention_path(record["id"]), record)
    rows.append(record)
    _write_plan(plan, rows)
    return {"featureId": record["id"], "planId": plan["id"],
            "pinned": True, "alreadyPinned": False}


def unpin(scene_id: str, feature_id: str) -> dict[str, Any]:
    """Take one feature out of the next run. The row stays, marked unpinned."""
    plan_id = staged_plan_id(scene_id)
    if plan_id is None:
        raise PinError("nothing is staged for this scene")
    plan = db.read_doc(db.plan_path(plan_id))
    rows: list[dict[str, Any]] = plan.get("interventions") or []
    for row in rows:
        if str(row.get("id")) == str(feature_id):
            row["pinned"] = False
            db.write_doc(db.intervention_path(row["id"]),
                         {**row, "pinned": False})
            _write_plan(plan, rows)
            return {"featureId": row["id"], "pinned": False}
    raise PinError(f"no staged feature {feature_id!r} in this scene")


def staged_features(scene_id: str) -> list[dict[str, Any]]:
    """Every row of the staged plan, pinned or not, newest first.

    The unpinned rows are returned too, because a user who just unpinned
    something needs to see it to pin it back.
    """
    plan_id = staged_plan_id(scene_id)
    if plan_id is None:
        return []
    plan = db.read_doc(db.plan_path(plan_id))
    rows = list(plan.get("interventions") or [])
    return list(reversed(rows))


def _write_plan(plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    from app.services import scene_service

    plan = dict(plan)
    plan["interventions"] = rows
    db.write_doc(db.plan_path(plan["id"]), plan)
    # The store's copy is the document; keep the derived view honest too.
    try:
        scene_service.list_plans.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - cache clearing is best-effort
        pass
