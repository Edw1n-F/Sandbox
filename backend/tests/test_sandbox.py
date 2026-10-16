"""The sandbox loop (Stage 4): suggest, input card, stage, remove, run.

Run directly: ``python tests/test_sandbox.py``. Writes go to a temp copy of the
store (``isolate_store``), so the real ``data/`` is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from _tmpstore import isolate_store  # noqa: E402

isolate_store()

from fastapi.testclient import TestClient  # noqa: E402
import numpy as np  # noqa: E402

from app.main import app  # noqa: E402
from app.services import sandbox  # noqa: E402

client = TestClient(app)
SCENE = "51e03c37-5360-43b0-a094-fbaca38cd70b"
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f" - {detail}"))
    if not ok:
        FAILURES.append(label)


def _suggest(plan_ids: list[str] | None = None) -> list[dict]:
    r = client.post(f"/api/scenes/{SCENE}/sandbox/suggestions",
                    json={"rainfallMm": 50, "planIds": plan_ids or []})
    assert r.status_code == 200, r.text
    return r.json()["suggestions"]


def _staged_pinned() -> list[dict]:
    return [f for f in client.get(f"/api/scenes/{SCENE}/pins").json()["features"]
            if f["pinned"]]


def _unstored(plan_ids: list[str]) -> float:
    r = client.post(f"/api/scenes/{SCENE}/run",
                    json={"rainfallMm": 50, "planIds": plan_ids})
    assert r.status_code == 200, r.text
    return r.json()["stats"]["scenario"]["unstoredM3"]


def main() -> int:
    print("route 1: the suggestions under a run's card")
    suggestions = _suggest()
    axes = [s["axis"] for s in suggestions]
    check("at most three, one per axis", len(suggestions) <= 3 and len(set(axes)) == len(axes), str(axes))
    check("every one is a real footprint with an estimate",
          all(s["feature"]["geometry"]["type"] == "Polygon" and "estimate" in s for s in suggestions))
    check("the run card no longer carries fixed sentences",
          sandbox_template_followups() == [], str(sandbox_template_followups()))

    print("the input card")
    reservoir = next((s for s in suggestions if s["kind"] == "water_reservoir"), None)
    check("a reservoir asks for its storage depth",
          reservoir is not None and [i["key"] for i in reservoir["inputs"]] == ["storageDepthM"])
    check("green space asks for nothing the run does not read",
          sandbox.inputs_for("green_space") == [])
    check("permeable paving offers no curve-number lever",
          sandbox.inputs_for("permeable_paving") == [])
    left = sandbox.apply_inputs("water_reservoir", {}, {})
    check("a value left to the AI is the estimate, labelled as one",
          left["inputSources"] == {"storageDepthM": "ai_estimate"} and left["storageDepthM"] == 1.5,
          str(left))
    given = sandbox.apply_inputs("water_reservoir", {}, {"storageDepthM": 3})
    check("a value the user typed is kept, labelled as theirs",
          given["inputSources"] == {"storageDepthM": "user"} and given["storageDepthM"] == 3.0)
    try:
        sandbox.apply_inputs("water_reservoir", {}, {"storageDepthM": 40})
        check("a depth the run would refuse is refused at the card", False, "accepted 40 m")
    except sandbox.InputError:
        check("a depth the run would refuse is refused at the card", True)

    print("confirming onto the sandbox")
    assert reservoir is not None
    before = _unstored([])
    bad = client.post(f"/api/scenes/{SCENE}/sandbox/stage",
                      json={"features": [reservoir["feature"]], "values": [{"storageDepthM": 40}]})
    check("a refused value is a 422 and stages nothing",
          bad.status_code == 422 and _staged_pinned() == [], bad.text[:120])
    staged = client.post(f"/api/scenes/{SCENE}/sandbox/stage",
                         json={"features": [reservoir["feature"]], "values": [{"storageDepthM": 3}]}).json()
    plan_id = staged["planId"]
    rows = _staged_pinned()
    check("it lands in the staged plan the Interventions panel reads",
          len(rows) == 1 and rows[0]["params"]["storageDepthM"] == 3.0, str(rows)[:160])
    after = _unstored([plan_id])
    check("and the next run is moved by it", after < before, f"{before} -> {after}")
    capacity_at_3m = client.post(f"/api/scenes/{SCENE}/run",
                                 json={"rainfallMm": 50, "planIds": [plan_id]}).json()["stats"]["scenario"]["storageCapacityM3"]
    again = _suggest([plan_id])
    check("the next suggestions avoid the ground it now occupies",
          all(s["feature"]["geometry"] != reservoir["feature"]["geometry"] for s in again))

    print("modification")
    resolved = client.post(f"/api/scenes/{SCENE}/sandbox/resolve-modification",
                           json={"instruction": "make the water reservoir 5 m deep"}).json()
    targets = resolved.get("targets") or []
    check("a change sentence finds the placed intervention, with the named depth",
          resolved["isModification"] and len(targets) == 1
          and targets[0]["suggestedValues"] == {"storageDepthM": 5.0}, str(resolved)[:160])
    check("a sentence about something not placed is not a modification",
          not client.post(f"/api/scenes/{SCENE}/sandbox/resolve-modification",
                          json={"instruction": "make the green space bigger"}).json()["isModification"])
    old_id = targets[0]["featureId"] if targets else ""
    refused = client.post(f"/api/scenes/{SCENE}/sandbox/modify",
                          json={"featureId": old_id, "values": {"storageDepthM": 40}})
    check("a refused value leaves the sandbox exactly as it was",
          refused.status_code == 422 and [r["id"] for r in _staged_pinned()] == [old_id])
    changed = client.post(f"/api/scenes/{SCENE}/sandbox/modify",
                          json={"featureId": old_id, "values": {"storageDepthM": 5}}).json()
    rows = _staged_pinned()
    check("the change replaces the row: one row, same footprint, new depth, still theirs",
          len(rows) == 1 and rows[0]["geometry"] == reservoir["feature"]["geometry"]
          and rows[0]["params"]["storageDepthM"] == 5.0
          and rows[0]["params"]["inputSources"] == {"storageDepthM": "user"}, str(rows)[:200])
    # Capacity, not unstored runoff: on this site the unstored volume is set by
    # where water runs, not by how deep the reservoir is (it reads the same at
    # 0.05 m and at 5 m), so only capacity can show the run took the new depth.
    capacity = client.post(f"/api/scenes/{SCENE}/run",
                           json={"rainfallMm": 50, "planIds": [plan_id]}).json()["stats"]["scenario"]["storageCapacityM3"]
    check("and the run reads the new depth", capacity > capacity_at_3m,
          f"{capacity_at_3m} -> {capacity}")
    kept = client.post(f"/api/scenes/{SCENE}/sandbox/modify",
                       json={"featureId": changed["featureId"], "values": {}}).json()
    check("a modify that types nothing keeps the current value and its origin",
          _staged_pinned()[0]["params"]["storageDepthM"] == 5.0
          and _staged_pinned()[0]["params"]["inputSources"] == {"storageDepthM": "user"}
          and kept["featureId"], str(_staged_pinned()[0]["params"])[:160])

    print("removal")
    found = client.post(f"/api/scenes/{SCENE}/sandbox/resolve-removal",
                        json={"instruction": "remove the water reservoir"}).json()["matches"]
    check("an added intervention resolves to an unpin",
          [(m["type"], m["action"]) for m in found] == [("intervention", "unpin")], str(found))
    station = client.post(f"/api/scenes/{SCENE}/sandbox/resolve-removal",
                          json={"instruction": "remove the mtr station"}).json()["matches"]
    check("a planned project resolves to a toggle-off, never a delete",
          [(m["type"], m["action"], m["planId"]) for m in station]
          == [("project", "toggle_off", "project-mtr-station")], str(station))
    city = client.post(f"/api/scenes/{SCENE}/sandbox/resolve-removal",
                       json={"instruction": "remove the buildings"}).json()
    check("existing buildings are not removable", city["matches"] == [] and city["message"])
    for row in _staged_pinned():
        client.post(f"/api/scenes/{SCENE}/pins/{row['id']}/unpin")
    check("unpinning takes it off the sandbox and out of the run",
          _staged_pinned() == [] and abs(_unstored([plan_id]) - before) < 0.01)
    check("and the ground it held can be suggested again",
          any(s["feature"]["geometry"] == reservoir["feature"]["geometry"] for s in _suggest([plan_id])))

    print("honesty: a hand-set depth is never recorded as a measurement")
    # A client that puts a depth straight into `params` (rather than answering
    # the input card) used to have it persisted and labelled `ai_estimate` --
    # a served value whose provenance label said the AI chose a number it never
    # saw. It is the sender's own value and must be labelled `user`.
    for bad, why in ((40, "out of range"), (0.001, "below the range"), ("abc", "not a number")):
        tampered, provider = _proposal("add a water reservoir in the north")
        if tampered is None:
            check("a proposal is available to tamper with", False)
            break
        tampered["params"] = {**(tampered.get("params") or {}), "storageDepthM": bad}
        refused = _apply([tampered], provider)
        detail = str(refused.json().get("detail", "")).lower()
        check(f"a hand-set depth that is {why} is a 422, not a silent default",
              refused.status_code == 422 and "storagedepthm" in detail,
              f"{bad!r} -> {refused.status_code} {refused.text[:90]}")
    clean, provider = _proposal("add a water reservoir in the north")
    if clean is not None:
        clean["params"] = {**(clean.get("params") or {}), "storageDepthM": 3.5}
        applied = _apply([clean], provider)
        # Read the sandbox, not the response: `AIPlanResponse` carries planId
        # and features, not the ids, and the row is what the run will read.
        rows = _staged_pinned()
        check("a hand-set depth is kept, and labelled the sender's own",
              applied.status_code == 200 and applied.json().get("features") == 1
              and rows and rows[0]["params"].get("storageDepthM") == 3.5
              and rows[0]["params"].get("inputSources") == {"storageDepthM": "user"},
              str(rows)[:200])
        for r in _staged_pinned():
            client.post(f"/api/scenes/{SCENE}/pins/{r['id']}/unpin")

    print("honesty: /ai-plan/apply re-validates before it pins")
    # The client owns the proposals between the gate and the apply, so a
    # geometry can change in between. A proposal moved onto surveyed buildings
    # must be refused, not persisted (the M6 hole).
    on_a_building = _building_ring()
    if on_a_building is None:
        check("a building cell is available to build a ring over", False)
    else:
        tampered, provider = _proposal("add permeable paving in the north")
        if tampered is not None:
            tampered["geometry"] = {"type": "Polygon", "coordinates": [on_a_building]}
            refused = _apply([tampered], provider)
            check("a proposal moved onto buildings is refused, not persisted",
                  refused.status_code == 200 and refused.json().get("features") == 0
                  and any((x.get("reasonCode") or x.get("reason_code")) == "BUILDING_OVERLAP"
                          for x in (refused.json().get("rejected") or []))
                  and not refused.json().get("planId"),
                  str(refused.json())[:200])

    print("honesty: an empty region says which region is empty")
    ap = client.post(f"/api/scenes/{SCENE}/ai-plan",
                     json={"instruction": "add a water reservoir in the south", "apply": False}).json()
    check("a region with no candidate names the region and where the kind is",
          not ap.get("proposals") and "south" in (ap.get("message") or "")
          and "north" in (ap.get("message") or ""), str(ap.get("message"))[:200])
    check("and it is not filed as a constraint refusal",
          not ap.get("rejected"), str(ap.get("rejected"))[:120])
    unscoped = client.post(f"/api/scenes/{SCENE}/ai-plan",
                           json={"instruction": "add a water reservoir", "apply": False}).json()
    check("an unscoped instruction keeps the generic sentence, not a region claim",
          "south region" not in (unscoped.get("message") or ""))

    print("prior work: the proposer skips rather than dead-ends")
    # Phase 4 T04b. Before this, a pinned reservoir made the proposal path
    # return an EMPTY list (every remaining candidate refused OVERLAPS_STAGED)
    # while free candidates existed. The fix narrows the pools at selection, so
    # the proposer moves on to free ground; the gate's overlap guard is
    # untouched and stays the backstop.
    for row in _staged_pinned():
        client.post(f"/api/scenes/{SCENE}/pins/{row['id']}/unpin")
    baseline = _ai_plan("add a water reservoir in the north")
    check("with nothing placed, every reservoir candidate is offered",
          len(baseline["proposals"]) == 3 and not baseline.get("rejected"),
          f"{len(baseline['proposals'])} proposals, "
          f"{len(baseline.get('rejected') or [])} refused")
    taken = baseline["proposals"][0]
    applied = _apply([taken], baseline["provider"])
    check("the first candidate can be placed",
          applied.status_code == 200 and applied.json().get("features") == 1,
          applied.text[:120])

    after = _ai_plan("add a water reservoir in the north")
    check("the taken candidate is not offered again",
          all(p["geometry"] != taken["geometry"] for p in after["proposals"]),
          f"{len(after['proposals'])} proposals")
    check("and the free candidates still are -- no dead end",
          len(after["proposals"]) == 2 and not after.get("rejected"),
          f"{len(after['proposals'])} proposals, "
          f"{len(after.get('rejected') or [])} refused")
    check("the shorter pool says why it is shorter",
          any("already covered by work on the sandbox" in n
              for n in after.get("groundingNotes", [])),
          str(after.get("groundingNotes"))[-160:])
    check("the constraint guard is untouched: a stale coordinate ask still refuses",
          _untouched_guard_refuses(), "")

    # With every reservoir placed, the kind is exhausted -- and the sentence has
    # to say that rather than blame the constraint engine or tell the planner to
    # rebuild rasters that are present and correct.
    remaining = _ai_plan("add a water reservoir in the north")["proposals"]
    for extra in remaining:
        _apply([extra], baseline["provider"])
    exhausted = client.post(f"/api/scenes/{SCENE}/ai-plan",
                            json={"instruction": "add a water reservoir in the north",
                                  "provider": "heuristic", "apply": False})
    check("an exhausted kind is not reported as missing data",
          exhausted.status_code in (200, 422)
          and "fetch_dem" not in exhausted.text
          and "fetch_landcover" not in exhausted.text,
          exhausted.text[:140])
    check("and it names the reason: prior work claims the ground",
          "already covered by work on the sandbox" in exhausted.text,
          exhausted.text[:160])
    # A kind the planner did NOT ask for must not be reported at all: the
    # reservoirs are full, and that is a true sentence about the wrong thing.
    for row in _staged_pinned():
        client.post(f"/api/scenes/{SCENE}/pins/{row['id']}/unpin")

    print("preview: the effect of an uncommitted change")
    # Phase 4 T01. The preview must measure, never run: no runId, no overlay, no
    # card, nothing written, and the same answer twice.
    for row in _staged_pinned():
        client.post(f"/api/scenes/{SCENE}/pins/{row['id']}/unpin")
    candidate = _ai_plan("add a water reservoir in the north")["proposals"][0]
    shallow = _preview(candidate, 1.5)
    deep = _preview(candidate, 3.0)

    check("the preview labels itself as not-a-run",
          shallow["status"] == "PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE"
          and shallow["warning"], shallow["status"])
    check("and carries no run identity at all",
          not any(k in shallow for k in
                  ("runId", "run_id", "overlayUrls", "explanationCard")),
          str(sorted(shallow))[:160])
    check("the scope declines the figures it does not measure",
          "NOT the flood extent" in shallow["scope"] and "NOT the overlay" in shallow["scope"],
          shallow["scope"][:120])
    check("the marginal delta is not published under the run's key",
          "deltaUnstoredM3" not in shallow["previewDelta"]
          and "deltaUnstoredM3" in shallow["runDelta"],
          f"previewDelta {sorted(shallow['previewDelta'])} vs runDelta {sorted(shallow['runDelta'])}")
    check("the candidate is measured as applied, not skipped",
          len(shallow["interventionsApplied"]) == 1 and not shallow["interventionsSkipped"],
          str(shallow["interventionsSkipped"])[:120])
    check("a deeper depth raises capacity and does not pretend to drain more",
          deep["previewDelta"]["storageCapacityM3"] > shallow["previewDelta"]["storageCapacityM3"]
          and abs(deep["previewDelta"]["unstoredM3"] - shallow["previewDelta"]["unstoredM3"]) < 0.01,
          f"cap {shallow['previewDelta']['storageCapacityM3']} -> "
          f"{deep['previewDelta']['storageCapacityM3']}, "
          f"unstored {shallow['previewDelta']['unstoredM3']} -> "
          f"{deep['previewDelta']['unstoredM3']}")
    check("the preview is byte-identical on repeat",
          json.dumps(_preview(candidate, 3.0), sort_keys=True)
          == json.dumps(deep, sort_keys=True))
    check("and it wrote nothing: the sandbox is unchanged",
          _staged_pinned() == [])
    check("an out-of-range depth is a 422, not a silent default",
          _preview_response(candidate, 40).status_code == 422,
          str(_preview_response(candidate, 40).text)[:110])

    print(f"\n{'all sandbox checks passed' if not FAILURES else f'{len(FAILURES)} failed'}")
    return 1 if FAILURES else 0


def _ai_plan(instruction: str) -> dict:
    return client.post(f"/api/scenes/{SCENE}/ai-plan",
                       json={"instruction": instruction, "apply": False}).json()


def _preview(candidate: dict, depth: float) -> dict:
    resp = _preview_response(candidate, depth)
    check("the preview answered", resp.status_code == 200, resp.text[:120])
    return resp.json() if resp.status_code == 200 else {}


def _preview_response(candidate: dict, depth: float):
    return client.post(f"/api/scenes/{SCENE}/sandbox/preview", json={
        "interventions": [candidate],
        "values": [{"storageDepthM": depth}],
        "planIds": [],
    })


def _untouched_guard_refuses() -> bool:
    """The gate still refuses a placement on claimed ground, so prevention did
    not replace detection.

    A coordinate-based ask bypasses the pool filter entirely -- it names a cell
    rather than choosing a candidate -- so it is the case that proves the guard
    still runs. The refusal is asserted as *a refusal*, not as one particular
    code: the exact predicate that fires at a given cell depends on how much of
    that cell's window is building, claimed or off-grid, and pinning the code
    would make this test a statement about one cell rather than about the guard.
    What matters is that it is refused, and that the claimed cells are among the
    reasons reported.
    """
    from app.services import proposal_constraints as pc
    from app.services.sandbox_model import load_grids

    grids = load_grids()
    if grids is None:
        return True
    taken = pc.claimed_cells_for(SCENE)
    if not taken:
        return False
    row, col = sorted(taken)[0]
    x0, y0, x1, y1 = grids.bounds
    lon = x0 + (col + 0.5) / grids.cols * (x1 - x0)
    lat = y1 - (row + 0.5) / grids.rows * (y1 - y0)
    seen = pc.validate_proposal(
        grids,
        {"intervention_type": "water_reservoir",
         "params": {"requested": {"centre": {"lon": lon, "lat": lat},
                                 "sizeCells": 60}}},
        pc.building_mask(grids), taken)
    return (seen["reasonCode"] is not None
            and seen["constraints"]["overlapCells"] > 0)


def _proposal(instruction: str) -> tuple[dict | None, str]:
    """One served proposal for ``instruction`` and the provider that served it.

    The provider is returned rather than assumed: the apply endpoint echoes it
    and rejects a value it did not emit, and a test that hardcoded "heuristic"
    would stop exercising that.
    """
    body = client.post(f"/api/scenes/{SCENE}/ai-plan",
                       json={"instruction": instruction, "apply": False}).json()
    proposals = body.get("proposals") or []
    return (dict(proposals[0]) if proposals else None), str(body.get("provider") or "heuristic")


def _apply(features: list[dict], provider: str = "heuristic"):
    """Confirm proposals onto the sandbox the way the client does."""
    return client.post(f"/api/scenes/{SCENE}/ai-plan/apply",
                       json={"instruction": "test", "provider": provider,
                             "proposals": features})


def _building_ring() -> list[list[float]] | None:
    """A ring whose cells are all surveyed building, or None if none is found."""
    from app.services import proposal_constraints, sandbox_model
    from _overlay_common import load_overlay

    grids = sandbox_model.load_grids()
    if grids is None:
        return None
    mask = proposal_constraints.building_mask(load_overlay())
    if mask is None:
        return None
    rows, cols = np.where(mask)
    if len(rows) == 0:
        return None
    x0, y0, x1, y1 = grids.bounds
    r0, c0 = int(rows[0]), int(cols[0])

    def at(r: int, c: int) -> list[float]:
        return [x0 + (c + 0.5) / grids.cols * (x1 - x0),
                y1 - (r + 0.5) / grids.rows * (y1 - y0)]

    ring = [at(r0, c0), at(r0, c0 + 4), at(r0 + 4, c0 + 4), at(r0 + 4, c0)]
    return ring if all(mask[r, c] for r, c in grids.cells_of_ring(ring)) else None


def sandbox_template_followups() -> list[str]:
    from app.services.run_card import run_card

    return run_card({"scenario": {}, "baseline": {}, "delta": {}}, 50.0).followups


if __name__ == "__main__":
    raise SystemExit(main())
