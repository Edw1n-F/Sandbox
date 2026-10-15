"""Tests for the proposal contract (the plan-v2 design loop).

Three "to verify" items from the todo, plus the constraints gao is still
designing:

1. **Every intervention type generates correctly.** Each kind the ledger knows
   (`sandbox_model.INTERVENTION_EFFECTS`) and each kind the proposal path can
   emit (`ai_plan.POOL_FOR_KIND`, the tool-schema enum) is driven through the
   real pipeline: a proposal round-trips `POST /api/scenes/{id}/ai-plan`
   (`apply=false`) as a valid `InterventionProposal` whose geometry is inside
   the study area, and applying it through `apply_interventions` yields either a
   grid effect or an explicit ``skipped`` with a reason -- never a silent
   nothing.

2. **Routing: proposals must not come back as result cards.** The backend now
   classifies every turn with `app.agent.intent.classify_turn` (DESIGN §2.5), so
   the routing authority is a pure function a test can drive directly. A table of
   proposal-shaped versus question-shaped instructions is driven through it, the
   two chat endpoints are compared, and the client's fast-path vocabulary is
   checked against the server's. Phrasings that still mis-route are reported as
   FINDINGs (never fixed here).

3. **Simulation of applied plans.** A confirmed proposal set is applied through
   `POST /ai-plan/apply`, the ledger is run over the resulting plan, and it is
   asserted that the plan moves the drainage axis (signed deltas, baseline
   beside scenario) and that **no combined score or verdict field exists
   anywhere** in the response -- the assertion pattern from `tests/test_suggest.py`.

Constraints that are NOT yet implemented (building-avoidance, overlap
prevention, size limits) are written as ``test_PENDING_*``: they document the
intended contract and are expected to fail until gao lands them.

Run:  ./backend/.venv/bin/python tests/test_proposal_contract.py
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

from app.agent import chat_service  # noqa: E402
from app.agent.answer import template_card  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_plan  # noqa: E402
from app.services.sandbox_model import (  # noqa: E402
    INTERVENTION_EFFECTS,
    MAX_INTERVENTION_COVERAGE,
    _polygon_cells,
    ledger_for_scenario,
    load_grids,
)

client = TestClient(app)

# The client's fast path and its single source. `looksLikeIntervention` lives in
# `intentVocabulary.ts` (imported by ChatPanel.tsx); the fixture is generated from
# `app/agent/intent.py`, so the two cannot drift.
INTENT_VOCAB_TS = (BACKEND_DIR.parent / "frontend" / "src" / "api"
                   / "intentVocabulary.ts")
CONTRACT_VOCAB = BACKEND_DIR.parent / "contracts" / "intent_vocabulary.json"

# A small square inside the study area, used to probe the ledger's effect per
# kind. It sits on the same ground `test_pins` uses; the ledger converts real
# cells, so a kind with a land-cover effect moves it.
PROBE_POLY = {
    "type": "Polygon",
    "coordinates": [[
        [113.9935, 22.4318], [113.9942, 22.4318],
        [113.9942, 22.4325], [113.9935, 22.4325], [113.9935, 22.4318],
    ]],
}

#: Canonical, in-vocabulary intervention phrasings: every one must reach the
#: gate. The verbs span the design's §2.5 set (including the ones the client
#: regex used to drop -- `dig`, `create`, `grow`, `retain`); the nouns are
#: `ai_plan.EN_ALIASES`, which is the one vocabulary `classify_turn` reads.
CANONICAL_PROPOSALS: list[str] = [
    "add a water reservoir in the north",
    "add green space in the centre",
    "add permeable paving in the north",
    "build a retention basin in the south",
    "dig a retention basin in the north",
    "plant a park in the east",
    "grow a park in the east",
    "fill a storage basin in the south",
    "install permeable pavement in the centre",
    "convert the road to permeable paving in the north",
    "retrofit drainage in the south",
    "expand the green space in the north",
    "increase green space in the west",
    "put a water reservoir in the east",
    "place a pond in the north",
    "create a swale in the south",
    "add a channel in the east",
    "add a street in the north",
    "add a railing in the west",
    "remove the drainage in the north",
    "delete the road in the south",
    "drop the green space in the west",
    "take out the permeable paving in the centre",
]

#: Proposal-shaped phrasings the router currently MISSES. They read as
#: interventions to a person, but `classify_turn`'s noun list is
#: `ai_plan.EN_ALIASES` matched whole-word, so a plural noun or a synonym falls
#: through to "ambiguous" (a result card). Reported by
#: `test_proposal_shaped_phrasings_all_reach_the_gate`, never fixed here.
PROPOSAL_SHAPED_GAPS: list[str] = [
    "add two water reservoirs in the north",        # plural noun
    "plant a green corridor along the riverfront",  # synonym (not in EN_ALIASES)
    "put a permeable plaza in the centre",          # synonym
    "dig a basin in the north",                     # synonym (bare "basin")
    "create a wetland in the east",                 # synonym
]

#: Question phrasings: none may be dragged to the confirm gate.
QUESTION_TABLE: list[str] = [
    "what is the deficit today?",
    "how many buildings are in the study area?",
    "why did the runoff increase?",
    "which project helps most?",
]

# The kinds whose effect is intentionally not on the land-class grid.
NO_GRID_EFFECT_KINDS = {"drainage_alignment", "massing_height"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _grids():
    grids = load_grids()
    assert grids is not None, "the study-area rasters must load"
    return grids


def _bounds() -> tuple[float, float, float, float]:
    return _grids().bounds


def _fresh_scene() -> str:
    lo0, la0, lo1, la1 = _bounds()
    return client.post("/api/scenes", json={
        "name": "proposal-contract",
        "center": {"type": "Point", "coordinates": [(lo0 + lo1) / 2, (la0 + la1) / 2]},
        "bbox": {"type": "Polygon", "coordinates": [[
            [lo0, la0], [lo1, la0], [lo1, la1], [lo0, la1], [lo0, la0]]]},
    }).json()["sceneId"]


def _grounded_scene() -> str | None:
    """The seeded scene that carries the analysis rasters, or None.

    The heuristic proposer needs grounding pools derived from the DEM / land
    cover / wet grid; only the seeded scene has them. A suite that cannot find
    it skips the endpoint cases rather than failing on the environment.
    """
    for path in settings.scenes_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("wet_grid_path") or doc.get("dem_path"):
            return doc.get("id") or path.stem
    return None


def _flatten(geometry: dict) -> list[list[float]]:
    out: list[list[float]] = []

    def walk(node):
        if (isinstance(node, list) and len(node) >= 2
                and all(isinstance(v, (int, float)) for v in node[:2])):
            out.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk((geometry or {}).get("coordinates"))
    return out


def _in_bounds(geometry: dict, bounds) -> bool:
    lo0, la0, lo1, la1 = bounds
    eps = 1e-9
    return all(
        lo0 - eps <= x <= lo1 + eps and la0 - eps <= y <= la1 + eps
        for x, y in _flatten(geometry)
    )


def _propose(scene_id: str, instruction: str, **extra) -> dict:
    body = {"instruction": instruction, "provider": "heuristic", "apply": False}
    body.update(extra)
    resp = client.post(f"/api/scenes/{scene_id}/ai-plan", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _apply(scene_id: str, instruction: str, proposals: list[dict],
           provider: str) -> dict:
    resp = client.post(f"/api/scenes/{scene_id}/ai-plan/apply", json={
        "instruction": instruction,
        "provider": provider,
        "proposals": proposals,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ledger(scene_id: str, plan_id: str) -> dict:
    resp = client.get(f"/api/scenes/{scene_id}/ledger", params={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _forbidden_key_hits(node, path: str = "") -> list[str]:
    """Every key that reads like a cross-axis judgement, recursively.

    The same forbidden vocabulary `tests/test_suggest.py` uses: the three axes
    are never collapsed into one number, and no response may carry a verdict.
    """
    forbidden = ("score", "combined", "verdict", "rank", "grade", "best")
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if any(word in key.lower() for word in forbidden):
                hits.append(f"{path}.{key}")
            hits.extend(_forbidden_key_hits(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_forbidden_key_hits(value, f"{path}[{i}]"))
    return hits


def _probe_feature(kind: str) -> dict:
    return {"id": f"probe-{kind}", "intervention_type": kind,
            "geometry": PROBE_POLY, "params": {}}


# --------------------------------------------------------------------------- #
# 1. Every intervention type generates correctly
# --------------------------------------------------------------------------- #


def test_every_effect_kind_is_applied_or_skipped_with_a_reason() -> None:
    """No kind the ledger knows may produce a silent nothing."""
    grids = _grids()
    silent: list[str] = []
    for kind in sorted(INTERVENTION_EFFECTS):
        result = ledger_for_scenario(grids, [_probe_feature(kind)], rainfall_mm=450.0)
        applied = result["interventionsApplied"]
        skipped = result["interventionsSkipped"]
        if len(applied) + len(skipped) != 1:
            silent.append(f"{kind}: applied={len(applied)} skipped={len(skipped)}")
            continue
        if skipped and not skipped[0].get("why"):
            silent.append(f"{kind}: skipped without a reason")
    assert not silent, (
        "kinds with no effect and no reason (a silent nothing): " + "; ".join(silent))


def test_no_grid_effect_kinds_are_reported_and_explained() -> None:
    """drainage_alignment / massing_height are refused with a stated reason;
    station_massing is *applied* (its effect is in the massing rasters)."""
    grids = _grids()
    for kind in NO_GRID_EFFECT_KINDS:
        result = ledger_for_scenario(grids, [_probe_feature(kind)], rainfall_mm=450.0)
        assert not result["interventionsApplied"], f"{kind} should not touch the grid"
        assert result["interventionsSkipped"], f"{kind} must be reported as skipped"
        assert result["interventionsSkipped"][0]["why"], f"{kind} needs a reason"

    station = ledger_for_scenario(grids, [_probe_feature("station_massing")],
                                  rainfall_mm=450.0)
    assert station["interventionsApplied"], (
        "station_massing is geometry_only and must be applied, not skipped")
    assert station["interventionsApplied"][0]["cellsChanged"] == 0


def test_every_proposal_kind_round_trips_through_ai_plan() -> None:
    """Every kind the proposal path can emit returns a valid, in-bounds proposal."""
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    bounds = _bounds()
    instructions = {
        "water_reservoir": "add a water reservoir in the north",
        "green_space": "add green space in the centre",
        "permeable_paving": "add permeable paving in the north",
        "drainage": "add drainage in the north",
        "road": "add road in the north",
    }
    for kind in sorted(ai_plan.POOL_FOR_KIND):
        body = _propose(scene_id, instructions[kind])
        proposals = body["proposals"]
        assert proposals, f"{kind}: the proposal path emitted nothing"
        for proposal in proposals:
            assert proposal["interventionType"], "proposal has no interventionType"
            assert proposal["geometry"]["type"] == "Polygon", proposal["geometry"]
            assert _in_bounds(proposal["geometry"], bounds), (
                f"{kind}: proposal geometry escapes the study area")


def test_every_proposal_kind_has_a_defined_ledger_effect() -> None:
    """A kind the proposal path can emit must not be a stranger to the ledger.

    A proposal that applies to nothing is worse than no proposal: the user
    confirms it, and the run reports the same numbers. FINDING: `drainage` and
    `road` are in `POOL_FOR_KIND` (so the model can propose them) but absent
    from `INTERVENTION_EFFECTS`, so applying them is skipped as "no land-cover
    effect is defined for this intervention type".
    """
    grids = _grids()
    strangers: list[str] = []
    for kind in sorted(ai_plan.POOL_FOR_KIND):
        result = ledger_for_scenario(grids, [_probe_feature(kind)], rainfall_mm=450.0)
        if result["interventionsApplied"]:
            continue
        why = (result["interventionsSkipped"] or [{}])[0].get("why", "")
        if "no land-cover effect is defined" in why:
            strangers.append(kind)
    assert not strangers, (
        "proposal-emittable kinds the ledger cannot apply at all: "
        f"{strangers} (add them to INTERVENTION_EFFECTS or remove them from "
        "POOL_FOR_KIND)")


# --------------------------------------------------------------------------- #
# 2. Routing: proposals must not come back as result cards
# --------------------------------------------------------------------------- #


def _facts() -> dict:
    return {"inputs": {"rainfallMm": 450.0},
            "scenario": {"unstoredM3": 1000.0, "wetAreaHa": 5.0, "heatBlendedMean": 0.5},
            "baseline": {"unstoredM3": 1000.0, "wetAreaHa": 5.0, "heatBlendedMean": 0.5},
            "delta": {"unstoredM3": 0.0, "wetAreaHa": 0.0, "heatBlendedMean": 0.0}}


def _blank_card(intent: str = "question", hint: str | None = None):
    from app.agent.answer import AnswerCard, Confidence
    return AnswerCard(
        title="t", sentence="s",
        confidence=Confidence(level="Low", pct=30, note="n"),
        source="template", intent=intent, intent_hint=hint)


def test_template_card_builder_still_defaults_to_question() -> None:
    """The raw card builder keeps the safe default; routing is a later step.

    `template_card` / `_no_run_card` never set `intent` themselves, so the card
    they build reads as a question. Routing is decided *after* the card is built
    by `classify_turn` (`chat_service.py:202` no-run path, `:226` answered path,
    `:359` stream path).
    """
    from app.agent.chat_service import _no_run_card

    assert template_card(_facts()).intent == "question"
    assert template_card(_facts()).intent_hint is None
    assert _no_run_card().intent == "question"
    assert _no_run_card().intent_hint is None


def test_every_canonical_intervention_phrasing_routes() -> None:
    """The design's vocabulary reaches the gate with no model configured.

    `classify_turn` is the routing authority (`app/agent/intent.py:184`). Every
    in-vocabulary phrasing -- the §2.5 verbs `dig`/`create`/`grow`/`retain`
    included -- must come back `intent="intervention"` with a hint the client can
    open the confirm gate with.
    """
    from app.agent.intent import classify_turn

    for phrase in CANONICAL_PROPOSALS:
        out = classify_turn(phrase, _blank_card())
        assert out.intent == "intervention", (phrase, out.intent)
        assert out.intent_source == "parser", (phrase, out.intent_source)
        assert out.intent_hint, (phrase, "no hint")


def test_every_question_phrasing_stays_a_result() -> None:
    """A question about the run must not be dragged to the confirm gate."""
    from app.agent.intent import classify_turn

    for phrase in QUESTION_TABLE:
        out = classify_turn(phrase, _blank_card())
        assert out.intent == "question", (phrase, out.intent)
        assert out.intent_hint is None, (phrase, out.intent_hint)


def test_proposal_shaped_phrasings_all_reach_the_gate() -> None:
    """FINDING: proposal-shaped phrasings that still come back as result cards.

    The classifier's noun list is `ai_plan.EN_ALIASES` matched whole-word
    (`intent._match_noun`, `app/agent/intent.py:132`), so two things fall
    through and the turn reads as "ambiguous" (a result card):

    * a **plural** noun -- "add two water reservoirs in the north" reads as a
      request but ``\\breservoir\\b`` does not match "reservoirs". Note the two
      vocabularies disagree: `ai_plan.parse_intent` *does* read it as
      ``water_reservoir x2``, while `classify_turn` does not;
    * a **synonym** the alias list does not carry -- "green corridor",
      "permeable plaza", "basin" (bare), "wetland". (The classifier adds no
      synonyms of its own, by design; reported so the gap is visible.)

    Reported, not fixed: the source is owned elsewhere.
    """
    from app.agent.intent import classify_turn

    misrouted: list[str] = []
    for phrase in PROPOSAL_SHAPED_GAPS:
        out = classify_turn(phrase, _blank_card())
        if out.intent != "intervention":
            misrouted.append(f"{phrase!r} -> {out.intent}")
    assert not misrouted, (
        "FINDING (routing): proposal-shaped phrasings that do not reach the "
        "gate -- intent._match_noun (app/agent/intent.py:132) matches whole "
        "words against ai_plan.EN_ALIASES only:\n  " + "\n  ".join(misrouted))


def test_post_and_stream_chat_route_a_proposal_the_same_way() -> None:
    """FINDING: the two chat endpoints disagree when the model is unavailable.

    `POST /chat` calls `chat_service.answer` and `POST /chat/stream` calls
    `chat_service.stream_events` (`app/routers/chat.py:29`, `:50`). The stream
    path classifies on its template fallback (`chat_service.py:359`), but
    `answer` returns `template_card(facts)` *without* classifying on the
    `ProviderUnavailable` / `LLMError` / no-draft branches
    (`chat_service.py:205-210`). So with a run in scope and the model
    unreachable, the same proposal-shaped instruction is an intervention on the
    stream and a result card on the plain endpoint -- the exact symptom the todo
    reports.
    """
    from app.agent import llm

    def unavailable(*_args, **_kwargs):
        raise llm.ProviderUnavailable("test: no model configured")

    original_json, original_stream = llm.complete_json, llm.stream_text
    llm.complete_json = unavailable
    llm.stream_text = unavailable
    try:
        question = "add a water reservoir in the north"
        post = chat_service.answer(question, _facts())
        events = list(chat_service.stream_events(question, _facts()))
        stream = next(e["answer"] for e in events if e["event"] == "answer")
    finally:
        llm.complete_json, llm.stream_text = original_json, original_stream

    assert post.intent == stream["intent"], (
        "FINDING (routing): POST /chat and POST /chat/stream disagree on the "
        f"same instruction -- answer()={post.intent!r} (chat_service.py:206) vs "
        f"stream_events()={stream['intent']!r} (chat_service.py:359). answer() "
        "must classify on its template fallback too.")


def test_llm_supplied_intent_is_passed_through_verbatim() -> None:
    """When the model does classify, the backend routes on its word unchanged."""
    original = chat_service._draft

    def fake_draft(facts, question, history, *, on_delta=None):
        return {
            "title": "t", "sentence": "s",
            "confidence": {"level": "Medium", "pct": 50, "note": "n"},
            "intent": "intervention",
            "intent_hint": "add a water reservoir in the north",
        }

    chat_service._draft = fake_draft
    try:
        card = chat_service.answer("dig a basin in the north", _facts())
    finally:
        chat_service._draft = original
    assert card.intent == "intervention", card.intent
    assert card.intent_hint == "add a water reservoir in the north"


def test_classifier_reads_the_same_vocabulary_as_the_planner() -> None:
    """The router's nouns are `ai_plan.EN_ALIASES`, not a private copy.

    One authority for the vocabulary is the whole point of `intent.py` (its
    docstring says so): if the planner learns a new alias and the router does
    not, the two drift and a proposal-shaped turn stops routing. This pins the
    router's noun set back to the planner's alias list.
    """
    from app.agent.intent import KIND_WORD, NOUNS

    expected = {alias: kind for alias, kind in ai_plan.EN_ALIASES.items()
                if kind in KIND_WORD}
    assert NOUNS == expected, (
        "intent.NOUNS has drifted from ai_plan.EN_ALIASES -- the router and the "
        "planner no longer share one vocabulary")


def test_client_fast_path_shares_the_server_vocabulary() -> None:
    """The client's fast path is the same vocabulary as the server's (R2 fixed).

    The client's `looksLikeIntervention` used to be a hand-typed twelve-verb
    regex, smaller than the server's (DESIGN §2.5, cause R2). It is now driven by
    a shared vocabulary: `contracts/intent_vocabulary.json` is the single source,
    the client copies it into `intentVocabulary.ts`, and the server's
    `agent/intent.py` is the authority. This asserts all three agree -- the verbs
    and nouns the server routes are the ones the client's fast path knows, so it
    no longer silently drops a proposal-shaped turn.
    """
    from app.agent.intent import INTERVENTION_VERBS, KIND_WORD, NOUNS, REGION_WORDS, REMOVAL_VERBS

    fixture = json.loads(CONTRACT_VOCAB.read_text(encoding="utf-8"))
    assert fixture["interventionVerbs"] == list(INTERVENTION_VERBS)
    assert fixture["removalVerbs"] == list(REMOVAL_VERBS)
    assert fixture["nouns"] == NOUNS
    assert fixture["kinds"] == KIND_WORD
    assert fixture["regions"] == REGION_WORDS

    # The client copy must carry every verb the server routes, or its fast path
    # is again narrower than the server's.
    client = INTENT_VOCAB_TS.read_text(encoding="utf-8")
    missing = [v for v in (*INTERVENTION_VERBS, *REMOVAL_VERBS)
               if f"'{v}'" not in client and f'"{v}"' not in client]
    assert not missing, (
        "FINDING (routing): client fast path (intentVocabulary.ts) does not "
        f"carry every server verb: {missing}")


# --------------------------------------------------------------------------- #
# 3. Simulation of applied plans
# --------------------------------------------------------------------------- #


def test_applied_plan_moves_the_ledger_and_has_no_combined_score() -> None:
    """A confirmed proposal applied through /ai-plan/apply moves the ledger.

    The proposal is a controlled in-bounds, sub-cap green-space footprint so the
    assertion tests the apply -> ledger machinery, not the proposer's site
    choice.
    """
    scene_id = _fresh_scene()
    try:
        proposal = {"geometry": PROBE_POLY, "interventionType": "green_space",
                    "source": "ai", "params": {}, "style": {}}
        applied = _apply(scene_id, "add green space", [proposal], "heuristic")
        assert applied["planId"], "apply returned no planId"
        assert applied["features"] == 1

        result = _ledger(scene_id, applied["planId"])
        # baseline beside scenario, and a signed delta
        assert "baseline" in result and "scenario" in result
        assert "unstoredM3" in result["baseline"] and "unstoredM3" in result["scenario"]
        delta = result["deltaUnstoredM3"]
        assert isinstance(delta, (int, float)) and delta != 0.0, (
            f"the applied plan did not move the drainage ledger (delta={delta})")
        assert delta < 0, "green space must reduce the unstored volume"
        # no cross-axis judgement anywhere
        hits = _forbidden_key_hits(result)
        assert not hits, f"ledger carries a combined score / verdict field: {hits}"
    finally:
        client.delete(f"/api/scenes/{scene_id}")


def test_ledger_response_has_no_combined_score_or_verdict() -> None:
    """The forbidden-key scan on a real (heuristic) applied plan."""
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    body = _propose(scene_id, "add green space in the centre")
    if not body["proposals"]:
        print("    (skipped: heuristic produced no proposals here)")
        return
    applied = _apply(scene_id, "add green space in the centre",
                     body["proposals"], body["provider"])
    try:
        result = _ledger(scene_id, applied["planId"])
        hits = _forbidden_key_hits(result) + _forbidden_key_hits(applied)
        assert not hits, f"a combined score / verdict field leaked: {hits}"
    finally:
        if applied.get("planId"):
            from app.services import scene_service
            scene_service._delete_plan_files(applied["planId"])


def test_heuristic_proposal_set_moves_the_ledger() -> None:
    """The proposal the user would actually confirm must move the simulation.

    FINDING: it does not. The heuristic proposes the *largest* site in each
    pool, and those are either refused by the ledger's 25% per-intervention cap
    (water reservoir 29.8%, permeable paving 85.3%) or already the target class
    (the green-space site is already green, cellsChanged 0). So a confirmed
    proposal set applies to a plan whose drainage delta is exactly zero.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    moved: list[str] = []
    unmoved: list[str] = []
    for instruction in ("add a water reservoir in the north",
                        "add permeable paving in the north",
                        "add green space in the centre"):
        body = _propose(scene_id, instruction)
        if not body["proposals"]:
            unmoved.append(f"{instruction!r} (no proposals)")
            continue
        applied = _apply(scene_id, instruction, body["proposals"], body["provider"])
        try:
            result = _ledger(scene_id, applied["planId"])
            if result["deltaUnstoredM3"] != 0.0:
                moved.append(instruction)
            else:
                why = [s.get("why") for s in result["interventionsSkipped"]] or \
                    ["applied but changed no cell"]
                unmoved.append(f"{instruction!r} -> {why[0][:60]}")
        finally:
            from app.services import scene_service
            scene_service._delete_plan_files(applied["planId"])
    assert moved, (
        "no confirmed proposal set moved the ledger:\n  " + "\n  ".join(unmoved))


# --------------------------------------------------------------------------- #
# 4. Constraints not yet implemented -- intended contract, expected to fail
#
# gao is designing these (the todo's "LLM input" item). They are written as the
# contract the finished feature must satisfy, and they fail until it lands.
# --------------------------------------------------------------------------- #


def test_PENDING_proposals_respect_the_coverage_cap() -> None:
    """PENDING (size limits, gao): a proposal must not exceed the ledger's cap.

    Intended contract: every proposed site is <= MAX_INTERVENTION_COVERAGE, so a
    confirmed proposal always applies. Today the reservoir (29.8%) and paving
    (85.3%) pools return land-use-scale masks the ledger then refuses.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    grids = _grids()
    site_cells = float(grids.rows * grids.cols)
    over: list[str] = []
    for instruction in ("add a water reservoir in the north",
                        "add permeable paving in the north"):
        for proposal in _propose(scene_id, instruction)["proposals"]:
            cells = len(_polygon_cells(grids, proposal["geometry"]["coordinates"]))
            coverage = cells / site_cells
            if coverage > MAX_INTERVENTION_COVERAGE:
                over.append(f"{proposal['interventionType']} {coverage:.1%}")
    assert not over, (
        "PENDING (size limits not implemented): proposals over the "
        f"{MAX_INTERVENTION_COVERAGE:.0%} cap: {over}")


def _building_cells(scene_id: str):
    from app.services import scene_service

    model = scene_service.buildings_path(scene_id)
    if not model or not Path(model).exists():
        return None
    grids = _grids()
    cells: set[tuple[int, int]] = set()
    for feature in json.loads(Path(model).read_text(encoding="utf-8")).get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Polygon":
            cells.update(_polygon_cells(grids, geometry.get("coordinates")))
    return cells


def test_PENDING_proposals_avoid_existing_buildings() -> None:
    """PENDING (building-avoidance, gao): no proposal cell on a building.

    Intended contract: a proposed footprint does not overlap an existing
    building's footprint.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    buildings = _building_cells(scene_id)
    if buildings is None:
        print("    (skipped: no building model in this environment)")
        return
    grids = _grids()
    offenders: list[str] = []
    for instruction in ("add a water reservoir in the north",
                        "add green space in the centre",
                        "add permeable paving in the north"):
        for proposal in _propose(scene_id, instruction)["proposals"]:
            cells = set(_polygon_cells(grids, proposal["geometry"]["coordinates"]))
            overlap = cells & buildings
            if overlap:
                offenders.append(f"{proposal['interventionType']} overlaps "
                                 f"{len(overlap)} building cell(s)")
    assert not offenders, (
        "PENDING (building-avoidance not implemented): " + "; ".join(offenders))


def test_PENDING_proposals_do_not_overlap_each_other() -> None:
    """PENDING (overlap prevention, gao): two proposals occupy distinct cells."""
    scene_id = _grounded_scene()
    if scene_id is None:
        print("    (skipped: no grounded scene in this environment)")
        return
    body = _propose(scene_id, "add a water reservoir and green space in the centre")
    proposals = body["proposals"]
    if len(proposals) < 2:
        print("    (skipped: fewer than two proposals here)")
        return
    grids = _grids()
    cell_sets = [set(_polygon_cells(grids, p["geometry"]["coordinates"]))
                 for p in proposals]
    for i in range(len(cell_sets)):
        for j in range(i + 1, len(cell_sets)):
            shared = cell_sets[i] & cell_sets[j]
            assert not shared, (
                "PENDING (overlap prevention not implemented): proposals "
                f"{i} and {j} share {len(shared)} cell(s)")


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



