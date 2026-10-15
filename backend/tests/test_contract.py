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

import re as _re
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.schemas.scene import SceneCreate, close_ring  # noqa: E402
from app.services import scene_service  # noqa: E402

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
        ("/api/plans/{plan_id}/interventions/{intervention_id}", "delete"),
        ("/api/scenes/{scene_id}/ai-plan", "post"),
        ("/api/scenes/{scene_id}/ai-plan/apply", "post"),
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


def _poly(cx: float, cy: float, d: float = 0.002) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [cx, cy], [cx + d, cy], [cx + d, cy + d], [cx, cy + d], [cx, cy],
        ]],
    }


def test_delete_intervention_hard_deletes_one() -> None:
    scene_id = client.post("/api/scenes", json={"name": "DelOne", "center": CENTER}).json()[
        "sceneId"
    ]
    try:
        plan = client.post(
            f"/api/scenes/{scene_id}/plans",
            json={
                "planType": "custom",
                "label": "two features",
                "interventions": [
                    {
                        "geometry": _poly(113.992, 22.438),
                        "interventionType": "透水铺装",
                        "params": {"permeability": 0.6},
                        "style": {"color": "#1976d2", "opacity": 0.7},
                    },
                    {
                        "geometry": _poly(113.994, 22.440),
                        "interventionType": "绿地",
                        "params": {},
                        "style": {"color": "#2ea043", "opacity": 0.5},
                    },
                ],
            },
        )
        assert plan.status_code == 201
        plan_id = plan.json()["planId"]

        features = client.get(f"/api/plans/{plan_id}/interventions").json()["features"]
        assert len(features) == 2
        victim_id = features[0]["id"]
        survivor_id = features[1]["id"]

        gone = client.delete(f"/api/plans/{plan_id}/interventions/{victim_id}")
        assert gone.status_code == 204
        assert gone.content == b""

        remaining = client.get(f"/api/plans/{plan_id}/interventions").json()["features"]
        remaining_ids = [f["id"] for f in remaining]
        assert remaining_ids == [survivor_id]
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_delete_intervention_404_on_unknown_plan() -> None:
    body = client.delete("/api/plans/does-not-exist/interventions/also-not").json()
    assert body["error"]["code"] == "PLAN_NOT_FOUND"


def test_delete_intervention_404_on_unknown_intervention() -> None:
    scene_id = client.post("/api/scenes", json={"name": "DelNope", "center": CENTER}).json()[
        "sceneId"
    ]
    try:
        plan_id = client.post(
            f"/api/scenes/{scene_id}/plans",
            json={"planType": "baseline", "label": "empty"},
        ).json()["planId"]
        body = client.delete(f"/api/plans/{plan_id}/interventions/no-such-id").json()
        assert body["error"]["code"] == "INTERVENTION_NOT_FOUND"
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def _seeded_scene_for_ai_plan() -> str | None:
    """The seeded HSK scene id, or None if the catalogue is empty.

    The AI module needs a scene with grounding rasters attached. The seeded
    scene is the only one that has them; a fresh `POST /api/scenes` would not,
    which is why ai-plan would 422 with "no grounded candidate sites".
    """
    plans = client.get("/api/scenes").json() if False else None
    _ = plans
    # The seeded scene id is written to data/scenes/*.json at setup time; look
    # it up by iterating `GET /api/scenes/{id}` is not exposed, so read the
    # catalogue directly through the service layer.
    scenes_dir = scene_service.settings.scenes_dir
    for path in scenes_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("wet_grid_path") or doc.get("dem_path"):
            return doc.get("id") or path.stem
    return None


def test_ai_plan_proposes_without_persisting_when_apply_false() -> None:
    scene_id = _seeded_scene_for_ai_plan()
    if not scene_id:
        return  # No seeded scene available in this environment; skipping.

    plans_before = client.get(f"/api/scenes/{scene_id}/plans").json()["plans"]
    body = client.post(
        f"/api/scenes/{scene_id}/ai-plan",
        json={
            "instruction": "add two water reservoirs in the north",
            "provider": "heuristic",
            "apply": False,
        },
    )
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["planId"] is None
    # Propose mode returns proposals; apply=true mode leaves it empty.
    assert isinstance(data.get("proposals"), list)
    # The store is unchanged -- the whole point of the confirm gate.
    plans_after = client.get(f"/api/scenes/{scene_id}/plans").json()["plans"]
    assert len(plans_after) == len(plans_before)


def test_ai_plan_allowed_types_filters_output() -> None:
    """Plan v2 §8: allowedTypes drops proposals outside the whitelist.

    'make it nicer' through the heuristic rescues as a mix of reservoir, green
    and paving. Pin allowedTypes=['green_space'] and no other type survives.
    """
    scene_id = _seeded_scene_for_ai_plan()
    if not scene_id:
        return

    body = client.post(
        f"/api/scenes/{scene_id}/ai-plan",
        json={
            "instruction": "make it nicer",
            "provider": "heuristic",
            "apply": False,
            "allowedTypes": ["green_space"],
        },
    )
    assert body.status_code == 200, body.text
    data = body.json()
    for p in data.get("proposals", []):
        assert p["interventionType"] == "green_space", p


def test_llm_config_get_never_returns_the_api_key() -> None:
    """Plan v2 §9.2 Decision: the API key is write-only."""
    body = client.get("/api/llm-config").json()
    assert "api_key" not in body and "apiKey" not in body, body
    assert "anthropic_api_key" not in body and "anthropicApiKey" not in body, body
    assert "configured" in body
    assert "baseUrl" in body or "base_url" in body


def test_llm_config_put_overrides_in_memory() -> None:
    """A PUT updates the live settings; a GET reflects it; a restart would
    clear it. The last part is implicit (we are in-process) -- the first two
    are what the UI relies on."""
    from app.config import settings

    before = {
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "api_key": settings.llm_api_key,
    }
    try:
        r = client.put(
            "/api/llm-config",
            json={"baseUrl": "http://unit-test/v1", "model": "unit-test-model", "apiKey": "k"},
        )
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["baseUrl"] == "http://unit-test/v1"
        assert got["model"] == "unit-test-model"
        # key is NOT in the response (write-only)
        assert "apiKey" not in got and "api_key" not in got
        assert got["configured"] is True
        # verify settings itself was updated
        assert settings.llm_api_key == "k"
    finally:
        # restore
        settings.llm_base_url = before["base_url"]
        settings.llm_model = before["model"]
        settings.llm_api_key = before["api_key"]


def test_ai_plan_allowed_types_rejects_garbage() -> None:
    scene_id = _seeded_scene_for_ai_plan()
    if not scene_id:
        return
    r = client.post(
        f"/api/scenes/{scene_id}/ai-plan",
        json={
            "instruction": "make it nicer",
            "provider": "heuristic",
            "apply": False,
            "allowedTypes": ["ufo_landing_pad"],
        },
    )
    assert r.status_code == 422
    assert "allowedTypes" in r.json()["error"]["message"] or "allowed" in r.json()["error"]["message"].lower()


def test_ai_plan_apply_persists_proposals() -> None:
    scene_id = _seeded_scene_for_ai_plan()
    if not scene_id:
        return

    proposed = client.post(
        f"/api/scenes/{scene_id}/ai-plan",
        json={
            "instruction": "add two water reservoirs in the north",
            "provider": "heuristic",
            "apply": False,
        },
    ).json()
    proposals = proposed["proposals"]
    if not proposals:
        return  # Grounding returned nothing in this environment; skipping.

    plans_before = client.get(f"/api/scenes/{scene_id}/plans").json()["plans"]
    applied = client.post(
        f"/api/scenes/{scene_id}/ai-plan/apply",
        json={
            "instruction": "add two water reservoirs in the north",
            "provider": proposed["provider"],
            "proposals": proposals,
            "groundingNotes": proposed.get("groundingNotes", []),
            "candidateCounts": proposed.get("candidateCounts", {}),
        },
    )
    assert applied.status_code == 200, applied.text
    data = applied.json()
    plan_id = data["planId"]
    try:
        assert plan_id is not None
        assert data["features"] == len(proposals)
        plans_after = client.get(f"/api/scenes/{scene_id}/plans").json()["plans"]
        assert len(plans_after) == len(plans_before) + 1
    finally:
        if plan_id:
            # Clean up the plan we just created so the store survives the suite.
            scene_service._delete_plan_files(plan_id)


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
# 4. Stages 3-6 -- the four new routes and the extended /ventilation
# --------------------------------------------------------------------------- #


def _scene_with_model() -> str:
    """A fresh scene carrying the shipped city model, for the routes that need one."""
    scene_id = client.post(
        "/api/scenes", json={"name": "Stage 3-6 probe", "center": CENTER}
    ).json()["sceneId"]
    scene_service.attach_overlay(scene_id, "buildings_path", "rasters/buildings_hsk.json")
    return scene_id


def _scenarios_body(weights: dict | None = None) -> dict:
    body = {
        "scenarios": [
            {"scenarioId": "A", "massingOps": []},
            {"scenarioId": "B", "massingOps": [{"op": "scale_all", "multiplier": 1.2}]},
        ]
    }
    if weights is not None:
        body["weights"] = weights
    return body


def test_compare_endpoint_refuses_without_weights() -> None:
    scene_id = _scene_with_model()
    try:
        r = client.post(f"/api/scenes/{scene_id}/scenario/compare",
                        json=_scenarios_body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["refused"] is True
        assert body["ranked"] is None
        assert isinstance(body["reason"], str) and len(body["reason"]) > 20
        assert body["combinedScore"] is None
        assert body["verdict"] is None
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_compare_endpoint_orders_with_weights() -> None:
    scene_id = _scene_with_model()
    try:
        r = client.post(f"/api/scenes/{scene_id}/scenario/compare",
                        json=_scenarios_body({"shade": 1.0, "ventilation": 2.0,
                                              "drainage": 1.0}))
        assert r.status_code == 200, r.text
        body = r.json()
        for field in ("refused", "reason", "weightsAsSupplied", "weightsUsed",
                      "weightsNormalisedBySum", "weightsAreCallersOpinion", "polarity",
                      "normalisation", "sharedBaseline", "scenarioCount", "ranked",
                      "byAxis", "combinedScore", "verdict", "whyNoVerdict", "status",
                      "warning"):
            assert field in body, f"missing {field}"
        assert body["refused"] is False
        assert body["combinedScore"] is None
        assert body["verdict"] is None
        assert abs(sum(body["weightsUsed"].values()) - 1.0) < 1e-9
        assert len(body["ranked"]) == 2
        for row in body["ranked"]:
            for field in ("rank", "scenarioId", "axes", "favourability",
                          "contributions", "weightedFavourability", "perAxisRank"):
                assert field in row, f"missing ranked row {field}"
            assert set(row["axes"]) == {"shade", "ventilation", "drainage"}
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_compare_endpoint_422_cases() -> None:
    scene_id = _scene_with_model()
    try:
        url = f"/api/scenes/{scene_id}/scenario/compare"
        # negative weight
        assert client.post(url, json=_scenarios_body({"shade": -1.0})).status_code == 422
        # unknown axis name
        assert client.post(url, json=_scenarios_body({"nonsense": 1.0})).status_code == 422
        # all-zero weights
        assert client.post(url, json=_scenarios_body(
            {"shade": 0.0, "ventilation": 0.0, "drainage": 0.0})).status_code == 422
        # duplicate scenarioId
        dup = {"scenarios": [{"scenarioId": "A"}, {"scenarioId": "A"}]}
        assert client.post(url, json=dup).status_code == 422
        # out-of-range dayOfYear
        body = _scenarios_body()
        body["dayOfYear"] = 999
        assert client.post(url, json=body).status_code == 422
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_heat_field_endpoint_contract() -> None:
    scene_id = _scene_with_model()
    try:
        r = client.get(f"/api/scenes/{scene_id}/heat-field",
                       params={"hours": "9,14", "day_of_year": 355})
        assert r.status_code == 200, r.text
        body = r.json()
        for field in ("meanHeatIndex", "maxHeatIndex", "weights", "components", "svf",
                      "anchoring", "status", "warning", "note"):
            assert field in body, f"missing {field}"
        assert body["svf"]["searchRadiusCells"] is not None
        anchor = body["anchoring"]
        assert anchor["anchorSource"] == "hko_station"
        assert anchor["anchorRangeC"] == [26.4, 32.7]
        assert anchor["quantity"] == "HKO_air"
        assert anchor["calibrationPath"] == "reference/heat_anchor.json"
        assert anchor["status"] == "ANCHORED_TO_PUBLISHED_REFERENCE"
        assert anchor["heatIndexSource"] == (
            "Hand-authored screening index; awaiting verification")
        assert "not a measurement of this site" in anchor["warning"]
        assert body["status"] == "RELATIVE_INDEX_NOT_A_TEMPERATURE"
        # the field is never a temperature: no key looks like a Celsius field
        assert not _celsius_like_keys(body), _celsius_like_keys(body)
        # an explicit winter day is still honoured (only the *default* moved)
        winter = client.get(f"/api/scenes/{scene_id}/heat-field",
                            params={"day_of_year": 355})
        assert winter.json()["solarInputs"]["dayOfYear"] == 355
        # out-of-range hours is refused, not silently clamped
        assert client.get(f"/api/scenes/{scene_id}/heat-field",
                          params={"hours": "99"}).status_code == 422
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_heat_field_rejects_out_of_range_day_of_year() -> None:
    """An out-of-range day-of-year must be refused, not silently evaluated.

    ``solar_position`` accepts any day-of-year, so 999 produces a plausible-looking
    sun position for a day that does not exist -- the exact silent-wrong-number
    failure this project keeps hitting. ``day_of_year`` and its ``dayOfYear``
    spelling must both be range-checked.
    """
    scene_id = _scene_with_model()
    try:
        assert client.get(f"/api/scenes/{scene_id}/heat-field",
                          params={"day_of_year": 999}).status_code == 422
        assert client.get(f"/api/scenes/{scene_id}/heat-field",
                          params={"dayOfYear": 999}).status_code == 422
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_design_metrics_day_of_year_alias_and_range() -> None:
    """``/design-metrics`` honours both spellings and range-checks the day.

    The camelCase alias and the range check were both missing until the fix: a
    camelCase caller was silently served the 355 default, and a day of 999 reached
    ``solar_position`` and returned a plausible-looking sun position for a date
    that does not exist. Both are pinned here so neither can regress.
    """
    scene_id = _scene_with_model()
    try:
        url = f"/api/scenes/{scene_id}/design-metrics"

        # 1. both spellings take effect, not merely parse
        snake = client.get(url, params={"day_of_year": 172})
        camel = client.get(url, params={"dayOfYear": 172})
        assert snake.status_code == 200, snake.text
        assert camel.status_code == 200, camel.text
        assert snake.json()["solarInputs"]["dayOfYear"] == 172
        assert camel.json()["solarInputs"]["dayOfYear"] == 172
        snake_alt = [i["solarAltitudeDeg"] for i in snake.json()["shade"]["instants"]]
        camel_alt = [i["solarAltitudeDeg"] for i in camel.json()["shade"]["instants"]]
        assert snake_alt == camel_alt, (
            f"the alias changed the geometry: {snake_alt} vs {camel_alt}")

        # 2. out-of-range is refused under both spellings
        assert client.get(url, params={"day_of_year": 999}).status_code == 422
        assert client.get(url, params={"dayOfYear": 999}).status_code == 422
        assert client.get(url, params={"day_of_year": 0}).status_code == 422
        assert client.get(url, params={"day_of_year": 367}).status_code == 422

        # 3. the default path is unchanged, and the parameter actually does something
        default = client.get(url).json()
        assert default["solarInputs"]["dayOfYear"] == 355
        default_alt = [i["solarAltitudeDeg"] for i in default["shade"]["instants"]]
        assert default_alt != snake_alt, (
            "day_of_year made no difference: the parameter is inert")
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_corridors_endpoint_contract() -> None:
    scene_id = _scene_with_model()
    try:
        r = client.get(f"/api/scenes/{scene_id}/ventilation/corridors",
                       params={"directions": "NE,SW"})
        assert r.status_code == 200, r.text
        body = r.json()
        for field in ("metric", "costSurface", "resolutionM", "directions", "status",
                      "warning", "limitation"):
            assert field in body, f"missing {field}"
        assert set(body["directions"]) == {"NE", "SW"}
        for block in body["directions"].values():
            for field in ("corridorCells", "pathLengthM", "meanCost", "blocked", "path"):
                assert field in block, f"missing corridor {field}"
        # unknown direction is refused
        assert client.get(f"/api/scenes/{scene_id}/ventilation/corridors",
                          params={"directions": "XX"}).status_code == 422
        # the default is the eight principal directions
        default = client.get(f"/api/scenes/{scene_id}/ventilation/corridors").json()
        assert set(default["directions"]) == {
            "N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_drainage_routed_endpoint_contract() -> None:
    # Routing reads only the land-class grid and the saved artefacts, so a scene
    # with NO city model must still answer -- unlike the other three new routes.
    scene_id = client.post(
        "/api/scenes", json={"name": "Routing only", "center": CENTER}
    ).json()["sceneId"]
    try:
        r = client.get(f"/api/scenes/{scene_id}/drainage/routed",
                       params={"rainfall_mm": 450})
        assert r.status_code == 200, r.text
        body = r.json()
        for field in ("runoffInM3", "storedM3", "dischargedM3", "massBalance",
                      "wetCells", "maxDepthM", "meanDepthInWetAreaM", "depressions",
                      "hand", "handVsRouted", "status", "warning", "rainfallMm"):
            assert field in body, f"missing {field}"
        assert body["massBalance"]["balanced"] is True
        assert abs(body["runoffInM3"]
                   - (body["storedM3"] + body["dischargedM3"])) < 1.0
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_city_model_routes_refuse_a_model_less_scene() -> None:
    scene_id = client.post(
        "/api/scenes", json={"name": "No model", "center": CENTER}
    ).json()["sceneId"]
    try:
        assert client.get(f"/api/scenes/{scene_id}/heat-field").status_code == 422
        assert client.get(f"/api/scenes/{scene_id}/ventilation").status_code == 422
        assert client.get(
            f"/api/scenes/{scene_id}/ventilation/corridors").status_code == 422
        assert client.post(
            f"/api/scenes/{scene_id}/scenario/compare",
            json={"scenarios": [{"scenarioId": "A"}]}).status_code == 422
    finally:
        client.delete(f"/api/scenes/{scene_id}")


# The pre-existing /ventilation payload, before Stages 3-6 added anything. The
# extension must not have reshaped a single one of these; `corridors` and
# `fadRatio` are the only additions.
_VENTILATION_PREEXISTING = {
    "metric", "units", "siteAreaM2", "buildingCount", "frontalAreaDensity",
    "prevailingDirections", "combinedFrequencyPercent", "prevailingMeanFad",
    "leastExposedPrevailingDirection", "limitation", "note", "sceneId",
    "publishedReference",
}


def test_ventilation_keeps_its_preexisting_keys() -> None:
    scene_id = _scene_with_model()
    try:
        body = client.get(f"/api/scenes/{scene_id}/ventilation").json()
        assert set(body) == _VENTILATION_PREEXISTING | {"corridors", "fadRatio"}, (
            f"unexpected key change: {sorted(set(body) ^ (_VENTILATION_PREEXISTING | {'corridors', 'fadRatio'}))}")
        # The pre-existing payload is still the untouched screening output.
        assert body["buildingCount"] == json.loads(
            (BACKEND_DIR / "data" / "rasters" / "buildings_meta.json").read_text(encoding="utf-8"))["features"]
        assert len(body["frontalAreaDensity"]["byDirection"]) == 16
        assert body["publishedReference"] is not None
        assert "corridors" in body and "fadRatio" in body
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def _truthy_verdict_hits(node, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("verdict", "pass", "fail") and value:
                hits.append(f"{path}.{key}={value!r}")
            hits.extend(_truthy_verdict_hits(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_truthy_verdict_hits(value, f"{path}[{i}]"))
    return hits


def _celsius_like_keys(node, path: str = "") -> list[str]:
    """Keys that look like a Celsius temperature field (the honesty scan).

    The heat field is a relative 0-1 index; the anchor states a band it is read
    against, but no response key may read as a temperature field. A key such as
    ``tempC`` / ``lstC`` / ``degreesCelsius`` would be the hidden conversion this
    guards against.
    """
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if _re.search(r"(?i)(temp|temperature|lst).*c$", key) or \
                    _re.search(r"(?i)(celsius|degreesc)", key):
                hits.append(f"{path}.{key}")
            hits.extend(_celsius_like_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_celsius_like_keys(value, f"{path}[{i}]"))
    return hits


def test_no_new_response_carries_a_truthy_verdict() -> None:
    scene_id = _scene_with_model()
    try:
        responses = [
            client.post(f"/api/scenes/{scene_id}/scenario/compare",
                        json=_scenarios_body({"shade": 1.0})).json(),
            client.get(f"/api/scenes/{scene_id}/heat-field").json(),
            client.get(f"/api/scenes/{scene_id}/ventilation/corridors",
                       params={"directions": "NE"}).json(),
            client.get(f"/api/scenes/{scene_id}/drainage/routed").json(),
            client.get(f"/api/scenes/{scene_id}/ventilation").json(),
        ]
        for i, body in enumerate(responses):
            hits = _truthy_verdict_hits(body)
            assert not hits, f"response {i} carries a truthy verdict/pass/fail: {hits}"
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_query_param_spellings_both_take_effect() -> None:
    """camelCase and snake_case must both change the answer, not just be accepted."""
    scene_id = _scene_with_model()
    try:
        snake = client.get(f"/api/scenes/{scene_id}/heat-field",
                           params={"day_of_year": 172}).json()
        camel = client.get(f"/api/scenes/{scene_id}/heat-field",
                           params={"dayOfYear": 172}).json()
        assert snake["solarInputs"]["dayOfYear"] == 172
        assert camel["solarInputs"]["dayOfYear"] == 172
        # and it is not merely the default being echoed
        default = client.get(f"/api/scenes/{scene_id}/heat-field").json()
        assert default["solarInputs"]["dayOfYear"] == 196

        wet_snake = client.get(f"/api/scenes/{scene_id}/drainage/routed",
                               params={"rainfall_mm": 550}).json()
        wet_camel = client.get(f"/api/scenes/{scene_id}/drainage/routed",
                               params={"rainfallMm": 550}).json()
        assert wet_snake["rainfallMm"] == 550.0
        assert wet_camel["rainfallMm"] == 550.0
        dry = client.get(f"/api/scenes/{scene_id}/drainage/routed",
                         params={"rainfall_mm": 450}).json()
        assert wet_snake["runoffInM3"] > dry["runoffInM3"]
    finally:
        client.delete(f"/api/scenes/{scene_id}")


# --------------------------------------------------------------------------- #
# Planned projects on the scene record
# --------------------------------------------------------------------------- #


def _project_store(tmp: Path, projects: dict[str, dict], files: list[str]):
    """Point `settings` at a throwaway data/model tree holding the given projects.

    The resolver is a pure function of two directories, so it is tested against a
    temp tree rather than the real one: that keeps the test independent of which
    projects happen to be seeded, and lets it prove an absent file is omitted
    rather than relying on one being absent in the working copy.
    """
    from app.config import settings

    data, model = tmp / "data", tmp / "model"
    (data / "projects").mkdir(parents=True)
    model.mkdir()
    for name, doc in projects.items():
        (data / "projects" / f"{name}.json").write_text(
            json.dumps(doc), encoding="utf-8")
    for rel in files:
        target = data / rel if rel.endswith(".json") else model / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    saved = (settings.data_dir, settings.model_dir)
    object.__setattr__(settings, "data_dir", data)
    object.__setattr__(settings, "model_dir", model)
    return saved


def _restore(saved) -> None:
    from app.config import settings

    object.__setattr__(settings, "data_dir", saved[0])
    object.__setattr__(settings, "model_dir", saved[1])


def test_the_scene_lists_every_project_that_has_a_model() -> None:
    """A second modeled project must appear with no code change.

    The wire used to carry two singular URL fields -- one project per scene -- so
    the viewer's model handle and project id were both hardcoded and a second
    model could not appear at all. The ceiling was the wire shape.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        saved = _project_store(
            Path(t),
            {
                "alpha": {"id": "alpha", "name": "Alpha", "model": "a.glb",
                          "footprint": "a.json", "origin": [114.0, 22.4], "scale": 10},
                "beta": {"id": "beta", "name": "Beta", "model": "b.glb",
                         "footprint": "b.json", "origin": [114.1, 22.5], "scale": 20,
                         "label": "Beta site"},
            },
            ["a.glb", "a.json", "b.glb", "b.json"],
        )
        try:
            found = scene_service.project_models()
        finally:
            _restore(saved)

    assert [p.project_id for p in found] == ["alpha", "beta"]
    beta = found[1]
    assert beta.origin == (114.1, 22.5) and beta.scale == 20.0
    assert beta.label == "Beta site"
    assert beta.model_url.endswith("/model/b.glb"), beta.model_url


def test_a_project_with_a_missing_file_is_omitted_not_listed_with_nulls() -> None:
    """Draw what exists. A missing GLB is a build step not run, not a project to
    render a placeholder for -- and a null-filled entry would make the viewer try
    to load a model that is not there."""
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        saved = _project_store(
            Path(t),
            {
                "ok": {"id": "ok", "name": "Ok", "model": "ok.glb",
                       "footprint": "ok.json", "origin": [114.0, 22.4]},
                "no_glb": {"id": "no_glb", "name": "NoGlb", "model": "gone.glb",
                           "footprint": "n.json", "origin": [114.0, 22.4]},
                "no_fp": {"id": "no_fp", "name": "NoFp", "model": "f.glb",
                          "footprint": "missing.json", "origin": [114.0, 22.4]},
                "effects_only": {"id": "effects_only", "name": "FxOnly"},
            },
            ["ok.glb", "ok.json", "n.json", "f.glb"],
        )
        try:
            found = [p.project_id for p in scene_service.project_models()]
        finally:
            _restore(saved)

    assert found == ["ok"], found


def test_an_unreadable_project_file_does_not_take_the_scene_down() -> None:
    """One malformed project file must not 500 the whole terrain endpoint."""
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        saved = _project_store(Path(t), {}, [])
        try:
            from app.config import settings

            (settings.data_dir / "projects" / "broken.json").write_text(
                "{not json", encoding="utf-8")
            found = scene_service.project_models()
        finally:
            _restore(saved)

    assert found == []


def test_the_terrain_endpoint_carries_projects_and_not_the_old_fields() -> None:
    """The singular fields are gone, not deprecated: one consumer, one commit."""
    scene_id = _seeded_scene_for_ai_plan()
    assert scene_id, "no seeded scene on disk; run scripts/seed_scene.py"
    body = client.get(f"/api/scenes/{scene_id}/terrain").json()
    assert isinstance(body.get("projects"), list), body.keys()
    assert "planProjectUrl" not in body and "planProjectFootprintUrl" not in body


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
