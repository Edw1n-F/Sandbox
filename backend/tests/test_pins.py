"""Tests for the pin path of the intervention loop (P0.3).

The loop is **read -> suggest -> author -> parameterise -> pin -> run**. This
module tests the pin: the service (`app.services.pins`) that turns a suggestion
or a catalog phase into a row of the one staged plan a scene owns.

The single property that matters is **the equality property**, stated in the
module under test and asserted here rather than as a summary statistic:

    pin A and B, run; unpin B, run -- that run equals a fresh run with only A.

Everything else guards the mechanism that makes it true:

- pinning is idempotent by fingerprint, so one physical feature is one row
  (two rows would double its coverage in the guard while changing nothing);
- unpinning keeps the row and marks it ``pinned: false``, so a user can pin it
  back -- and so the run's composition (which drops ``pinned: false``) scores
  exactly the pinned subset;
- provenance round-trips untouched, because a footprint that cannot say where
  it came from is a footprint nobody can defend;
- errors are clear (``PinError``), never a crash.

An integration test drives the *actual* run composition
(`intervention_features_many`, the helper the live `/run` uses) to check the
whole pipeline honours unpin. It is separated because it currently surfaces a
gap in the composition layer, not in `pins`.

Run:  ./backend/.venv/bin/python tests/test_pins.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

sys.path.insert(0, str(BACKEND_DIR / "tests"))
from _tmpstore import isolate_store  # noqa: E402

isolate_store()

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import scenes as scenes_router  # noqa: E402
from app.services import pins, scene_service  # noqa: E402
from app.services.sandbox_model import ledger_for_scenario, load_grids  # noqa: E402
from app.schemas.scene import SceneCreate  # noqa: E402

client = TestClient(app)

# --- study-area fixtures --------------------------------------------------- #
# A frame inside the canonical 117x148 study area; the two squares below are
# disjoint and each converts real paved ground to green (verified against the
# ledger: A ~ -282 m3, B ~ -369 m3, A+B ~ -651 m3 at 450 mm -- not summation of
# two identical masks, so the composition test has something to lose).
STUDY_BBOX = {
    "type": "Polygon",
    "coordinates": [[
        [113.9922, 22.431], [113.9994, 22.431],
        [113.9994, 22.4363], [113.9922, 22.4363], [113.9922, 22.431],
    ]],
}
STUDY_CENTER = {"type": "Point", "coordinates": [113.9958, 22.4337]}
_SQ = 0.0007
A_GEOM = {
    "type": "Polygon",
    "coordinates": [[
        [113.9935, 22.4318], [113.9935 + _SQ, 22.4318],
        [113.9935 + _SQ, 22.4318 + _SQ], [113.9935, 22.4318 + _SQ],
        [113.9935, 22.4318],
    ]],
}
B_GEOM = {
    "type": "Polygon",
    "coordinates": [[
        [113.9965, 22.4340], [113.9965 + _SQ, 22.4340],
        [113.9965 + _SQ, 22.4340 + _SQ], [113.9965, 22.4340 + _SQ],
        [113.9965, 22.4340],
    ]],
}
PROVENANCE = {"source": "suggestion", "why": "drainage axis candidate",
              "cells": 200, "confidence": "screening"}


def _scene_id(name: str = "pins-test") -> str:
    return create_scene(SceneCreate(name=name, bbox=STUDY_BBOX,
                                    center=STUDY_CENTER)).scene_id


create_scene = scene_service.create_scene


def _pin_body(geometry, *, provenance=None, depth=None) -> dict:
    params: dict = {}
    if provenance is not None:
        params["provenance"] = provenance
    if depth is not None:
        params["storageDepthM"] = depth
    return {"interventionType": "green_space", "geometry": geometry,
            "params": params}


def _compose_like_run(rows: list[dict]) -> list[dict]:
    """Compose stored rows into ledger features the way the run documents it.

    Drop rows marked ``pinned: false`` and de-duplicate by id -- the exact rule
    `intervention_features_many` states in its docstring. Reimplemented here at
    the *stored-row* level because that is the only place the top-level
    ``pinned`` flag survives; the router's own helper is exercised separately in
    the integration section (where it currently does not drop the row -- a
    composition-layer gap, reported, not a `pins` behaviour).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        if row.get("pinned") is False:
            continue
        key = str(row.get("id"))
        if row.get("id") and key in seen:
            continue
        if row.get("id"):
            seen.add(key)
        out.append({
            "id": row.get("id"),
            "intervention_type": row.get("intervention_type"),
            "geometry": row.get("geometry"),
            "params": row.get("params") or {},
        })
    return out


def _row_for(rows: list[dict], feature_id: str) -> dict:
    return next(r for r in rows if str(r.get("id")) == str(feature_id))


def _staged_plan_doc(scene_id: str) -> dict:
    return db.read_doc(db.plan_path(pins.staged_plan_id(scene_id)))


# --------------------------------------------------------------------------- #
# 1. Idempotence by fingerprint
# --------------------------------------------------------------------------- #


def test_pinning_the_same_feature_twice_yields_one_row() -> None:
    scene = _scene_id()
    first = pins.pin(scene, _pin_body(A_GEOM))
    second = pins.pin(scene, _pin_body(A_GEOM))

    assert first["alreadyPinned"] is False, "first pin is new"
    assert second["alreadyPinned"] is True, "second pin is idempotent"
    assert first["featureId"] == second["featureId"], (
        "the second pin must return the row already there, not a twin")
    assert len(pins.staged_features(scene)) == 1, (
        "one physical feature must be one staged row")


def test_the_row_carries_the_pin_state_and_plan() -> None:
    scene = _scene_id()
    out = pins.pin(scene, _pin_body(A_GEOM))
    assert out["pinned"] is True
    row = _row_for(pins.staged_features(scene), out["featureId"])
    assert row["pinned"] is True
    assert row["intervention_type"] == "green_space"
    _staged = _staged_plan_doc(scene)
    assert _staged["params"]["stagedScenario"] is True, (
        "the staged plan is marked so the scene keeps one")


# --------------------------------------------------------------------------- #
# 2. Pin/unpin state -- a row survives unpin so it can be pinned back
# --------------------------------------------------------------------------- #


def test_unpin_keeps_the_row_and_marks_it_unpinned() -> None:
    scene = _scene_id()
    a = pins.pin(scene, _pin_body(A_GEOM))["featureId"]
    b = pins.pin(scene, _pin_body(B_GEOM))["featureId"]

    assert len(pins.staged_features(scene)) == 2, "both rows staged"

    pins.unpin(scene, b)
    rows = pins.staged_features(scene)
    assert len(rows) == 2, "unpin keeps the row on screen"
    assert _row_for(rows, b)["pinned"] is False, "B is un-pinned"
    assert _row_for(rows, a)["pinned"] is True, "A is untouched"

    # Pin B back: it is the same physical feature (same fingerprint), so it
    # finds the existing row rather than creating a twin.
    back = pins.pin(scene, _pin_body(B_GEOM))
    assert back["alreadyPinned"] is True and back["featureId"] == b, (
        "re-pinning an unpinned feature finds the same row")
    assert len(pins.staged_features(scene)) == 2, "still two rows, not three"
    assert _row_for(pins.staged_features(scene), b)["pinned"] is True


# --------------------------------------------------------------------------- #
# 3. The equality property -- the important one
# --------------------------------------------------------------------------- #


def test_unpin_then_run_equals_a_fresh_run_with_only_a() -> None:
    """Pin A and B, unpin B, run -- that run is byte-identical to running A.

    (c) is "only A" built from A's own stored row: the physical feature is the
    same, and re-pinning A into a fresh scene would mint a new id that appears
    in the ledger's audit list, so a byte-identical comparison requires A's
    row unchanged. The assertion is the identity of the two ledger dicts, not a
    tolerance on one field.
    """
    grids = load_grids()
    assert grids is not None, "the study-area rasters must load"

    scene = _scene_id()
    a = pins.pin(scene, _pin_body(A_GEOM))["featureId"]
    pins.pin(scene, _pin_body(B_GEOM))

    # (a) both pinned
    rows_both = pins.staged_features(scene)
    run_a = ledger_for_scenario(grids, _compose_like_run(rows_both),
                                rainfall_mm=450.0)
    assert len(run_a["interventionsApplied"]) == 2, "A and B both apply"
    assert not run_a["interventionsSkipped"], (
        f"unexpected skips: {run_a['interventionsSkipped']}")

    # (b) unpin B, then run. B is whichever staged row is not A, so the test
    # is robust to staged_features' newest-first ordering.
    pins.unpin(scene, _other_id(scene, a))
    rows_after = pins.staged_features(scene)
    assert len(rows_after) == 2, "unpin keeps both rows"
    assert _row_for(rows_after, _other_id(scene, a))["pinned"] is False, (
        "B is marked un-pinned")
    run_b = ledger_for_scenario(grids, _compose_like_run(rows_after),
                                rainfall_mm=450.0)

    # (c) fresh scenario with only A -- A's own stored row, id included
    row_a = _row_for(_staged_plan_doc(scene)["interventions"], a)
    run_c = ledger_for_scenario(grids, _compose_like_run([row_a]),
                                rainfall_mm=450.0)

    # The proof that this is meaningful: with B pinned the run is *different*.
    assert run_a != run_c, (
        "B changed nothing when pinned -- the equality test would be vacuous")
    # The property: (b) == (c) exactly, full dict.
    assert run_b == run_c, (
        "unpin-then-run != fresh-run-with-only-A; the pinned subset is not the "
        "only difference between the two runs")
    # And unpin actually moved the run away from the both-pinned case.
    assert run_a != run_b, "unpin did not change the run"


def _other_id(scene_id: str, feature_id: str) -> str:
    return next(r["id"] for r in pins.staged_features(scene_id)
                if r["id"] != feature_id)


# --------------------------------------------------------------------------- #
# 4. Error cases -- clear errors, never a crash
# --------------------------------------------------------------------------- #


def test_unpin_unknown_feature_is_a_clear_error() -> None:
    scene = _scene_id()
    pins.pin(scene, _pin_body(A_GEOM))  # something is staged
    try:
        pins.unpin(scene, "not-a-staged-feature")
    except pins.PinError as exc:
        assert "not-a-staged-feature" in str(exc), "the error names the feature"
    else:  # pragma: no cover
        raise AssertionError("unpinning an unknown feature did not raise")


def test_unpin_with_nothing_staged_is_a_clear_error() -> None:
    scene = _scene_id()  # no pins at all -> no staged plan
    try:
        pins.unpin(scene, "anything")
    except pins.PinError as exc:
        assert "staged" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("unpin on an empty scene did not raise")


def test_pin_missing_geometry_is_a_clear_error_not_a_crash() -> None:
    scene = _scene_id()
    for bad in (
        {"interventionType": "green_space"},                     # no geometry
        {"geometry": A_GEOM},                                     # no type
        {},                                                       # empty body
    ):
        try:
            pins.pin(scene, bad)
        except pins.PinError as exc:
            assert "geometry" in str(exc) or "type" in str(exc), (
                f"the error should say what is missing: {exc}")
        else:  # pragma: no cover
            raise AssertionError(f"a malformed body was accepted: {bad}")


def test_pin_with_an_invalid_geometry_is_a_clear_error() -> None:
    scene = _scene_id()
    bad_type = {"type": "Triangle", "coordinates": [[[0, 0], [1, 0], [1, 1]]]}
    try:
        pins.pin(scene, {"interventionType": "green_space",
                         "geometry": bad_type})
    except pins.PinError as exc:
        assert "geometry" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("an unsupported geometry type was accepted")


# --------------------------------------------------------------------------- #
# 5. Provenance round-trip
# --------------------------------------------------------------------------- #


def test_provenance_survives_pin_stage_and_ledger() -> None:
    scene = _scene_id()
    out = pins.pin(scene, _pin_body(A_GEOM, provenance=PROVENANCE))
    row = _row_for(pins.staged_features(scene), out["featureId"])
    assert row["params"]["provenance"] == PROVENANCE, (
        "provenance must survive pin -> staged_features")

    composed = _compose_like_run([row])
    assert composed[0]["params"]["provenance"] == PROVENANCE, (
        "provenance must survive composition into the ledger's params")

    grids = load_grids()
    result = ledger_for_scenario(grids, composed, rainfall_mm=450.0)
    assert result["interventionsApplied"], "the feature must apply"
    # The ledger carries the row's declared basis through from params.
    assert result["interventionsApplied"][0]["id"] == out["featureId"]


# --------------------------------------------------------------------------- #
# 6. Fingerprint semantics
# --------------------------------------------------------------------------- #


def test_provenance_is_not_part_of_a_features_identity() -> None:
    """Two pins that differ only in provenance are the same physical feature.

    DOCUMENTED FINDING: provenance is excluded from the fingerprint, so a
    suggestion and a catalog phase naming the same cells collapse to one row
    (two rows would double the coverage in the guard for one mask).
    """
    scene = _scene_id()
    first = pins.pin(scene, _pin_body(A_GEOM, provenance={"why": "from suggest"}))
    second = pins.pin(scene, _pin_body(A_GEOM, provenance={"why": "from catalog"}))
    assert second["alreadyPinned"] is True
    assert first["featureId"] == second["featureId"]
    assert len(pins.staged_features(scene)) == 1, (
        "provenance is not identity: the same footprint is one row")


def test_storage_depth_is_part_of_a_features_identity() -> None:
    """Two pins that differ in storageDepthM are different features.

    DOCUMENTED FINDING: storageDepthM (and curveNumber) DO enter the
    fingerprint, because a tank's usable depth changes what the ledger does --
    a 0.5 m and a 1.5 m detention feature on the same footprint are not the
    same intervention.
    """
    scene = _scene_id()
    shallow = pins.pin(scene, _pin_body(A_GEOM, depth=0.5))
    deep = pins.pin(scene, _pin_body(A_GEOM, depth=1.5))
    assert deep["alreadyPinned"] is False, (
        "a different storageDepthM is a different feature")
    assert shallow["featureId"] != deep["featureId"]
    assert len(pins.staged_features(scene)) == 2, (
        "different depths are two rows, so their coverage does not collapse")


# --------------------------------------------------------------------------- #
# 7. Persistence -- the state on disk is the state the service reports
# --------------------------------------------------------------------------- #


def test_pin_and_unpin_state_persists_to_the_plan_doc() -> None:
    scene = _scene_id()
    a = pins.pin(scene, _pin_body(A_GEOM))["featureId"]
    b = pins.pin(scene, _pin_body(B_GEOM))["featureId"]
    plan_id = pins.staged_plan_id(scene)

    doc = db.read_doc(db.plan_path(plan_id))  # straight off disk, not cached
    assert doc is not None and doc["params"]["stagedScenario"] is True
    flags = {r["id"]: r.get("pinned") for r in doc["interventions"]}
    assert flags[a] is True and flags[b] is True

    pins.unpin(scene, b)
    doc = db.read_doc(db.plan_path(plan_id))
    flags = {r["id"]: r.get("pinned") for r in doc["interventions"]}
    assert flags[a] is True and flags[b] is False, (
        "the un-pin is durable in the plan doc on disk")
    # The intervention's own document agrees with the plan's copy.
    iv_doc = db.read_doc(db.intervention_path(b))
    assert iv_doc is not None and iv_doc.get("pinned") is False, (
        "the standalone intervention doc must not disagree with the plan")

    # And the derived view matches what is on disk.
    rows = pins.staged_features(scene)
    assert _row_for(rows, b)["pinned"] is False


# --------------------------------------------------------------------------- #
# 8. The HTTP routes (wired by the engineer into scenes.py)
# --------------------------------------------------------------------------- #


def test_endpoint_pin_get_and_unpin_round_trip() -> None:
    scene = _scene_id()
    r = client.post(f"/api/scenes/{scene}/pins", json=_pin_body(A_GEOM))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alreadyPinned"] is False and body["featureId"]

    listed = client.get(f"/api/scenes/{scene}/pins")
    assert listed.status_code == 200, listed.text
    doc = listed.json()
    assert doc["status"] == "STAGED" and len(doc["features"]) == 1
    assert doc["features"][0]["pinned"] is True

    u = client.post(f"/api/scenes/{scene}/pins/{body['featureId']}/unpin")
    assert u.status_code == 200, u.text
    assert u.json()["pinned"] is False
    # The unpinned row is still served so it can be pinned back.
    again = client.get(f"/api/scenes/{scene}/pins").json()
    assert len(again["features"]) == 1 and again["features"][0]["pinned"] is False
    assert again["status"] == "ALL_UNPINNED"


def test_endpoint_pin_is_idempotent_by_fingerprint() -> None:
    scene = _scene_id()
    client.post(f"/api/scenes/{scene}/pins", json=_pin_body(A_GEOM))
    second = client.post(f"/api/scenes/{scene}/pins", json=_pin_body(A_GEOM))
    assert second.status_code == 200, second.text
    assert second.json()["alreadyPinned"] is True
    assert len(client.get(f"/api/scenes/{scene}/pins").json()["features"]) == 1


def test_endpoint_pin_missing_geometry_is_422_not_500() -> None:
    scene = _scene_id()
    r = client.post(f"/api/scenes/{scene}/pins", json={"interventionType": "green_space"})
    assert r.status_code == 422, r.text
    assert "geometry" in r.text


def test_endpoint_unpin_unknown_feature_is_404() -> None:
    scene = _scene_id()
    client.post(f"/api/scenes/{scene}/pins", json=_pin_body(A_GEOM))
    r = client.post(f"/api/scenes/{scene}/pins/nope/unpin")
    assert r.status_code == 404, r.text


def test_endpoint_pins_on_unknown_scene_is_404() -> None:
    r = client.get("/api/scenes/not-a-scene/pins")
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# Integration: the RUN composition must honour unpin
#
# This drives the same helper the live `POST /run` composes with
# (`intervention_features_many`). It is kept apart because, at authoring time,
# it exposes a GAP IN THE COMPOSITION LAYER, not in `app.services.pins`: pins
# writes `pinned: false` at the top level of the stored row (verified in the
# persistence test), but `scene_service.list_interventions` rebuilds the wire
# feature without that flag, so `intervention_features` falls back to its
# `True` default and the un-pinned row is NOT dropped. See the test report.
# --------------------------------------------------------------------------- #


def test_run_composition_drops_unpinned_rows() -> None:
    scene = _scene_id()
    a = pins.pin(scene, _pin_body(A_GEOM))["featureId"]
    pins.pin(scene, _pin_body(B_GEOM))
    pins.unpin(scene, _other_id(scene, a))
    plan_id = pins.staged_plan_id(scene)

    composed = scenes_router.intervention_features_many([plan_id])
    ids = [f["id"] for f in composed]
    assert ids == [a], (
        f"run composition kept {len(composed)} features after unpin "
        f"(expected exactly [{a}]); it did not drop the pinned:false row -- "
        "the run would still score the un-pinned feature")


# --------------------------------------------------------------------------- #
# Minimal runner (pytest is not installed in this environment)
# --------------------------------------------------------------------------- #


def _run() -> int:
    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
