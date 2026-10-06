"""Contract and invariant tests (ADR-036).

Three cheap tests, chosen because they cover the failures that would be
embarrassing in public:

1. **Contract** -- every endpoint answers with the spec's exact field names, in
   camelCase, and errors use the unified envelope.  Catches the ADR-027 class of
   bug where the map silently renders nothing.
2. **Silent-constant** -- the two places the source documents warn "fail
   silently": bbox ring closure (a 2-coordinate ring is not a polygon) and the
   storage ``id`` -> wire ``sceneId`` alias.
3. **Cascade** -- deleting a scene removes its plans and interventions.

Run:  python -m pytest tests/ -q      (or: python tests/test_contract.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.schemas.scene import SceneCreate, close_ring  # noqa: E402

client = TestClient(app)

HSK_BBOX = {
    "type": "Polygon",
    "coordinates": [
        [
            [113.982, 22.425],
            [114.005, 22.425],
            [114.005, 22.455],
            [113.982, 22.455],
            [113.982, 22.425],
        ]
    ],
}
CENTER = {"type": "Point", "coordinates": [113.9935, 22.44]}


# --------------------------------------------------------------------------- #
# 1. Contract
# --------------------------------------------------------------------------- #


def test_health_reports_store_mode() -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["storeMode"] in {"file", "postgis"}


def test_openapi_lists_every_contract_endpoint() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path, method in [
        ("/api/scenes", "post"),
        ("/api/scenes/{scene_id}", "get"),
        ("/api/scenes/{scene_id}", "put"),
        ("/api/scenes/{scene_id}", "delete"),
        ("/api/scenes/{scene_id}/plans", "get"),
        ("/api/scenes/{scene_id}/plans", "post"),
        ("/api/plans/{plan_id}", "put"),
        ("/api/plans/{plan_id}/interventions", "get"),
        ("/api/scenes/{scene_id}/terrain", "get"),
    ]:
        assert path in paths, f"missing {path}"
        assert method in paths[path], f"missing {method.upper()} {path}"


def test_scene_lifecycle_returns_spec_shapes() -> None:
    created = client.post(
        "/api/scenes",
        json={
            "name": "Contract probe",
            "center": CENTER,
            "bbox": HSK_BBOX,
            "imageryUrl": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "defaultCamera": {"height": 3000, "heading": 0, "pitch": -45},
            "envParams": {"temperature": 28.5, "precipitation": 450},
        },
    )
    assert created.status_code == 201
    assert set(created.json()) == {"sceneId", "name", "createdAt"}
    scene_id = created.json()["sceneId"]

    read = client.get(f"/api/scenes/{scene_id}")
    assert read.status_code == 200
    body = read.json()
    # §3.2.2 field names, camelCase, plus the documented additive overlay fields.
    for field in (
        "sceneId",
        "name",
        "center",
        "bbox",
        "terrainUrl",
        "imageryUrl",
        "defaultCamera",
        "envParams",
        "createdAt",
        "updatedAt",
    ):
        assert field in body, f"missing {field}"
    # §4.2.1's DDL comment shape: the camera is always complete on the wire.
    assert body["defaultCamera"]["longitude"] == 113.9935
    assert body["defaultCamera"]["latitude"] == 22.44

    terrain = client.get(f"/api/scenes/{scene_id}/terrain").json()
    for field in ("terrainUrl", "imageryUrl", "bbox"):
        assert field in terrain

    plans = client.get(f"/api/scenes/{scene_id}/plans").json()
    assert "plans" in plans

    updated = client.put(
        f"/api/scenes/{scene_id}", json={"envParams": {"temperature": 31.0}}
    )
    assert updated.status_code == 200
    assert updated.json()["envParams"]["temperature"] == 31.0
    # A partial PUT must not erase untouched fields.
    assert updated.json()["imageryUrl"] is not None

    assert client.delete(f"/api/scenes/{scene_id}").status_code == 204


def test_interventions_return_a_real_feature_collection() -> None:
    scene_id = client.post("/api/scenes", json={"name": "Geo", "center": CENTER}).json()[
        "sceneId"
    ]
    plan = client.post(
        f"/api/scenes/{scene_id}/plans",
        json={
            "planType": "custom",
            "label": "方案B",
            "interventions": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [113.992, 22.438],
                                [113.996, 22.438],
                                [113.996, 22.442],
                                [113.992, 22.442],
                                [113.992, 22.438],
                            ]
                        ],
                    },
                    "interventionType": "透水铺装",
                    "params": {"permeability": 0.6},
                    "style": {"color": "#1976d2", "opacity": 0.7},
                }
            ],
        },
    )
    assert plan.status_code == 201
    plan_id = plan.json()["planId"]

    fc = client.get(f"/api/plans/{plan_id}/interventions").json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"]["interventionType"] == "透水铺装"
    # Geometry is pass-through: coordinates survive byte-identically.
    assert feature["geometry"]["coordinates"][0][0] == [113.992, 22.438]
    # ADR-030: status is server-owned and always starts as a draft.
    assert feature["properties"]["status"] == "draft"

    client.delete(f"/api/scenes/{scene_id}")


def test_empty_feature_collection_is_valid() -> None:
    scene_id = client.post("/api/scenes", json={"name": "Bare", "center": CENTER}).json()[
        "sceneId"
    ]
    plan_id = client.post(
        f"/api/scenes/{scene_id}/plans", json={"planType": "baseline", "label": "基线"}
    ).json()["planId"]
    fc = client.get(f"/api/plans/{plan_id}/interventions").json()
    assert fc == {"type": "FeatureCollection", "features": []}
    client.delete(f"/api/scenes/{scene_id}")


# --------------------------------------------------------------------------- #
# 2. Silent-constant tests
# --------------------------------------------------------------------------- #


def test_bbox_ring_is_closed_on_ingress() -> None:
    """§3.2.2 ships a 2-coordinate "polygon"; it must be repaired, not trusted."""
    unclosed = {
        "type": "Polygon",
        "coordinates": [[[113.99, 22.43], [114.0, 22.43], [114.0, 22.45]]],
    }
    ring = close_ring(unclosed)["coordinates"][0]
    assert ring[0] == ring[-1], "outer ring must repeat its first position"
    assert all(len(position) == 2 for position in ring), "positions stay [lon, lat]"

    scene = SceneCreate(name="closure", center=CENTER, bbox=unclosed)
    assert scene.bbox["coordinates"][0][0] == scene.bbox["coordinates"][0][-1]


def test_already_closed_ring_is_left_alone() -> None:
    ring_before = [list(p) for p in HSK_BBOX["coordinates"][0]]
    ring_after = close_ring(HSK_BBOX)["coordinates"][0]
    assert ring_after == ring_before


def test_storage_id_becomes_scene_id_on_the_wire() -> None:
    """The DDL column is `id`; the API field is `sceneId`.  Never a rename."""
    scene_id = client.post("/api/scenes", json={"name": "Alias", "center": CENTER}).json()[
        "sceneId"
    ]
    body = client.get(f"/api/scenes/{scene_id}").json()
    assert body["sceneId"] == scene_id
    assert "id" not in body, "the storage column name must not leak to the wire"
    client.delete(f"/api/scenes/{scene_id}")


# --------------------------------------------------------------------------- #
# 3. Cascade + error envelope
# --------------------------------------------------------------------------- #


def test_delete_cascades_to_plans_and_interventions() -> None:
    scene_id = client.post("/api/scenes", json={"name": "Cascade", "center": CENTER}).json()[
        "sceneId"
    ]
    plan_id = client.post(
        f"/api/scenes/{scene_id}/plans",
        json={
            "planType": "custom",
            "interventions": [
                {
                    "geometry": {"type": "Point", "coordinates": [113.99, 22.44]},
                    "interventionType": "蓄水池",
                }
            ],
        },
    ).json()["planId"]

    assert client.delete(f"/api/scenes/{scene_id}").status_code == 204
    assert client.get(f"/api/scenes/{scene_id}").status_code == 404
    assert client.get(f"/api/plans/{plan_id}/interventions").status_code == 404


def test_error_envelope_is_unified() -> None:
    missing = client.get("/api/scenes/nope")
    assert missing.status_code == 404
    body = missing.json()
    # §3.5 asks for {"detail": ...} and for {"error": {"code","message"}}; both ship.
    assert body["detail"] == "scene not found"
    assert body["error"]["code"] == "SCENE_NOT_FOUND"

    invalid = client.post("/api/scenes", json={"name": "", "center": CENTER})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert invalid.json()["error"]["details"]


def test_epsg4326_is_enforced() -> None:
    """A projected CRS reaching the API is an error, not a silent axis swap."""
    scene_id = client.post("/api/scenes", json={"name": "Proj", "center": CENTER}).json()[
        "sceneId"
    ]
    bad = client.post(
        f"/api/scenes/{scene_id}/plans",
        json={
            "planType": "custom",
            "interventions": [
                {
                    "geometry": {"type": "Point", "coordinates": [820000, 835000]},
                    "interventionType": "绿地",
                }
            ],
        },
    )
    assert bad.status_code == 422
    client.delete(f"/api/scenes/{scene_id}")


# --------------------------------------------------------------------------- #
# Minimal runner so the suite works without pytest installed
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
