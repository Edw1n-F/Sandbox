"""Tests for the suggest step of the intervention loop (P0.2).

The failure modes this suite guards against are specific:

1. **A suggestion that does nothing when pinned.** Every offered candidate is
   put through the ledger's own coverage guard before it is offered, so a
   suggestion that would be refused on screen never appears.
2. **A score.** There is deliberately no combined number across the three axes;
   the units are incommensurable and the axes genuinely conflict.
3. **A number that does not match the ledger's arithmetic.** The runoff delta
   is recomputed here from first principles (SCS-CN, HSG D, the 254 constant)
   and compared, so a drifted formula fails rather than quietly quoting.
4. **Non-determinism.** The same query twice must be the same answer, to the
   last digit -- no model, no clock, no iteration-order accident.

Run:  ./backend/.venv/bin/python tests/test_suggest.py
"""

from __future__ import annotations

import math
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

from app.main import app  # noqa: E402
from app.services import suggest  # noqa: E402
from app.services.sandbox_model import load_grids  # noqa: E402
from app.services.scene_service import create_scene  # noqa: E402
from app.schemas.scene import SceneCreate  # noqa: E402

client = TestClient(app)


def _scene_id() -> str:
    return create_scene(SceneCreate(
        name="suggest-test",
        bbox={
            "type": "Polygon",
            "coordinates": [[
                [113.9922, 22.431], [113.9994, 22.431],
                [113.9994, 22.4363], [113.9922, 22.4363], [113.9922, 22.431],
            ]],
        },
        center={"type": "Point", "coordinates": [113.9958, 22.4337]},
    )).scene_id


def _runoff_depth_mm(rainfall_mm: float, cn: float) -> float:
    """SCS-CN runoff depth in mm, derived here rather than imported.

    S = (25400 / CN) - 254.  Q = (P - 0.2 S)^2 / (P + 0.8 S).
    The constant is **254**, not 2540: S_inches = 1000/CN - 10 and 1 in is
    25.4 mm, so S_mm = 25400/CN - 254.
    """
    retention = 25400.0 / cn - 254.0
    if rainfall_mm <= 0.2 * retention:
        return 0.0
    return ((rainfall_mm - 0.2 * retention) ** 2
            / (rainfall_mm + 0.8 * retention))


# --------------------------------------------------------------------------- #
# Honesty labels and shape
# --------------------------------------------------------------------------- #


def test_suggestions_carry_a_status_and_a_human_warning() -> None:
    out = suggest.suggest()
    assert out["status"] == suggest.STATUS
    assert out["warning"] and len(out["warning"]) > 40, (
        "the warning must say what these numbers are not")
    assert out["generatedFrom"]["deterministic"] is True


def test_no_combined_score_anywhere_in_the_output() -> None:
    """The three axes must never be collapsed into one number."""
    out = suggest.suggest()
    forbidden = ("score", "combined", "best", "rank", "verdict", "grade")
    for axis in out["axes"]:
        for key in axis:
            assert not any(word in key.lower() for word in forbidden), (
                f"{axis['axis']}.{key} sounds like a cross-axis judgement")
        for candidate in axis["candidates"]:
            for key in candidate:
                assert not any(word in key.lower() for word in forbidden), (
                    f"{axis['axis']}.{key} sounds like a judgement")
            for key in candidate["estimate"]:
                assert not any(word in key.lower() for word in forbidden), (
                    f"{axis['axis']}.estimate.{key} sounds like a judgement")
    assert [a["axis"] for a in out["axes"]] == ["drainage", "heat", "runoff"], (
        "axes are reported in a fixed order, never sorted by score")


# --------------------------------------------------------------------------- #
# The guard: nothing offered that the ledger would refuse
# --------------------------------------------------------------------------- #


def test_every_offered_candidate_is_one_the_ledger_will_apply() -> None:
    out = suggest.suggest()
    grids = load_grids()
    offered = 0
    for axis in out["axes"]:
        for candidate in axis["candidates"]:
            offered += 1
            intervention = candidate["plan"]["interventions"][0]
            from app.services.sandbox_model import apply_interventions

            _trial, _cn, _storage, applied, skipped = apply_interventions(
                grids, [{
                    "id": "probe",
                    "intervention_type": intervention["intervention_type"],
                    "geometry": intervention["geometry"],
                    "params": intervention.get("params") or {},
                }])
            assert applied, (
                f"{candidate['kind']} is offered but the ledger refuses it: "
                f"{[s.get('why') for s in skipped]}")
        # ...and the refused ones are reported, not hidden.
        for refused in axis["candidatesRefused"]:
            assert refused.get("why"), "a refused candidate must say why"
    assert offered > 0, "no candidate was offered at all"


def test_refusals_are_the_caps_and_are_reported() -> None:
    out = suggest.suggest()
    survey = {r["why"] for axis in out["axes"] for r in axis["candidatesRefused"]}
    assert survey, "this study area has an over-cap parcel, so it must appear"
    for why in survey:
        assert "25%" in why or "40%" in why, (
            f"a refusal for a reason other than the caps: {why}")


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #


def test_runoff_delta_is_the_scs_cn_arithmetic_recomputed() -> None:
    """The suggester's runoff number, recomputed from first principles."""
    out = suggest.suggest(rainfall_mm=450.0)
    grids = load_grids()
    cell_area = grids.cell_area_m2
    checked = 0
    for axis in out["axes"]:
        for candidate in axis["candidates"]:
            estimate = candidate["estimate"]
            if estimate["classAfter"] == "green" and estimate["classToday"] == "paved":
                expected_mm = (_runoff_depth_mm(450.0, 98.0)
                               - _runoff_depth_mm(450.0, 80.0))
                expected_m3 = expected_mm / 1000.0 * estimate["cellsCovered"] * cell_area
                assert math.isclose(estimate["runoffDeltaM3"], expected_m3,
                                    rel_tol=0.01, abs_tol=0.5), (
                    f"runoff delta {estimate['runoffDeltaM3']} != "
                    f"{expected_m3:.1f} m3 from SCS-CN")
                checked += 1
    assert checked > 0, "no paved->green candidate was checked"


def test_storage_is_cells_times_working_depth() -> None:
    out = suggest.suggest()
    grids = load_grids()
    from scripts._overlay_common import STORAGE_DEPTH_M

    for axis in out["axes"]:
        for candidate in axis["candidates"]:
            estimate = candidate["estimate"]
            if candidate["kind"] == "underground_tank":
                expected = (STORAGE_DEPTH_M["tank"] * estimate["cellsCovered"]
                            * grids.cell_area_m2)
                assert math.isclose(estimate["storageAddedM3"], expected,
                                    rel_tol=0.01), (
                    f"tank storage {estimate['storageAddedM3']} != {expected:.1f}")


def test_a_reservoir_is_quoted_as_raising_runoff_while_storing_water() -> None:
    """Water is CN 100 in TR-55: the reservoir's benefit is storage, not runoff.

    This is the candidate's own ledger table talking. The suggestion must not
    hide it, and must not relabel a negative as "avoided".
    """
    out = suggest.suggest()
    tanks = [c for axis in out["axes"] for c in axis["candidates"]
             if c["kind"] == "water_reservoir"]
    if not tanks:  # pragma: no cover - this study area has one basin
        return
    estimate = tanks[0]["estimate"]
    assert estimate["storageAddedM3"] > 0, "the basin must add storage"
    assert estimate["runoffDeltaM3"] < 0, (
        "paved (CN 98) -> water (CN 100) must show as a runoff increase")
    assert "runoffDeltaM3" in estimate, "the field is a delta, not an avoidance"


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_the_same_query_twice_is_the_same_answer() -> None:
    first = suggest.suggest(rainfall_mm=450.0)
    second = suggest.suggest(rainfall_mm=450.0)
    assert first == second, "the suggester is not deterministic"


def test_rainfall_changes_the_numbers_not_the_pools() -> None:
    """The pools are a property of the rasters; only the estimates move."""
    dry = suggest.suggest(rainfall_mm=100.0)
    wet = suggest.suggest(rainfall_mm=450.0)
    keys = lambda out: [
        [c["kind"] + "@" + str(c["estimate"]["cellsCovered"])
         for c in axis["candidates"]] for axis in out["axes"]]
    assert keys(dry) == keys(wet), "the candidate set changed with rainfall"
    if dry["axes"][0]["candidates"]:
        assert (dry["axes"][0]["candidates"][0]["estimate"]["storageAddedM3"]
                == wet["axes"][0]["candidates"][0]["estimate"]["storageAddedM3"]), (
            "storage does not depend on rainfall; if it did, something is wrong")


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #


def test_endpoint_serves_suggestions_for_a_real_scene() -> None:
    scene_id = _scene_id()
    # Query params are snake_case here, like every other GET in this router.
    body = client.get(f"/api/scenes/{scene_id}/suggestions",
                      params={"rainfall_mm": 450, "max_per_axis": 2})
    assert body.status_code == 200, body.text
    doc = body.json()
    assert doc["status"] == suggest.STATUS
    for axis in doc["axes"]:
        assert len(axis["candidates"]) <= 2
        assert axis["basis"], "each axis states its own basis"


def test_endpoint_404s_for_an_unknown_scene() -> None:
    body = client.get("/api/scenes/not-a-scene/suggestions")
    assert body.status_code == 404, body.text


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
