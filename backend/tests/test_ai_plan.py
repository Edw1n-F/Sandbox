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

from app.services.ai_plan import (  # noqa: E402
    ANTHROPIC_URL,
    CHOOSE_TOOL,
    MASSING_TOOL,
    parse_massing_intent,
    HeuristicProvider,
    LLMProvider,
    PlanGenerationError,
    build_prompt,
    get_provider,
    load_grounding,
    parse_intent,
)

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
    check("both tools are offered",
          [t["name"] for t in capture["payload"]["tools"]]
          == [CHOOSE_TOOL["name"], MASSING_TOOL["name"]],
          str([t["name"] for t in capture["payload"]["tools"]]))
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
    check("auto picks the heuristic with no key",
          isinstance(get_provider("auto"), HeuristicProvider),
          type(get_provider("auto")).__name__)
    try:
        get_provider("llm")
        check("explicit llm without a key raises", False, "no error")
    except PlanGenerationError:
        check("explicit llm without a key raises", True)


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


def test_llm_massing_op_becomes_a_grounded_draft_feature() -> None:
    """"Make the district 25% taller" -> one massing_height feature, multiplier 1.25.

    The acceptance test from the gap brief. Checks source, status, the multiplier,
    and that the geometry is a real closed ring rather than an invented shape.
    """
    g = load_grounding()
    provider = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "scale_all", "multiplier": 1.25}]))
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
    check("carries no model-authored coordinate",
          "coordinates" not in json.dumps(f["params"])[:200]
          or "siteId" in f["params"], "params leaked a coordinate")


def test_massing_tool_is_offered_alongside_the_site_tool() -> None:
    """`tool_choice: any` weakens the forced tool, so both must be offered."""
    capture: dict = {}
    provider = LLMProvider(api_key="k", transport=massing_transport(
        [{"op": "reset"}], capture=capture))
    provider.propose("reset the heights", load_grounding())
    names = [t["name"] for t in capture["payload"]["tools"]]
    check("both tools offered", names == ["select_intervention_sites", "modify_massing"],
          str(names))
    check("tool_choice is any", capture["payload"]["tool_choice"] == {"type": "any"},
          json.dumps(capture["payload"]["tool_choice"]))
    system = capture["payload"]["system"]
    check("system prompt disambiguates the two intents",
          "modify_massing" in system and "height" in system.lower(),
          "no routing rule in the system prompt")


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


if __name__ == "__main__":
    raise SystemExit(_run())
