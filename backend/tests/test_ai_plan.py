"""Tests for the natural-language -> intervention module.

The LLM path is driven through an injected transport rather than a live call,
so the request shape, the tool-use parsing and -- most importantly -- the
grounding constraint are exercised on every test run with no API key and no
network. A test that only runs when a credential happens to be present is not a
test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.services.ai_plan import (
    ANTHROPIC_URL,
    CHOOSE_TOOL,
    FIT_TOOL,
    HeuristicProvider,
    LLMProvider,
    MASSING_TOOL,
    OpenAICompatProvider,
    PlanGenerationError,
    _cell_axes,
    _fraction_ring,
    _post_json,
    build_prompt,
    get_provider,
    load_grounding,
    parse_fit_intent,
    parse_intent,
    parse_massing_intent,
    resolve_scope,
    route_tools)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL  {label} - {detail}")


def recorded_transport(selections: list[dict], *, capture: dict | None = None):
    """A transport that replays a recorded Messages API response shape."""

    def _send(url: str, api_key: str, payload: dict) -> dict:
        if capture is not None:
            capture.update({"url": url, "api_key": api_key, "payload": payload})
        return {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "toolu_fixture",
                "name": CHOOSE_TOOL["name"],
                "input": {"selections": selections},
            }],
            "stop_reason": "tool_use",
        }

    return _send


def test_grounding_is_derived_and_cited() -> None:
    g = load_grounding()
    if not g.any_available():
        print("      (skipped: run the fetch_* and make_wetgrid scripts first)")
        return
    check("grounding offers candidate sites", g.any_available(), "nothing available")
    for pool in (g.reservoir_sites, g.green_sites, g.paving_sites):
        for site in pool:
            check(f"site cites its raster ({site['kind']})",
                  bool(site["evidence"]) and site["areaHa"] > 0,
                  f"evidence={site['evidence']!r} area={site['areaHa']}")
            ring = site["geometry"]["coordinates"][0]
            check(f"site ring closes ({site['kind']})",
                  len(ring) >= 4 and ring[0] == ring[-1], f"{len(ring)} points")
            break


def test_prompt_gives_the_model_no_coordinates_to_copy() -> None:
    """The model chooses from a menu; it is never shown a coordinate to echo.

    This is the structural guarantee against hallucinated geometry. If a site id
    or evidence string ever grew a coordinate pair, the model would have
    something to copy and the guarantee would quietly weaken.
    """
    g = load_grounding()
    if not g.any_available():
        print("      (skipped: no grounding)")
        return
    _, user = build_prompt("add a reservoir", g)
    payload = json.loads(user)
    for site in payload["candidate_sites"][:6]:
        check(f"catalogue {site['site_id']} carries no coordinate",
              "centroid" not in site and "geometry" not in site,
              "the model was given geometry it could echo")
    check("catalogue uses opaque ids",
          all("#" in s["site_id"] for s in payload["candidate_sites"]),
          "site ids are not selection handles")


def test_llm_choices_become_real_grounded_geometry() -> None:
    g = load_grounding()
    if not g.reservoir_sites:
        print("      (skipped: no reservoir sites)")
        return
    capture: dict = {}
    provider = LLMProvider(api_key="test-key", transport=recorded_transport(
        [{"site_id": "water_reservoir#0",
          "intervention_type": "water_reservoir",
          "rationale": "deepest basin on the model"}], capture=capture))
    out = provider.propose("add a reservoir", g)

    check("one feature produced", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    f = out[0]
    check("request went to the Messages API", capture["url"] == ANTHROPIC_URL,
          capture.get("url", ""))
    check("api key forwarded", capture["api_key"] == "test-key", "missing")
    # Was a forced tool; is now `any`, because a massing instruction and an
    # intervention instruction are genuinely different intents. The compensation
    # is the system-prompt routing rule, asserted separately in
    # test_massing_tool_is_offered_alongside_the_site_tool.
    check("only the site tool is offered for an intervention instruction",
          [t["name"] for t in capture["payload"]["tools"]] == [CHOOSE_TOOL["name"]],
          str([t["name"] for t in capture["payload"]["tools"]]))
    check("and it is forced",
          capture["payload"]["tool_choice"] == {"type": "tool", "name": CHOOSE_TOOL["name"]},
          json.dumps(capture["payload"]["tool_choice"]))
    check("marked source=ai", f["source"] == "ai", f["source"])
    check("geometry is the real site, not invented",
          f["geometry"] == g.reservoir_sites[0]["geometry"], "geometry differs")
    check("rationale carried through",
          "deepest basin" in f["params"]["rationale"], f["params"]["rationale"])
    check("basis recorded", bool(f["params"]["basis"]), "no basis")


def test_llm_cannot_invent_a_site_id() -> None:
    """An id we never offered is dropped, not guessed at."""
    provider = LLMProvider(api_key="k", transport=recorded_transport([
        {"site_id": "water_reservoir#9999", "intervention_type": "water_reservoir",
         "rationale": "made up"},
        {"site_id": "not_a_kind#0", "intervention_type": "green_space",
         "rationale": "wrong pool"}]))
    out = provider.propose("add a reservoir", load_grounding())
    check("invented ids are dropped", out == [], f"accepted {len(out)} bogus")


def test_llm_type_field_cannot_relabel_a_site() -> None:
    """The id's pool wins, so a park cannot be labelled a reservoir."""
    g = load_grounding()
    if not g.green_sites:
        print("      (skipped: no green sites)")
        return
    provider = LLMProvider(api_key="k", transport=recorded_transport([
        {"site_id": "green_space#0", "intervention_type": "water_reservoir",
         "rationale": "mislabelled"}]))
    out = provider.propose("do something", g)
    check("feature takes its type from the site pool",
          bool(out) and out[0]["intervention_type"] == "green_space",
          out[0]["intervention_type"] if out else "no output")


def test_llm_without_a_key_refuses_rather_than_silently_degrading() -> None:
    try:
        LLMProvider(api_key="").propose("add a reservoir", load_grounding())
        check("missing key raises", False, "it silently returned something")
    except PlanGenerationError as exc:
        check("missing key raises", "ANTHROPIC_API_KEY" in str(exc), str(exc)[:60])


def test_auto_provider_falls_back_and_says_so() -> None:
    """`auto` falls back only when nothing is configured.

    An earlier version of this test read the ambient settings, so it began
    failing the moment a local Ollama was configured -- which is the point of the
    feature, not a regression. It now controls the configuration it depends on,
    by patching the settings the providers actually read.
    """
    from app.config import settings
    from app.services.ai_plan import get_provider, live_providers

    saved = (settings.anthropic_api_key, settings.llm_base_url, settings.llm_model)
    try:
        settings.anthropic_api_key = ""
        settings.llm_base_url = ""
        settings.llm_model = ""
        check("nothing configured -> no live providers", live_providers() == [],
              str([p.name for p in live_providers()]))
        check("auto picks the heuristic when nothing is configured",
              isinstance(get_provider("auto"), HeuristicProvider),
              type(get_provider("auto")).__name__)
        try:
            get_provider("llm")
            check("explicit llm with nothing configured raises", False, "no error")
        except PlanGenerationError:
            check("explicit llm with nothing configured raises", True)
    finally:
        (settings.anthropic_api_key, settings.llm_base_url,
         settings.llm_model) = saved

    # With something configured, auto must prefer it over the heuristic.
    if live_providers():
        check("auto prefers a configured provider over the heuristic",
              not isinstance(get_provider("auto"), HeuristicProvider),
              "fell back to the heuristic while a provider was configured")
    else:
        print("      (no provider configured; preference branch not exercised)")


def test_vague_instruction_invents_nothing() -> None:
    check("'make it nicer' yields no intent", parse_intent("make it nicer") == [],
          str(parse_intent("make it nicer")))
    out = HeuristicProvider().propose("make it nicer", load_grounding())
    check("and proposes no features", out == [], f"{len(out)} features")


def test_intent_parsing_reads_counts() -> None:
    got = dict(parse_intent("add two water reservoirs and a green space"))
    check("word counts parsed", got.get("water_reservoir") == 2, str(got))
    got2 = dict(parse_intent("build 3 permeable paving areas"))
    check("numerals parsed", got2.get("permeable_paving") == 3, str(got2))


def test_stored_ai_features_are_all_drafts() -> None:
    """ADR-030: machine-proposed geometry is never 'validated'."""
    from app import db
    from app.config import settings

    checked = 0
    for path in db.list_docs(settings.plans_dir):
        doc = db.read_doc(path)
        if not doc or doc.get("source") != "ai":
            continue
        for item in doc.get("interventions", []):
            check("AI feature is draft", item.get("status") == "draft",
                  f"{item.get('intervention_type')} is {item.get('status')}")
            checked += 1
    if not checked:
        print("      (no AI plans stored yet; skipped)")


def massing_transport(edits: list[dict], rationale: str = "because", *, capture=None):
    """A transport replaying a recorded `modify_massing` tool-use response."""
    def _send(url: str, api_key: str, payload: dict) -> dict:
        if capture is not None:
            capture.update({"url": url, "api_key": api_key, "payload": payload})
        return {
            "id": "msg_fixture", "type": "message", "role": "assistant",
            "content": [{
                "type": "tool_use", "id": "toolu_fixture",
                "name": "modify_massing",
                "input": {"edits": edits, "rationale": rationale},
            }],
            "stop_reason": "tool_use",
        }
    return _send


# Substrings that would let a model author geometry. Checked against the tool's
# own property names rather than its serialised text: "string" contains "ring",
# so a naive substring scan of the schema reports a false positive every time.
GEOMETRY_WORDS = ("coord", "ring", "geometry", "polygon", "point", "lat", "lon",
                  "position", "vertex", "wkt", "geojson")


def _geometry_fields(tool: dict, prefix: str = "") -> list[str]:
    """Every property name anywhere in a tool schema that could carry geometry."""
    found: list[str] = []
    properties = (tool.get("input_schema") or tool).get("properties") or {}
    for name, spec in properties.items():
        path = f"{prefix}{name}"
        if any(word in name.lower() for word in GEOMETRY_WORDS):
            found.append(path)
        for key in ("items", "properties"):
            nested = (spec or {}).get(key)
            if isinstance(nested, dict):
                found.extend(_geometry_fields(nested, f"{path}."))
    return found


def test_llm_massing_op_becomes_a_grounded_draft_feature() -> None:
    """"Make the district 25% taller" -> one massing_height feature, multiplier 1.25.

    The acceptance test from the gap brief. Checks source, status, the multiplier,
    and that the geometry is a real closed ring rather than an invented shape.
    """
    g = load_grounding()
    capture: dict = {}
    provider = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "scale_all", "multiplier": 1.25}], capture=capture))
    out = provider.propose("make the district 25% taller", g)

    check("one feature produced", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    f = out[0]
    check("intervention_type is massing_height",
          f["intervention_type"] == "massing_height", f["intervention_type"])
    check("multiplier is 1.25", f["params"]["multiplier"] == 1.25,
          str(f["params"]["multiplier"]))
    check("op is scale_all", f["params"]["op"] == "scale_all", str(f["params"]["op"]))
    check("source is ai", f["source"] == "ai", f["source"])
    check("rationale carried through", bool(f["params"]["rationale"]), "no rationale")
    ring = f["geometry"]["coordinates"][0]
    check("geometry is a closed ring",
          f["geometry"]["type"] == "Polygon" and len(ring) >= 4 and ring[0] == ring[-1],
          f"{f['geometry']['type']} {len(ring)} pts")
    check("the request offered the model no coordinate to echo",
          not _geometry_fields(capture["payload"]["tools"][0]),
          f"coordinate-bearing fields: {_geometry_fields(capture['payload']['tools'][0])}")


def test_scope_is_resolved_here_and_is_not_the_model_s_geometry() -> None:
    """"Make the western blocks 30% taller" must not become a global edit.

    This is the regression that motivated scope. The first massing tool had no
    way to say *where*, so the region word was discarded and `scale_all` was
    returned for every building: a plausible 3D answer to a different question.
    The model may name a region; it may not place one.
    """
    g = load_grounding()
    capture: dict = {}
    provider = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "scale_all", "multiplier": 1.3, "scope": "west"}], capture=capture))
    out = provider.propose("make the western blocks 30% taller", g)
    check("one feature produced", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    scope = out[0]["params"]["scope"]
    lon_min, lat_min, lon_max, lat_max = g.bounds
    mid = round(lon_min + (lon_max - lon_min) / 2, 7)
    west = [p[0] for p in scope["ring"]]
    check("scope is a region, not the whole study area", scope["kind"] == "region",
          scope["kind"])
    check("the ring is the western half of the locked bbox",
          max(west) == mid and min(west) == round(lon_min, 7),
          f"lon {min(west)}..{max(west)} vs mid {mid}")
    check("the feature's own geometry is the area it affects",
          out[0]["geometry"]["coordinates"][0] == scope["ring"], "geometry differs")
    check("label is human-readable", scope["label"] == "the west", scope["label"])
    # The tool schema is where a coordinate would have to come from.
    tool = capture["payload"]["tools"][0]
    check("the tool offers no coordinate field",
          not _geometry_fields(tool),
          f"coordinate-bearing fields: {_geometry_fields(tool)}")


def test_a_scoped_edit_reaches_less_than_the_whole_area() -> None:
    """Two different scopes must not resolve to the same polygon."""
    g = load_grounding()
    rings = {}
    for region in ("all", "west", "east", "north-west", "centre"):
        feature = LLMProvider(api_key="k", transport=massing_transport(
            [{"op": "scale_all", "multiplier": 1.1, "scope": region}])).propose(
                f"scale {region}", g)
        rings[region] = feature[0]["params"]["scope"]["ring"] if feature else None
    check("every named region resolves", all(rings.values()), str(rings))
    check("all ten regions are distinct where they should be",
          len({json.dumps(r) for r in rings.values()}) == len(rings),
          "two regions resolved to the same polygon")
    lon_min, lat_min, lon_max, lat_max = g.bounds
    mid_lon = (lon_min + lon_max) / 2
    mid_lat = (lat_min + lat_max) / 2
    inside = lambda ring, lon, lat: (  # noqa: E731
        min(p[0] for p in ring) <= lon <= max(p[0] for p in ring)
        and min(p[1] for p in ring) <= lat <= max(p[1] for p in ring))
    check("the west does not contain the east",
          not inside(rings["west"], lon_max - 0.0005, lat_max - 0.0005),
          "west ring reaches the eastern edge")
    check("the north-west quadrant excludes the south-east",
          not inside(rings["north-west"], lon_max - 0.0005, lat_min + 0.0005),
          "north-west ring reaches the south-east corner")
    check("the centre contains the middle", inside(rings["centre"], mid_lon, mid_lat),
          "centre ring misses its own middle")


def test_set_site_is_scoped_to_the_site_it_names() -> None:
    """`set_site` used to apply its height to every building in the district.

    The site id selected a *polygon* in the geometry, and the frontend had no way
    to filter by it, so the only safe-looking reading was "everywhere". The scope
    now carries that polygon, so the id means something.
    """
    g = load_grounding()
    if not g.paving_sites:
        print("      (skipped: no paving sites)")
        return
    out = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "set_site", "site_id": "permeable_paving#0", "height_m": 40}]
    )).propose("set the buildings there to 40 m", g)
    check("one feature produced", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    scope = out[0]["params"]["scope"]
    check("scope kind is the site", scope["kind"] == "site", scope["kind"])
    check("the ring is that site's own polygon",
          scope["ring"] == g.paving_sites[0]["geometry"]["coordinates"][0],
          "ring differs from the catalogue entry")
    check("and it is smaller than the study area",
          len({tuple(p) for p in scope["ring"]})
          < len({tuple(p) for p in _fraction_ring(g.bounds, (0, 0, 1, 1))})
          or scope["ring"] != _fraction_ring(g.bounds, (0, 0, 1, 1)),
          "the site covers everything")
    check("siteId is recorded for provenance", scope["siteId"] == "permeable_paving#0",
          str(scope.get("siteId")))


def test_placement_footprints_are_generated_on_built_up_land_in_scope() -> None:
    """`place` is the one op that creates geometry, so it is checked hardest.

    The footprints must be inside the scope, on land the analysis calls built up,
    of the requested height and count, with ids the viewer can key on -- and they
    must be the system's coordinates, not the model's.
    """
    import numpy as np

    g = load_grounding()
    if g.overlay is None:
        print("      (skipped: no overlay)")
        return
    out = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "place", "count": 3, "height_m": 60, "scope": "east"}],
        rationale="more homes on serviced land",
    )).propose("add three 60 m residential towers in the east", g)
    check("one feature produced", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    params = out[0]["params"]
    placements = params.get("placements") or []
    check("three blocks placed", len(placements) == 3, f"got {len(placements)}")
    check("ids are unique and stable",
          len({b["id"] for b in placements}) == len(placements),
          str([b["id"] for b in placements]))

    scope = params["scope"]["ring"]
    s_west, s_east = min(p[0] for p in scope), max(p[0] for p in scope)
    s_south, s_north = min(p[1] for p in scope), max(p[1] for p in scope)
    check("every footprint lies inside the scope",
          all(s_west <= p[0] <= s_east and s_south <= p[1] <= s_north
              for b in placements for p in b["ring"]),
          "a footprint fell outside the named region")
    check("rings are closed", all(b["ring"][0] == b["ring"][-1] for b in placements),
          "an unclosed ring is not a polygon")
    check("heights are as instructed",
          all(b["heightM"] == 60.0 for b in placements),
          str({b["heightM"] for b in placements}))

    # And on land the analysis actually calls built up.
    lon_min, _, _, lat_max = g.bounds
    cw, ch = _cell_axes(g.overlay)
    paved = g.overlay.class_mask("paved")
    on_paved = True
    for block in placements:
        west = min(p[0] for p in block["ring"])
        east = max(p[0] for p in block["ring"])
        south = min(p[1] for p in block["ring"])
        north = max(p[1] for p in block["ring"])
        c0, c1 = int((west - lon_min) / cw), int((east - lon_min) / cw)
        r0, r1 = int((lat_max - north) / ch), int((lat_max - south) / ch)
        if not paved[max(r0, 0):r1 + 1, max(c0, 0):c1 + 1].any():
            on_paved = False
    check("every footprint sits on ESA WorldCover paved cells", on_paved,
          "a block was placed on land the land cover does not call built up")
    check("basis states where the footprints came from",
          "paved" in params["basis"], params["basis"])


def test_placement_avoids_the_blocks_that_already_exist() -> None:
    """A new tower must not land inside an existing envelope.

    Placement reuses `make_citymodel.build_blocks` on a paved mask with the
    already-occupied cells removed, so the two cannot disagree about what a block
    is. If nothing unbuilt is available the filter is dropped, and `basis` says so
    -- so this only asserts the strong form when the basis claims it.
    """
    from app.config import settings

    path = settings.rasters_dir / "citymodel_hsk.json"
    if not path.exists():
        print("      (skipped: run make_citymodel.py first)")
        return
    existing = {
        json.dumps(f["geometry"]["coordinates"][0])
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]
    }
    g = load_grounding()
    out = HeuristicProvider().propose("add two 70 m towers in the west", g)
    placed = [f for f in out if f["params"].get("op") == "place"]
    if not placed:
        print("      (skipped: no placeable land in the west)")
        return
    params = placed[0]["params"]
    rings = [json.dumps(b["ring"]) for b in params["placements"]]
    if params["basis"].startswith("new massing on unbuilt paved land"):
        check("no placed block duplicates an existing envelope",
              not (set(rings) & existing),
              f"{len(set(rings) & existing)} of {len(rings)} coincide with a block")
    else:
        check("fallback placement declares itself in the basis",
              "existing envelopes excluded where possible" in params["basis"],
              params["basis"])


def test_a_model_cannot_place_a_building_by_coordinate() -> None:
    """Hostile-response test for the one op that creates geometry."""
    g = load_grounding()
    out = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "place", "count": 1, "height_m": 200, "scope": "east",
         # A model that tries to place by coordinate, and one that tries to smuggle
         # a ring past the scope.
         "coordinates": [[[114.05, 22.49]]],
         "ring": [[[113.0, 22.0]]],
         "geometry": {"type": "Polygon", "coordinates": [[[114.05, 22.49]]]}},
    ])).propose("put a 200 m tower at 114.05, 22.49", g)
    check("the op is still honoured, on the analysis's terms", len(out) == 1,
          f"got {len(out)}")
    if not out:
        return
    params = out[0]["params"]
    lon_min, lat_min, lon_max, lat_max = g.bounds
    east = [p[0] for p in params["scope"]["ring"]]
    check("the scope is still the eastern half, not the model's point",
          max(east) == round(lon_max, 7) and min(east) > 113.99,
          f"{min(east)}..{max(east)}")
    check("no footprint is at the model-authored coordinate",
          all(abs(p[0] - 114.05) > 0.001 for b in params["placements"]
              for p in b["ring"]),
          "a placed block used the coordinate the model supplied")


def test_rotate_is_read_scoped_and_bounded() -> None:
    g = load_grounding()
    out = HeuristicProvider().propose("rotate the western blocks 30 degrees", g)
    check("rotate is understood", bool(out) and out[0]["params"]["op"] == "rotate",
          str([f["params"].get("op") for f in out]))
    if out:
        check("and is scoped", out[0]["params"]["scope"]["label"] == "the west",
              out[0]["params"]["scope"]["label"])
        check("with the stated angle", out[0]["params"]["degrees"] == 30.0,
              str(out[0]["params"].get("degrees")))
    # Out of range is dropped, not clamped: a 400 degree turn is a mistake.
    bad = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "rotate", "degrees": 400, "scope": "west"}])).propose("spin", g)
    check("an impossible rotation is dropped", bad == [], f"accepted {len(bad)}")
    # A default angle is declared as a default, not reported as an instruction.
    defaulted = HeuristicProvider().propose("reorient the buildings", g)
    check("a defaulted angle is flagged",
          bool(defaulted) and defaulted[0]["params"].get("angleDefaulted") is True,
          str(defaulted[0]["params"].get("angleDefaulted") if defaulted else "none"))


def test_a_height_change_is_not_read_as_an_absolute_height() -> None:
    """"30 m taller" must not set every building to 30 m.

    This one is worth a test because the failure is invisible: the model changes,
    the panel reports a height, and nothing says the number was the wrong one.
    """
    ops = parse_massing_intent("make the eastern blocks 30 m taller")
    check("exactly one operation", len(ops) == 1, f"got {len(ops)}")
    if not ops:
        return
    check("read as a delta", ops[0].get("deltaM") == 30.0 and "heightM" not in ops[0],
          json.dumps(ops[0]))
    check("and scoped", ops[0]["scope"] == "east", str(ops[0].get("scope")))


def test_the_deterministic_parser_is_not_the_weak_half() -> None:
    """No-key baseline: everything the LLM can do, the parser can do.

    A fallback that quietly loses the headline capability is the worst kind,
    because the demo still runs and just shows less.
    """
    cases = {
        "make the district 25% taller": ("scale_all", "all"),
        "make the western blocks 30% taller": ("scale_all", "west"),
        "set the buildings in the south to 45 m": ("set_site", "south"),
        "make the north-east blocks 30 m taller": ("set_site", "north-east"),
        "rotate the eastern blocks 30 degrees": ("rotate", "east"),
        "move the northern blocks 25 m north": ("move", "north"),
        "add three 60 m residential towers in the eastern part": ("place", "east"),
        "place four 80 m towers in the north-west": ("place", "north-west"),
        "reset the building heights": ("reset", None),
    }
    for text, (op, scope) in cases.items():
        ops = parse_massing_intent(text)
        got = (ops[0]["op"], ops[0].get("scope")) if ops else (None, None)
        check(f"{text!r} -> {op} in {scope}", got == (op, scope), f"got {got}")

    # Things that must still not fire.
    for text in ("add two water reservoirs", "make it nicer",
                 "permeable paving please"):
        check(f"no false positive: {text!r}", parse_massing_intent(text) == [],
              str(parse_massing_intent(text)))


def test_a_model_that_drops_the_array_wrapper_is_still_understood() -> None:
    """`qwen2.5:3b-instruct` answers with the operation, not an array of them.

    The schema asks for ``{"edits": [ ... ]}``; the local model returns
    ``{"op": "place", "count": 3, "scope": "east"}``. Reading ``args["edits"]``
    alone found nothing, so a working model producing a correct answer yielded
    zero features and a green tick in the panel. Found by running it, not by
    reading it.
    """
    def _flat(url: str, api_key: str, payload: dict) -> dict:
        return {
            "id": "msg_flat", "type": "message", "role": "assistant",
            "content": [{
                "type": "tool_use", "id": "tu_flat", "name": MASSING_TOOL["name"],
                "input": {"op": "scale_all", "multiplier": 1.4, "scope": "west"},
            }],
            "stop_reason": "tool_use",
        }

    out = LLMProvider(api_key="k", transport=_flat).propose(
        "make the western blocks 40% taller", load_grounding())
    check("a flattened answer is still read", len(out) == 1, f"got {len(out)}")
    if out:
        check("with its scope and multiplier",
              out[0]["params"]["multiplier"] == 1.4
              and out[0]["params"]["scope"]["region"] == "west",
              json.dumps(out[0]["params"]["scope"]))


def test_the_structured_fallback_cannot_answer_the_other_question() -> None:
    """A massing instruction must never come back as interventions.

    The JSON-schema retry is asked to satisfy the *massing* schema, and a small
    model fills in the intervention shape anyway -- ``selections`` with real,
    valid catalogue ids. The parser believed it, and "add three 60 m towers in
    the east" produced three water reservoirs: a complete, plausible, wrong plan
    for a question nobody asked. This is the failure mode this project treats as
    the worst one there is, so it is pinned.
    """
    seen: list[dict] = []

    def _no_tools_then_wrong_shape(url: str, api_key: str, payload: dict) -> dict:
        seen.append(payload)
        if payload.get("tools"):
            return {"choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": None}}]}
        return {
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps({
                    "selections": [{"site_id": "water_reservoir#0",
                                    "intervention_type": "water_reservoir",
                                    "rationale": "the deepest basin"}],
                })},
            }],
        }

    provider = OpenAICompatProvider(base_url="http://local.invalid/v1", api_key="k",
                                    model="m")
    provider._transport = _no_tools_then_wrong_shape  # type: ignore[assignment]
    features = provider.propose("add three 60 m residential towers in the east",
                                load_grounding())
    check("the schema retry did happen", len(seen) == 2, f"{len(seen)} requests")
    check("the retry enforced the massing schema",
          (seen[1].get("response_format") or {}).get("json_schema", {}).get("name")
          == MASSING_TOOL["name"],
          json.dumps(seen[1].get("response_format")) if len(seen) > 1 else "no retry")
    check("and the wrong-shaped answer was refused, not believed",
          features == [], f"produced {len(features)} intervention(s) instead")


def test_move_is_read_as_an_offset_in_metres() -> None:
    """"Move the western blocks 30 m north" -- and it composes, it does not compound.

    The offset is resolved here rather than left as a bearing, because "30 m
    north" has to be the same number whatever a block's orientation is, and a
    compass angle is exactly the kind of thing a model will invent.
    """
    ops = parse_massing_intent("move the western blocks 30 m north")
    check("move is understood", bool(ops) and ops[0]["op"] == "move",
          str(parse_massing_intent("move the western blocks 30 m north")))
    if not ops:
        return
    check("as a north-positive offset",
          ops[0].get("dyM") == 30.0 and ops[0].get("dxM") == 0.0,
          json.dumps(ops[0]))
    check("and scoped", ops[0]["scope"] == "west", str(ops[0].get("scope")))

    ops_east = parse_massing_intent("shift the eastern blocks 40 m east")
    check("east is east-positive",
          bool(ops_east) and ops_east[0].get("dxM") == 40.0
          and ops_east[0].get("dyM") == 0.0,
          json.dumps(ops_east))

    g = load_grounding()
    out = HeuristicProvider().propose("move the western blocks 30 m north", g)
    check("produces a move feature",
          bool(out) and out[0]["params"]["op"] == "move",
          str([f["params"].get("op") for f in out]))

    # A distance with no direction is not a move, and neither is a move of zero.
    check("a distance with no direction yields nothing",
          parse_massing_intent("move the buildings 30 m") == [],
          str(parse_massing_intent("move the buildings 30 m")))
    dropped = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "move", "direction": "north", "distance_m": 0, "scope": "west"}],
    )).propose("nudge them", g)
    check("a zero-distance move is dropped", dropped == [], f"{len(dropped)} accepted")

    # direction + distance_m is the same request as dx/dy, and must be honoured
    # rather than read as a move with no offset at all.
    both = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "move", "direction": "east", "distance_m": 25, "scope": "west"}],
    )).propose("shift the west 25 m east", g)
    check("direction + distance is accepted as an offset",
          bool(both) and both[0]["params"]["dxM"] == 25.0
          and both[0]["params"]["dyM"] == 0.0,
          json.dumps(both[0]["params"]) if both else "no feature")

    # A compass word outranks the model's own arithmetic. This is not
    # hypothetical: qwen2.5:3b-instruct answered "40 m north" with dx=40, dy=0,
    # transposing the axes into a plausible pair of numbers pointing the wrong way.
    transposed = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "move", "scope": "west", "dx_m": 40, "dy_m": 0,
         "direction": "north", "distance_m": 40},
    ])).propose("move the western blocks 40 m north", g)
    check("a named direction overrides transposed dx/dy",
          bool(transposed) and transposed[0]["params"]["dyM"] == 40.0
          and transposed[0]["params"]["dxM"] == 0.0,
          json.dumps(transposed[0]["params"]) if transposed else "no feature")


def test_a_named_region_outranks_a_stray_site_id() -> None:
    """A model fills in every schema field, so a placeholder id arrives too.

    `qwen2.5:3b-instruct` sent `scope: "west"` *and* `site_id: "road#0"` for "move
    the western blocks". Site-id precedence answered a different question and moved
    all 2,639 buildings. The region the instruction named is the deliberate choice.
    """
    g = load_grounding()
    out = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "move", "scope": "west", "site_id": "road#0",
         "dx_m": 0, "dy_m": 40},
    ])).propose("move the western blocks 40 m north", g)
    check("one feature", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    scope = out[0]["params"]["scope"]
    check("the named region won",
          scope["kind"] == "region" and scope["region"] == "west",
          json.dumps(scope))
    check("the stray id is still recorded for provenance",
          scope["siteId"] == "road#0", str(scope.get("siteId")))

    # ...and with no region named, a site id is still the scope.
    site_only = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "scale_all", "multiplier": 1.2, "site_id": "permeable_paving#0"},
    ])).propose("make it taller", g)
    check("a site id alone still scopes",
          bool(site_only) and site_only[0]["params"]["scope"]["kind"] == "site",
          json.dumps(site_only[0]["params"]["scope"]) if site_only else "none")

    # An unknown region is still fatal, even with a valid site id alongside it.
    mixed = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "scale_all", "multiplier": 1.2, "scope": "the moon",
         "site_id": "road#0"},
    ])).propose("do something", g)
    check("an unknown region is still refused", mixed == [], f"{len(mixed)} accepted")


def test_an_irrelevant_field_cannot_invalidate_an_operation() -> None:
    """A model fills in the schema, so it sends fields the op does not use.

    `qwen2.5:3b-instruct` answered a move with `height_m: 0` alongside a correct
    `dx_m`/`dy_m`. A shared range check on every field then rejected the whole
    operation, and "move the western blocks 40 m north" produced nothing at all.
    Validation is per-operation for exactly this reason.
    """
    g = load_grounding()
    noisy = LLMProvider(api_key="k", transport=massing_transport([
        {"op": "move", "scope": "west", "height_m": 0, "count": 999,
         "degrees": 9999, "dx_m": 40, "dy_m": 0},
    ])).propose("move the western blocks 40 m north", g)
    check("the operation survives unrelated fields", len(noisy) == 1,
          f"got {len(noisy)}")
    if noisy:
        check("with its offset intact",
              noisy[0]["params"]["dxM"] == 40.0 and noisy[0]["params"]["dyM"] == 0.0,
              json.dumps(noisy[0]["params"]))

    # ...but a field the op *does* use is still enforced, and a contradiction is
    # still a contradiction.
    cases = (
        # (payload, expected feature count, why)
        ({"op": "scale_all", "multiplier": 1.2, "height_m": 5000}, 1,
         "height_m is irrelevant to scale_all"),
        ({"op": "rotate", "degrees": 20, "dx_m": 9000}, 1,
         "dx_m is irrelevant to rotate"),
        ({"op": "move", "dx_m": 40, "degrees": 9999}, 1,
         "degrees is irrelevant to move"),
        ({"op": "set_site", "height_m": 40, "delta_m": 10}, 0,
         "an absolute height and a delta contradict each other"),
        ({"op": "rotate", "degrees": 400}, 0, "400 degrees is not a rotation"),
        ({"op": "move", "dx_m": 4000}, 0, "4 km is not a plausible shift"),
        ({"op": "scale_all", "multiplier": 500}, 0, "x500 is not a scenario"),
    )
    for payload, expected, why in cases:
        out = LLMProvider(api_key="k", transport=massing_transport(
            [payload])).propose("do something to the buildings", g)
        check(f"{why} ({json.dumps(payload)[:44]})", len(out) == expected,
              f"got {len(out)}, expected {expected}")


def test_an_unrecognised_scope_is_dropped_not_widened() -> None:
    """The bug this whole mechanism exists to prevent, asserted directly.

    A scope we do not recognise must produce no edit. Falling back to "the whole
    study area" would answer a scoped question with an unscoped one, which is the
    silent-wrong-answer failure mode rather than an honest failure.
    """
    g = load_grounding()
    check("a nonsense region resolves to nothing",
          resolve_scope("the far side of the moon", None, g) is None,
          "it silently became a scope")
    out = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "scale_all", "multiplier": 2.0, "scope": "the far side of the moon"}],
    )).propose("make the far side of the moon twice as tall", g)
    check("so the op is dropped", out == [], f"accepted {len(out)}")
    # ...while a hyphenated compound region still works, since that is a real one.
    ok = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "scale_all", "multiplier": 0.9, "scope": "north-west"}],
    )).propose("make the north-west 10% shorter", g)
    check("a hyphenated compound region still resolves",
          bool(ok) and ok[0]["params"]["scope"]["region"] == "north-west",
          str(ok[0]["params"]["scope"] if ok else "no feature"))


def test_massing_tool_is_offered_alongside_the_site_tool() -> None:
    """`tool_choice: any` weakens the forced tool, so both must be offered."""
    capture: dict = {}
    provider = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "reset"}], capture=capture))
    provider.propose("reset the heights", load_grounding())
    names = [t["name"] for t in capture["payload"]["tools"]]
    check("only the massing tool is offered for a height instruction",
          names == ["modify_massing"], str(names))
    check("and it is forced",
          capture["payload"]["tool_choice"] == {"type": "tool", "name": "modify_massing"},
          json.dumps(capture["payload"]["tool_choice"]))


def test_massing_ops_are_validated_not_guessed() -> None:
    """An incomplete or oversized op is dropped, not coerced to a default."""
    g = load_grounding()
    bad = [
        {"op": "scale_all"},                                  # no multiplier
        {"op": "scale_all", "multiplier": 500},               # out of range
        {"op": "levitate"},                                   # unknown op
        {"op": "set_site", "site_id": "nope#0", "height_m": 40},   # unknown site
        {"op": "set_site", "site_id": "green_space#0"},        # no height
    ]
    for edit in bad:
        provider = LLMProvider(api_key="k", transport=massing_transport([edit]))
        out = provider.propose("do something to the buildings", g)
        check(f"rejected: {json.dumps(edit)[:44]}", out == [],
              f"accepted {len(out)}")


def test_heuristic_reads_massing_instructions_too() -> None:
    """The no-key fallback must not silently lose the headline capability."""
    g = load_grounding()
    cases = {
        "make the district 25% taller": 1.25,
        "make the buildings 1.4x higher": 1.4,
        "lower the buildings by 20%": 0.8,
        "make it 30% shorter": 0.7,
    }
    for text, expected in cases.items():
        ops = parse_massing_intent(text)
        got = ops[0]["multiplier"] if ops else None
        check(f"{text!r} -> x{expected}", got == expected, f"got {got}")

    out = HeuristicProvider().propose("make the district 25% taller", g)
    check("produces a massing feature",
          bool(out) and out[0]["intervention_type"] == "massing_height",
          str([f["intervention_type"] for f in out]))

    reset = HeuristicProvider().propose("reset the building heights", g)
    check("reset is understood",
          bool(reset) and reset[0]["params"]["op"] == "reset",
          str([f["params"].get("op") for f in reset]))

    # And it must not fire on unrelated instructions.
    for text in ("add two water reservoirs", "make it nicer"):
        check(f"no false positive: {text!r}", parse_massing_intent(text) == [],
              str(parse_massing_intent(text)))


def test_stored_massing_features_are_drafts() -> None:
    """The service, not the provider, assigns status -- and it must be draft."""
    from app import db
    from app.config import settings

    checked = 0
    for path in db.list_docs(settings.plans_dir):
        doc = db.read_doc(path)
        if not doc:
            continue
        for item in doc.get("interventions", []):
            if item.get("intervention_type") != "massing_height":
                continue
            check("stored massing edit is draft", item.get("status") == "draft",
                  str(item.get("status")))
            check("stored massing edit records its op", bool(item["params"].get("op")),
                  "no op recorded")
            checked += 1
    if not checked:
        print("      (no massing plans stored yet; skipped)")


def test_http_round_trip_against_a_spec_shaped_server() -> None:
    """The transport really opens a socket, and really parses what comes back.

    Until now the LLM path had only ever been driven through an injected
    transport, so the one part nobody had executed was the part most likely to
    break on demo day: urllib, the headers, the JSON body, and the response
    shape. A recorded fixture cannot catch a typo in any of those.

    So this stands up a real HTTP server on localhost that speaks the **actual
    Anthropic Messages response shape**, points the real ``_post_json`` at it,
    and asserts grounded features come out the other end.

    What this does and does not prove, stated plainly:

    - **Does:** the socket, the method, the headers, the JSON round trip, the
      tool-use parsing, and the grounding checks all work over real HTTP. Those
      are the parts that are our code.
    - **Does not:** that a real model produces good selections. That needs a real
      model, and a spec-shaped server is not one.

    It also covers the OpenAI-compatible transport, which is the route to a live
    run with no vendor key.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _read(self):
            length = int(self.headers.get("Content-Length", "0"))
            return _json.loads(self.rfile.read(length) or b"{}")

        def do_POST(self):  # noqa: N802
            body = self._read()
            seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
            # Branch on the *body*, not the path, and on a key that genuinely
            # differs. Two earlier attempts were wrong: branching on the URL broke
            # when the host was rewritten to localhost, and branching on
            # "messages" broke because the Anthropic payload has `messages` too.
            # Only a top-level `system` is unique to the Anthropic shape.
            if "system" in body:
                payload = {
                    "id": "msg_live", "type": "message", "role": "assistant",
                    "content": [{
                        "type": "tool_use", "id": "tu_1",
                        "name": "modify_massing",
                        "input": {"edits": [{"op": "scale_all", "multiplier": 1.25}],
                                  "rationale": "live round trip"},
                    }],
                    "stop_reason": "tool_use",
                }
            else:
                payload = {
                    "id": "chatcmpl-live", "object": "chat.completion",
                    "model": body.get("model", "?"),
                    "choices": [{
                        "index": 0, "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "id": "call_1", "type": "function",
                                "function": {
                                    "name": "modify_massing",
                                    "arguments": _json.dumps({
                                        "edits": [{"op": "scale_all",
                                                   "multiplier": 1.5}],
                                        "rationale": "live openai-compatible",
                                    }),
                                },
                            }],
                        },
                    }],
                }
            raw = _json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        g = load_grounding()

        # --- Anthropic shape, real HTTP, real transport --------------------- #
        provider = LLMProvider(api_key="test-key",
                               transport=lambda url, key, body: _post_json(
                                   url.replace("https://api.anthropic.com",
                                                f"http://127.0.0.1:{port}"), key, body))
        out = provider.propose("make the district 25% taller", g)
        check("anthropic round trip returned a feature", len(out) == 1, f"got {len(out)}")
        if out:
            check("anthropic feature is massing_height",
                  out[0]["intervention_type"] == "massing_height",
                  out[0]["intervention_type"])
            check("anthropic multiplier survived the round trip",
                  out[0]["params"]["multiplier"] == 1.25,
                  str(out[0]["params"]["multiplier"]))
        # HTTP header names are case-insensitive and the server normalises them,
        # so compare lowercased rather than assuming the exact casing we sent.
        sent = [{k.lower(): v for k, v in r["headers"].items()} for r in seen]
        check("x-api-key header was sent",
              any(h.get("x-api-key") == "test-key" for h in sent),
              f"headers seen: {[sorted(h) for h in sent]}")
        check("anthropic-version header was sent",
              any(h.get("anthropic-version") == "2023-06-01" for h in sent),
              f"headers seen: {[sorted(h) for h in sent]}")

        # --- OpenAI-compatible shape over the same socket ------------------- #
        seen.clear()
        compat = OpenAICompatProvider(
            base_url=f"http://127.0.0.1:{port}/v1", api_key="k", model="local-model",
        )
        out2 = compat.propose("make the district 50% taller", g)
        check("openai-compatible round trip returned a feature", len(out2) == 1,
              f"got {len(out2)}")
        if out2:
            check("openai-compatible multiplier survived",
                  out2[0]["params"]["multiplier"] == 1.5,
                  str(out2[0]["params"]["multiplier"]))
        check("both tools were sent in OpenAI function form",
              any(all(t.get("type") == "function" for t in r["body"].get("tools", []))
                  for r in seen), "tools were not OpenAI-shaped")
    finally:
        server.shutdown()
        server.server_close()


def test_tool_routing_is_decided_in_code_not_by_the_model() -> None:
    """The model is offered one tool, chosen deterministically.

    Both tools used to be advertised with a permissive `tool_choice`, on the
    reasoning that a system-prompt rule would be enough. A real model settled
    it: asked "make the district 25% taller to reduce flood risk" with both
    tools available, qwen2.5:3b-instruct returned no tool call at all and the
    feature was lost silently. Offering one tool is a guarantee; a prompt rule
    is a request.
    """
    for text, expected in (
        ("make the district 25% taller", "modify_massing"),
        ("lower the buildings by 20%", "modify_massing"),
        ("reset the building heights", "modify_massing"),
        ("add two water reservoirs", "select_intervention_sites"),
        ("permeable paving please", "select_intervention_sites"),
    ):
        tools, forced = route_tools(text)
        check(f"{text!r} -> {expected}",
              forced == expected and [t["name"] for t in tools] == [expected],
              f"got {[t['name'] for t in tools]}")


def test_grounding_survives_a_hostile_response() -> None:
    """A real model can ignore the catalogue. The validation must still hold."""
    g = load_grounding()
    provider = LLMProvider(api_key="k", transport=recorded_transport([
        # A model inventing coordinates in the rationale, and a site it was
        # never offered.
        {"site_id": "water_reservoir#0", "intervention_type": "water_reservoir",
         "rationale": "I placed it at 113.9999, 22.4401 because it looked right"},
    ]))
    out = provider.propose("add a reservoir", g)
    check("the real site won, not the model's coordinates",
          bool(out) and out[0]["geometry"] == g.reservoir_sites[0]["geometry"],
          "geometry came from somewhere other than the grounding")
    if out:
        check("model-authored coordinates stayed in the rationale as prose",
              "113.9999" in out[0]["params"]["rationale"],
              "rationale was rewritten rather than preserved")


def _run() -> int:
    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"{name} raised {exc}")
            print(f"  FAIL  {name} raised {exc}")
    failed_names = {f.split(":")[0] for f in FAILURES}
    print(f"\n{len(tests) - len(failed_names)}/{len(tests)} passed")
    return 1 if FAILURES else 0




def test_the_fit_tool_asks_for_an_objective_and_never_a_number() -> None:
    """The point of Option A: the model states intent, the ledger does the maths.

    `modify_massing` asks the model for numbers, and a 3.1B model has already got
    four of them wrong in one afternoon. `assess_building_fit` asks for an
    objective and a set of constraints -- no multiplier, no height, no area --
    and `app/services/massing_fit.py` computes the rest from the storage ledger.
    """
    tool = next(t for t in (MASSING_TOOL, FIT_TOOL) if t["name"] == "assess_building_fit")
    schema = tool["input_schema"]
    props = set(schema["properties"])

    check("the fit tool takes an objective",
          "objective" in props and schema["required"] == ["objective"],
          str(schema["required"]))
    forbidden = {"multiplier", "height_m", "delta_m", "degrees", "count",
                 "dx_m", "dy_m", "distance_m"}
    check("and offers no field that applies a number",
          not (props & forbidden),
          f"leaked: {sorted(props & forbidden)}" if props & forbidden else
          "objective + constraints only")

    check("the objective is a closed enum",
          set(schema["properties"]["objective"]["enum"]) ==
          {"reduce_flood", "reduce_runoff", "reduce_heat", "balanced"},
          str(schema["properties"]["objective"]["enum"]))


def test_a_recommendation_request_routes_to_the_fit_tool() -> None:
    for text in ("make this building better for flood risk",
                 "what should I do with this block",
                 "optimise the western blocks for heat",
                 "make it better for the environment"):
        tools, forced = route_tools(text)
        check(f"'{text[:38]}' routes to the fit tool",
              forced == "assess_building_fit" and len(tools) == 1, forced)

    # A stated number is a decision, not a question, and must keep going to the
    # editing tool. Losing this is how "set it to 60 m" turns into "here are some
    # options you did not ask for".
    for text in ("set it to 60 m", "make the district 25% taller",
                 "rotate the northern blocks 20 degrees"):
        _tools, forced = route_tools(text)
        check(f"'{text[:38]}' stays an edit", forced == "modify_massing", forced)


def test_fit_constraints_default_to_conservative_and_parse_goals() -> None:
    flood = parse_fit_intent("make this building better for flood risk")
    check("flood risk reads as reduce_flood",
          flood and flood["objective"] == "reduce_flood", str(flood))
    heat = parse_fit_intent("optimise the western blocks for heat")
    check("heat reads as reduce_heat",
          heat and heat["objective"] == "reduce_heat", str(heat))
    vague = parse_fit_intent("make it better for the environment")
    check("an unstated objective becomes 'balanced', never a guess at one",
          vague and vague["objective"] == "balanced", str(vague))

    check("green is protected unless permission to trade it is explicit",
          flood and flood["constraints"]["no_net_green_loss"] is True,
          "a planner who did not mention green has not permitted losing it")
    permitted = parse_fit_intent(
        "make it better for flood risk even if it means losing some green")
    check("explicit permission is honoured",
          permitted and permitted["constraints"]["no_net_green_loss"] is False,
          str(permitted))

    check("height changes stay off unless asked for",
          flood and flood["constraints"]["allow_height_change"] is False,
          str(flood))
    heights = parse_fit_intent("what height best reduces flood risk here")
    check("and turn on when the instruction mentions height",
          heights and heights["constraints"]["allow_height_change"] is True,
          str(heights))


if __name__ == "__main__":
    raise SystemExit(_run())
