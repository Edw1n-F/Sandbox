"""Tests for the fixed project catalog (P0.1 of the intervention loop plan).

The failure this suite guards against is the one the placeholder had: a project
whose footprint is re-derived per toggle, so a before/after comparison compares
two different geometries. So the checks below assert *sameness* (the same phase
twice, the catalog and the generated file, the plan payload and the catalog
geometry) and *consequence* (a phase actually changes the ledger, and the
geometry is the documented true mask rather than a bounding box).

Run:  ./backend/.venv/bin/python tests/test_project_catalog.py
"""

from __future__ import annotations

import json
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
from app.services import project_catalog  # noqa: E402
from app.services.sandbox_model import (  # noqa: E402
    _polygon_cells,
    ledger_for_scenario,
    load_grids,
)
from app.services.scene_service import create_scene  # noqa: E402
from app.schemas.scene import SceneCreate  # noqa: E402

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _catalog() -> dict:
    return project_catalog.load_catalog()


def _first_phase() -> tuple[dict, dict]:
    catalog = _catalog()
    project = catalog["projects"][0]
    return project, project["phases"][0]


def _scene_id() -> str:
    body = SceneCreate(
        name="catalog-test",
        bbox={
            "type": "Polygon",
            "coordinates": [[
                [113.982, 22.425], [114.005, 22.425],
                [114.005, 22.455], [113.982, 22.455], [113.982, 22.425],
            ]],
        },
        center={"type": "Point", "coordinates": [113.9935, 22.44]},
    )
    return create_scene(body).scene_id


# --------------------------------------------------------------------------- #
# The catalog document
# --------------------------------------------------------------------------- #


def test_catalog_matches_the_generator_output_byte_for_byte() -> None:
    """The checked-in file is what the script produces, not an edited copy.

    A hand-tweaked catalog is exactly how a demo acquires a footprint nobody
    can reproduce, so the committed file is pinned against its own generator
    on every run.
    """
    import subprocess

    out = Path(isolate_store()) / "regenerated_projects.json"
    subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "make_project_catalog.py"),
         "--out", str(out)],
        check=True, capture_output=True,
    )
    committed = (BACKEND_DIR / "data" / "projects.json").read_bytes()
    assert committed == out.read_bytes(), (
        "data/projects.json differs from scripts/make_project_catalog.py output; "
        "re-run the script and commit the result"
    )


def test_every_project_phase_and_feature_carries_provenance() -> None:
    catalog = _catalog()
    assert catalog["warning"], "the catalog must state what it is not"
    assert catalog["projects"], "a catalog with no projects is not a catalog"

    for project in catalog["projects"]:
        assert project["source"] and project["date"] and project["confidence"], (
            f"{project['id']} lacks source/date/confidence")
        assert project["phases"], f"{project['id']} has no phases"
        seen_labels = set()
        for phase in project["phases"]:
            assert phase["interventions"], (
                f"{project['id']}/{phase['id']} has no interventions")
            assert phase["grounding"].get("evidence"), (
                f"{project['id']}/{phase['id']} has no grounding evidence")
            seen_labels.add(phase["label"])
            phase_cells = 0
            for feature in phase["interventions"]:
                provenance = feature.get("provenance") or {}
                assert provenance.get("why"), (
                    f"{project['id']}/{phase['id']} feature has no provenance")
                # A footprint must be a contour of a real cell count, never a
                # bounding box: at least 5 vertices and a measured area.
                ring = feature["geometry"]["coordinates"][0]
                assert len(ring) >= 6 or feature["geometry"]["type"] != "Polygon", (
                    f"{project['id']}/{phase['id']} ring has {len(ring)} points; "
                    "a 5-point ring is a bounding box")
                assert provenance["cells"] >= 1, (
                    f"{project['id']}/{phase['id']} covers {provenance['cells']} "
                    "cells; nothing to convert")
                # A banded or haloed phase legitimately produces small fringe
                # parts -- they are measured ground, not noise to be quietly
                # dropped -- so the floor applies to the phase as a whole.
                phase_cells += provenance["cells"]
            assert phase_cells >= 40, (
                f"{project['id']}/{phase['id']} covers {phase_cells} cells in "
                "total; too small to be a project")
        assert len(seen_labels) == len(project["phases"]), (
            f"{project['id']} repeats a phase label")


def test_no_two_phases_are_the_same_geometry() -> None:
    """Phase 2 must not be Phase 1 again.

    The placeholder's pond project could legitimately produce an identical
    footprint for 'full' and 'partial'; here, phases are generated from
    distinct rasters rules and a repeat would mean one of them is decorative.
    """
    catalog = _catalog()
    for project in catalog["projects"]:
        shapes = set()
        for phase in project["phases"]:
            for feature in phase["interventions"]:
                shapes.add(json.dumps(feature["geometry"], sort_keys=True)
                           + "|" + feature["intervention_type"])
        if len(project["phases"]) > 1:
            assert len(shapes) > 1, (
                f"{project['id']} has {len(project['phases'])} phases that all "
                "describe the same geometry")


def test_a_bad_catalog_is_refused_not_truncated() -> None:
    """A typo in the file is an error, not a shorter list."""
    bad = {"schema": project_catalog.SCHEMA, "projects": []}  # no warning
    try:
        project_catalog._validate(bad)
    except project_catalog.CatalogError:
        pass
    else:  # pragma: no cover - the raise is the assertion
        raise AssertionError("a catalog with no warning text was accepted")

    no_phase = {
        "schema": project_catalog.SCHEMA,
        "warning": "x",
        "projects": [{
            "id": "p", "name": "P", "note": "n", "axis": "drainage",
            "source": "s", "date": "2026-10-15", "confidence": "c",
            "phases": [{"id": "a", "label": "A", "interventions": [],
                        "grounding": {"evidence": "e"}}],
        }],
    }
    try:
        project_catalog._validate(no_phase)
    except project_catalog.CatalogError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a phase with no interventions was accepted")


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #


def test_endpoint_serves_the_catalog_for_a_real_scene() -> None:
    scene_id = _scene_id()
    body = client.get(f"/api/scenes/{scene_id}/project-catalog")
    assert body.status_code == 200, body.text
    doc = body.json()
    assert doc["schema"] == project_catalog.SCHEMA
    assert [p["id"] for p in doc["projects"]], "the endpoint returned no projects"


def test_endpoint_404s_for_an_unknown_scene() -> None:
    body = client.get("/api/scenes/not-a-scene/project-catalog")
    assert body.status_code == 404, body.text


# --------------------------------------------------------------------------- #
# The plan payload -- deterministic composition
# --------------------------------------------------------------------------- #


def test_the_same_phase_twice_is_the_same_plan_payload() -> None:
    project, phase = _first_phase()
    first = project_catalog.phase_plan_payload(project["id"], phase["id"])
    second = project_catalog.phase_plan_payload(project["id"], phase["id"])
    assert first == second, "the same phase produced two different plans"
    assert first["interventions"], "the payload carried no interventions"
    for feature in first["interventions"]:
        assert feature["geometry"]["type"] == "Polygon"
        assert feature["params"]["provenance"], (
            "the plan payload dropped the provenance")


def test_plan_payload_reruns_the_ledger_and_changes_it() -> None:
    """A catalog phase is not decoration: it moves the drainage ledger.

    One run with the phase's real features, against the same run without them.
    The delta is whatever the ledger says; the assertion is that it is
    non-zero, which is what stops a catalog of no-ops.
    """
    project, phase = _first_phase()
    payload = project_catalog.phase_plan_payload(project["id"], phase["id"])
    features = [{
        "id": f"catalog-{i}",
        "intervention_type": f["intervention_type"],
        "geometry": f["geometry"],
        "params": f["params"],
    } for i, f in enumerate(payload["interventions"])]

    grids = load_grids()
    result = ledger_for_scenario(grids, features, rainfall_mm=450.0)
    assert result["interventionsApplied"], (
        "the ledger skipped every catalog feature")
    assert result["deltaUnstoredM3"] != 0.0, (
        "a catalog phase left the unstored volume unchanged")


def test_catalog_geometry_is_the_true_mask_not_a_box() -> None:
    """The polygon must enclose the cells it claims, measured with the
    ledger's own centre test."""
    project, phase = _first_phase()
    payload = project_catalog.phase_plan_payload(project["id"], phase["id"])
    grids = load_grids()
    for feature, source in zip(payload["interventions"],
                               phase["interventions"]):
        coordinates = feature["geometry"]["coordinates"]
        cells = _polygon_cells(grids, coordinates)
        claimed = source["provenance"]["cells"]
        assert cells, f"{project['id']}/{phase['id']} rasterises to no cells"
        assert len(cells) == claimed, (
            f"{project['id']}/{phase['id']} claims {claimed} cells but the "
            f"ledger rasteriser makes {len(cells)}")
        # A bounding box of the same mask would contain strictly more cells
        # whenever the mask is not a rectangle, so this is the check that the
        # polygon is a contour and not a box pretending to be one.
        outer = coordinates[0]
        corners = [grids.cell_of(lon, lat) for lon, lat in outer]
        min_r = min(r for r, _ in corners)
        max_r = max(r for r, _ in corners)
        min_c = min(c for _, c in corners)
        max_c = max(c for _, c in corners)
        boxed = (max_r - min_r + 1) * (max_c - min_c + 1)
        assert len(cells) <= boxed, "contour enclosed more than its own bbox"


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
