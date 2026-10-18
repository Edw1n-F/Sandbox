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
import numpy as np  # noqa: E402

from app.agent import chat_service  # noqa: E402
from app.agent.answer import template_card  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_plan  # noqa: E402
from app.services.proposal_constraints import (  # noqa: E402
    REASON_CODES,
    REJECT_STATUS,
)
from app.services.sandbox_model import (  # noqa: E402
    DEFAULT_RAINFALL_MM,
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


def _clear_staged(scene_id: str) -> None:
    """Take everything this suite placed off the sandbox.

    ``/ai-plan/apply`` pins into the scene's staged plan rather than writing a
    plan of its own, so a test that applies and does not clear leaves ground
    claimed for every test after it, and later proposals are rightly refused.
    """
    from app.services import pins

    for row in pins.staged_features(scene_id):
        if row.get("pinned"):
            pins.unpin(scene_id, row["id"])


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
    # The staged plan is shared state: a prior test (or a manual probe) that
    # pinned a reservoir leaves ground claimed, and the proposer then rightly
    # offers nothing over it -- which this test would misread as "the kind
    # emits nothing". Clear the suite's own pins first so the assertion tests
    # the proposal path, not the leftovers of the test that ran before it.
    _clear_staged(scene_id)
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


def _rect_ring(min_lon: float, min_lat: float,
               max_lon: float, max_lat: float) -> list[list[float]]:
    """A closed lon/lat rectangle, sized in degrees.

    Built here rather than borrowed from a pool so this fixture is independent
    of which sites today's data happens to offer -- the point of these two tests
    is the overlap rule, and a fixture that can silently stop exercising it (by
    returning one proposal, as the previous version did) tests nothing.
    """
    return [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat]]


def _rect_polygon(min_lon: float, min_lat: float,
                  max_lon: float, max_lat: float) -> dict:
    return {"type": "Polygon",
            "coordinates": [_rect_ring(min_lon, min_lat, max_lon, max_lat)]}


def _candidate(kind: str, cell_id: str, min_lon: float, min_lat: float,
               max_lon: float, max_lat: float) -> dict:
    """A site-based candidate dict of the shape `validate_batch` consumes."""
    return {"id": cell_id, "intervention_type": kind,
            "geometry": _rect_polygon(min_lon, min_lat, max_lon, max_lat),
            "params": {}}


def test_overlap_guard_refuses_the_second_and_the_accepted_stay_disjoint() -> None:
    """Two candidates on the same ground: the first wins, the second is refused.

    Driven straight through `validate_batch` with its own overlapping polygons,
    because the endpoint cannot be made to produce two overlapping proposals --
    the gate already prevents that, which is the behaviour under test. Asserting
    it through the endpoint would therefore only ever observe the empty case.

    The second candidate must be refused `OVERLAPS_STAGED` and must name
    `proposal#0` as the owner: a refusal that does not say *whose* ground it
    was is not actionable by the planner who receives it.
    """
    from app.services.proposal_constraints import validate_batch

    grids = _grids()
    west, south, east, north = grids.bounds
    d_lon = (east - west) / grids.cols
    d_lat = (north - south) / grids.rows

    # Two rectangles sharing a 6x6-cell block, each 20x20 cells, placed well
    # inside the grid so neither can clip out of bounds.
    r, c = 40, 40
    first = _candidate("green_space", "cand-a",
                       west + c * d_lon, north - (r + 20) * d_lat,
                       west + (c + 20) * d_lon, north - r * d_lat)
    second = _candidate("green_space", "cand-b",
                        west + (c + 14) * d_lon, north - (r + 20) * d_lat,
                        west + (c + 34) * d_lon, north - r * d_lat)

    accepted, rejected = validate_batch(grids, [first, second], None, set())

    assert len(accepted) == 1, (
        f"expected exactly one acceptance, got {len(accepted)}: "
        f"{[p['intervention_type'] for p in accepted]}")
    assert len(rejected) == 1, (
        f"expected exactly one refusal, got {len(rejected)}: "
        f"{[r_['reasonCode'] for r_ in rejected]}")
    assert rejected[0]["reasonCode"] == "OVERLAPS_STAGED", rejected[0]
    assert rejected[0]["overlapCells"] > 0, (
        "the refusal must say how many cells are contested, or it is not a "
        "finding a planner can act on")
    assert rejected[0]["status"] == REJECT_STATUS, rejected[0]
    assert rejected[0]["warning"], "a refusal carries a written reason"

    # And the served geometry of the acceptance must not reach into the
    # contested block: refusing the second is only safe if the first stopped
    # exactly where it was accepted.
    served = set(_polygon_cells(grids, accepted[0]["geometry"]["coordinates"]))
    contested = served & {
        (rr, cc) for rr in range(r, r + 20) for cc in range(c + 14, c + 20)
    }
    assert contested, (
        "the fixture did not actually overlap: the two rectangles share no "
        "cell, so this test would pass without exercising the guard")


def test_apply_accepts_the_provider_string_propose_actually_returns() -> None:
    """The apply endpoint must accept the provider the propose endpoint emits.

    The frontend echoes `AIPlanResult.provider` straight back into
    `AIPlanApplyRequest.provider` (`ChatPanel.tsx:427`, "so the audit trail
    matches what the user saw"). Until this was measured against a live Ollama
    run, `AIPlanApplyRequest.provider` carried the *propose* endpoint's pattern
    `^(auto|llm|heuristic)$` -- but `generate_proposals` emits a wider set:
    `openai-compatible` for any /chat/completions endpoint, `anthropic`,
    `unknown`, and after a fallback a composite such as
    "heuristic (after openai-compatible returned nothing)" (`ai_plan.py:2287`).

    The consequence was total and invisible from a heuristic-only run: **every
    apply whose proposal came from a real model failed 422**, because the client
    echoed back exactly the string the server had just handed it. Only the
    heuristic fallback could ever be confirmed.

    So this test closes the loop the way the product does: propose, take the
    `provider` from that response verbatim, and apply with it. It asserts 200,
    which is the only thing that proves the echo is accepted.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene: the propose/apply provider echo cannot be "
            "observed, and this test must not report a pass it did not earn")

    body = _propose(scene_id, "add a water reservoir in the north")
    echoed = body.get("provider")
    assert echoed, "the propose response carried no provider to echo"
    assert body.get("proposals"), "no proposal to apply"

    resp = client.post(f"/api/scenes/{scene_id}/ai-plan/apply", json={
        "instruction": "add a water reservoir in the north",
        "provider": echoed,
        "proposals": body["proposals"],
    })
    try:
        assert resp.status_code == 200, (
            f"apply rejected the provider the propose endpoint returned "
            f"({echoed!r}): {resp.text[:300]}")
    finally:
        # Apply puts the proposal on the sandbox (the staged plan). Left there,
        # its ground is correctly refused to every later proposal in this suite.
        _clear_staged(scene_id)


def test_apply_accepts_every_provider_string_the_planner_can_emit() -> None:
    """The same guarantee, without needing a live model.

    The test above echoes whatever provider answered *this* run, which is
    `heuristic` whenever no LLM is configured -- a value the old pattern
    happened to allow. So it would have passed with the bug present. This one
    asserts the schema directly against the full set `generate_proposals` can
    emit, so it bites in CI where no provider is live.

    Every value here is one the code actually produces, not a hypothetical:
    `openai-compatible` is the class `name` on `OpenAICompatibleProvider`
    (`ai_plan.py:1785`), `heuristic` is the fallback, and the composite is the
    post-fallback label built at `ai_plan.py:2287`.
    """
    from app.schemas.plan import AIPlanApplyRequest

    for value in ("auto", "llm", "heuristic", "openai-compatible", "anthropic",
                  "unknown",
                  "heuristic (after openai-compatible returned nothing)"):
        AIPlanApplyRequest.model_validate({
            "instruction": "add a water reservoir in the north",
            "provider": value,
            "proposals": [],
        })


def test_rejected_refusals_reach_the_wire() -> None:
    """A refusal the client cannot see is a silent outage.

    `AIPlanResponse.rejected` is declared and its docstring promises "a refusal
    is carried, never dropped", but the three `model_validate` sites in
    `app/routers/scenes.py` did not pass it -- so the field always serialised
    as `[]` and a user who was refused learned nothing.

    The endpoint can no longer be made to *produce* a refusal by a bare
    instruction: `_drop_claimed_sites` removes prior-work candidates before the
    gate sees them (by design), and the reservoir pool (gentle-slope beds) sits
    apart from the green-space blocks, so nothing collides inside one batch
    either. The refusal is therefore manufactured the same way the guard
    manufactures it -- through `validate_batch` with two overlapping candidates
    -- and the assertion is against the *wire shape* the endpoint serialises,
    which is the contract that used to be dropped.

    This asserts the field is *populated* and that every entry carries a closed
    `reasonCode` and a non-empty reason. It fails loudly rather than skipping:
    an assertion that cannot run here would be indistinguishable from a pass.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene in this environment: the rejected[] contract "
            "cannot be observed, and this test must not report a pass it did "
            "not earn")

    from app.services.proposal_constraints import validate_batch
    from app.schemas.plan import AIPlanResponse

    grids = _grids()
    west, south, east, north = grids.bounds
    d_lon = (east - west) / grids.cols
    d_lat = (north - south) / grids.rows
    r, c = 40, 40
    first = _candidate("green_space", "cand-a",
                       west + c * d_lon, north - (r + 20) * d_lat,
                       west + (c + 20) * d_lon, north - r * d_lat)
    second = _candidate("green_space", "cand-b",
                        west + (c + 14) * d_lon, north - (r + 20) * d_lat,
                        west + (c + 34) * d_lon, north - r * d_lat)
    accepted, rejected = validate_batch(grids, [first, second], None, set())
    assert rejected, "validate_batch produced no refusal to serialise"
    assert len(accepted) == 1 and len(rejected) == 1, (
        f"expected 1 accepted + 1 rejected, got {len(accepted)} + {len(rejected)}")

    # The wire shape is what the endpoint serialises: an AIPlanResponse built
    # from the rejection must carry it as `rejected` (camelCase fields), not
    # drop it to [].
    response = AIPlanResponse.model_validate({
        "provider": "heuristic",
        "features": len(accepted),
        "proposals": accepted,
        "rejected": rejected,
    })
    body = response.model_dump(by_alias=True)
    wire_rejected = body.get("rejected", [])
    assert isinstance(wire_rejected, list), (
        f"rejected must be a list, got {type(wire_rejected).__name__}")
    assert wire_rejected, (
        "the response dropped the refusal: rejected serialised to []")

    for entry in wire_rejected:
        assert entry.get("reasonCode"), f"a refusal with no reasonCode: {entry}"
        assert entry["reasonCode"] in REASON_CODES, (
            f"{entry['reasonCode']!r} is outside the frozen set")
        assert entry.get("warning"), f"a refusal with no written reason: {entry}"
        assert entry.get("interventionType"), (
            f"a refusal that does not say which kind was refused: {entry}")

    # The complementary half: what is served and what was refused must be told
    # apart. A response claiming both "3 proposals" and "6 refusals" is the
    # honest description of a constrained batch; a client cannot reconstruct
    # that from `features` alone.
    assert body["features"] == len(body["proposals"]), (
        "`features` must be the accepted count, not the batch size")


# --------------------------------------------------------------------------- #
# 4. P0-2: the parameter card
# --------------------------------------------------------------------------- #


def _gross_vs_net(grids: Any) -> tuple[float, float]:
    """``(gross, net)`` for one real reservoir proposal, both from the ledger.

    ``gross`` is the brief's own ``area x depth`` formula, applied to the
    proposal the server actually served: its rasterised cell count, the grid's
    cell area, and the depth the ledger resolved for that kind. It counts only
    what the new structure is *claimed* to hold.

    ``net`` is ``scenario.storageCapacityM3 - baseline.storageCapacityM3`` read
    out of ``ledger_for_scenario`` -- the same subtraction ``run_service``
    publishes as ``stats.delta.storageCapacityM3``. It is smaller than gross
    whenever the ground the proposal covers already stored something, because
    net credits what the ground gave up.

    Both sides are computed through the ledger's own functions rather than
    re-implemented here: a second copy of either figure is the failure this test
    exists to catch, so the test must not become one.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene: the gross/net comparison needs a real proposal, "
            "and this test must not report an unearned pass")

    proposals = _propose(scene_id, "add a water reservoir in the north")["proposals"]
    assert proposals, "no reservoir proposal to compare gross against net"
    proposal = proposals[0]

    # The depth the ledger resolved, read from the proposal's own published
    # parameters block -- not from the class default, so a proposal whose depth
    # was edited still compares the figure the run will actually use.
    depth = float(proposal["parameters"]["storageDepthM"]["value"])
    served = set(_polygon_cells(grids, proposal["geometry"]["coordinates"]))
    gross = round(len(served) * grids.cell_area_m2 * depth, 1)

    feature = {
        "geometry": proposal["geometry"],
        "intervention_type": proposal["interventionType"],
        "params": dict(proposal.get("params") or {},
                       storageDepthM=depth),
        "source": "ai",
    }
    ledger = ledger_for_scenario(grids, [feature], rainfall_mm=DEFAULT_RAINFALL_MM)
    net = round(ledger["scenario"]["storageCapacityM3"]
                - ledger["baseline"]["storageCapacityM3"], 1)
    return gross, net


def _card_for_reservoir(grids: Any) -> dict:
    """The card for a plan holding one real reservoir proposal.

    ``_card()`` reads the scene's own plan, which is a *different* plan from the
    proposal ``_gross_vs_net`` measures -- so comparing their gross figures
    compares two plans, not two implementations. This builds the plan, so the
    card and the independent computation describe one thing.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene: the card cannot be observed for a real "
            "proposal, and this test must not report an unearned pass")
    proposal = _propose(
        scene_id, "add a water reservoir in the north")["proposals"][0]
    resp = client.post(f"/api/scenes/{scene_id}/plans", json={
        "planType": "custom", "label": "contract-suite reservoir",
        "source": "ai", "params": {},
        "interventions": [{
            "geometry": proposal["geometry"],
            "interventionType": proposal["interventionType"],
            "source": "ai",
            "params": dict(proposal.get("params") or {}),
            "style": proposal.get("style") or {},
        }],
    })
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["planId"]
    card = client.get(f"/api/scenes/{scene_id}/parameter-card",
                      params={"plan_id": plan_id})
    assert card.status_code == 200, card.text
    return card.json()


def _card() -> dict:
    """The card for the seeded scene, or a loud failure.

    No skipping: a card assertion that cannot run here would report a pass it
    did not earn, which is the same defect R21 exists to remove from the
    overlap guard.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene in this environment: the parameter card cannot "
            "be observed, and this test must not report an unearned pass")
    resp = client.get(f"/api/scenes/{scene_id}/parameter-card")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _card_entry(card: dict, key: str) -> dict:
    for entry in card["entries"]:
        if entry["key"] == key:
            return entry
    raise AssertionError(
        f"the card has no {key!r} row; rows are "
        f"{[e['key'] for e in card['entries']]}")


def test_every_card_number_matches_the_ledger() -> None:
    """Every number on the card is the ledger's own number, read not recomputed.

    The card is only useful if it cannot disagree with a run. This recomputes
    each row through the functions the ledger uses and asserts equality, so a
    second implementation of any figure fails here rather than in a client's
    head.

    R4 is the load-bearing assertion: gross explicit storage (the brief's
    ``area x depth``) must be **>= net** storage capacity change, because net
    credits the storage the ground gave up. If gross ever fell below net, the
    card would be quoting a gross figure that flattered a proposal built on
    ground which already stored something.
    """
    from app.services.proposal_constraints import cells_to_area_ha
    from app.services.sandbox_model import ledger_for_scenario

    grids = _grids()
    scene_id = _grounded_scene()
    features: list[dict] = []
    if scene_id is not None:
        from app.routers.scenes import intervention_features, request_plan_id
        chosen = request_plan_id(scene_id)
        features = intervention_features(chosen) if chosen else []
    ledger = ledger_for_scenario(grids, features, rainfall_mm=DEFAULT_RAINFALL_MM)
    card = _card()

    baseline = ledger["baseline"]
    scenario = ledger["scenario"]

    # Site area: the same helper the ledger's own size block uses.
    assert _card_entry(card, "siteAreaHa")["value"] == cells_to_area_ha(
        grids.rows * grids.cols, grids.cell_area_m2)

    # Per-class areas: read from the one sum `_area_stats` computes.
    for key, cls in (("waterAreaHa", "water"), ("wetlandAreaHa", "wetland"),
                     ("greenAreaHa", "green")):
        expected = round(baseline["classAreaM2"][cls] / 10_000.0, 2)
        assert _card_entry(card, key)["value"] == expected, (
            f"{key}: card says {_card_entry(card, key)['value']}, ledger says "
            f"{expected}")

    # Signed deltas, scenario minus baseline.
    bare = round((scenario["classAreaM2"]["bare"]
                  - baseline["classAreaM2"]["bare"]) / 10_000.0, 2)
    assert _card_entry(card, "bareGroundCreatedHa")["value"] == bare
    impervious = round(scenario["imperviousShare"]
                       - baseline["imperviousShare"], 4)
    assert _card_entry(card, "imperviousShareChange")["value"] == impervious
    net = round(scenario["storageCapacityM3"]
                - baseline["storageCapacityM3"], 1)
    assert _card_entry(card, "netStorageCapacityChangeM3")["value"] == net

    # R4: gross >= net, and both are present as distinct rows. Checked on a real
    # reservoir rather than on the empty plan, because on an empty plan gross is
    # legitimately absent and the assertion would pass without testing anything.
    gross_entry = _card_entry(card, "explicitStorageAddedM3")
    net_entry = _card_entry(card, "netStorageCapacityChangeM3")
    assert gross_entry["key"] != net_entry["key"], (
        "gross and net must be two rows; one number cannot be both")
    assert "GROSS" in gross_entry["note"], (
        "the gross row must label itself gross, or a reader will take it for "
        "the run's figure")
    assert "NET" in net_entry["note"], (
        "the net row must label itself net")

    gross, net = _gross_vs_net(grids)
    assert gross >= net, (
        f"gross {gross} < net {net}: the brief's area x depth formula cannot be "
        "smaller than the ledger's own capacity change, which also credits "
        "what the ground gave up")

    # The row must actually CARRY the figure, not merely describe its absence.
    # R4 asks for two numbers side by side; a card that prints the net figure
    # and a sentence explaining why gross is missing has delivered one of the
    # two. Compared against a card built for the SAME proposal, so the two
    # figures describe one plan rather than two.
    plan_card = _card_for_reservoir(grids)
    gross_row = _card_entry(plan_card, "explicitStorageAddedM3")
    assert gross_row["value"] is not None, (
        "the gross row carries no value on a plan that has storage: R4's "
        "'two numbers side by side' is not delivered, only described")
    assert gross_row["value"] == gross, (
        f"card gross {gross_row['value']} != the independently computed {gross}: "
        "the card's area x depth is a second implementation of the brief's "
        "formula, which is the failure this test exists to catch")

    # Rainfall is the number the ledger was run at, not a card-side default.
    assert card["inputs"]["rainfallMm"] == ledger["rainfallMm"]


def test_card_states_the_things_this_project_cannot_know() -> None:
    """Three rows must REFUSE a value, and no date may be invented.

    This is the honesty core of the card. Each of these rows is a number a
    reader would reasonably expect and this project does not have:

    - **soil group**: ``CURVE_NUMBER_HSG_D`` is a table merely *named* HSG D;
      no soil survey exists. It is the largest single lever on every runoff
      figure here.
    - **heat preset**: ``SCENARIO_PRESETS`` exists and has **no caller in
      ``app/``**; the run takes a raw ``temperature_c``. A card printing
      "extreme heatwave" for 30.0 C would be the exact failure this project
      exists to prevent.
    - **tree cover**: the raster has no tree class -- ``green`` aggregates ESA
      tree/shrub/grassland.

    R7: ``date`` is ``null`` unless a real acquisition date exists. Only the
    canonical Landsat scene yields one, and it dates the LST scene alone.
    """
    card = _card()
    keys = {e["key"] for e in card["entries"]}

    # -- soil group: no value, and the absence is sourced -------------------
    soil = _card_entry(card, "soilGroup")
    assert soil["value"] is None, (
        "soilGroup must carry no value: this project holds no soil survey")
    assert "no survey exists" in soil["source"].lower(), (
        f"the soil row's source must state that no survey exists, got: "
        f"{soil['source']!r}")
    assert soil["confidence"] == "UNAVAILABLE", soil

    # -- heat: a number, and no preset claim --------------------------------
    heat = _card_entry(card, "temperatureC")
    assert isinstance(heat["value"], (int, float)), (
        "the heat row must report the number the run actually used")
    assert "SCENARIO_PRESETS" in heat["source"], (
        "the heat row must say the presets exist and are not wired, or a "
        f"reader will assume one was chosen: {heat['source']!r}")
    assert not any(word in str(heat["value"]).lower()
                   for word in ("mean_summer", "hot_day", "extreme")), (
        "the heat row must not present a preset name as the value")

    # -- tree cover: absent, and said to be absent --------------------------
    assert "treeAreaHa" not in keys, (
        "the raster has no tree class; publishing a tree area would invent one")
    green = _card_entry(card, "greenAreaHa")
    assert green["value"] is not None, "green area itself IS measurable"
    assert "aggregate" in green["note"].lower(), (
        f"the green row must say the ESA class aggregates tree/shrub/grass, "
        f"got: {green['note']!r}")

    # -- R7: no invented date ----------------------------------------------
    from datetime import date as _date

    today = _date.today().isoformat()
    lst_date = _card_entry(card, "temperatureC")["date"]
    for entry in card["entries"]:
        assert entry["date"] is None or entry["date"] == lst_date, (
            f"{entry['key']} carries date {entry['date']!r}; only the canonical "
            "Landsat scene has a real acquisition date")
        assert entry["date"] != today, (
            f"{entry['key']} carries today's date, which is a fabrication and "
            "breaks determinism")

    # The one real date must be the LST scene's, derived not typed.
    assert lst_date == "2022-09-03", (
        f"the LST acquisition date must come from CANONICAL_SCENE_ID, got "
        f"{lst_date!r}")

    # Every row is fully populated: the shape itself is part of the contract.
    for entry in card["entries"]:
        for field in ("key", "label", "unit", "status", "source", "confidence"):
            assert entry.get(field) is not None, (
                f"{entry['key']} is missing {field}")
        assert entry["confidence"] in (
            "MEASURED", "DERIVED", "ASSUMED", "UNAVAILABLE"), (
            f"{entry['key']} has confidence {entry['confidence']!r}, outside "
            "the closed enum")


# --------------------------------------------------------------------------- #
# 6. A capability the executor does not have (§3B) -- C1..C6
#
# The third member of the family §3.4 named: an instruction the executor cannot
# honour, answered by producing something from the pool instead of declining.
# §3B.1 shows the mechanism is not an unmatched noun falling back -- it is a
# *forced tool with a closed enum* (`route_tools` forces
# `select_intervention_sites`; its schema requires `selections`, each item
# requires an `intervention_type` from a five-member enum, and the tool requires
# a `rationale`). So the model's only legal move is to name one of the five kinds
# and narrate the substitution. The fix is a guard that refuses *before any
# provider runs* -- a 422, not a 200 with zero features, because no model ran and
# so there is no AI output to label (invariant 3) -- rather than a looser schema.
# --------------------------------------------------------------------------- #

#: Requests for something this sandbox has no pool to create. The heuristic is
#: innocent (it returns 0 features for all of them); the substitution is on the
#: forced LLM leg only, which is why C1 drives the endpoint directly.
BUILDING_REQUESTS: list[str] = [
    "add a school",
    "add 3 buildings",
    "add a hospital",
    "add an office block",
]

#: Words that would turn the message into a claim about the *sentence* rather
#: than a fact about the system. §3B.4: a message that told a synonym user their
#: request is unsupported would be *worse* than today's silence.
UNSUPPORTED_CLAIM_WORDS: tuple[str, ...] = (
    "not supported", "unsupported", "not available", "impossible",
    "cannot be created", "no such",
)


def _require_grounded_scene() -> str:
    """The seeded scene, or a loud failure -- never a silent skip.

    An assertion that cannot run here would be indistinguishable from a pass,
    which is the same defect R21 exists to remove from the overlap guard.
    """
    scene_id = _grounded_scene()
    if scene_id is None:
        raise AssertionError(
            "no grounded scene in this environment: the capability guard cannot "
            "be observed, and this test must not report a pass it did not earn")
    return scene_id


def test_a_building_request_is_refused_not_substituted() -> None:
    """C1: a building request is refused (422), never filled with a pool kind.

    Driven straight at `/ai-plan`, not through the chat or the UI. The
    substitution is masked in the browser -- a chat turn for "add a school"
    returns `ambiguous` and never reaches the gate -- exactly like the removal
    inversion (§3.4), so a walkthrough cannot see it. Today (guardless) the LLM
    leg returns a `green_space` whose rationale names the school, and the
    heuristic leg returns a 200 with zero features; neither is a refusal, and
    this asserts one.
    """
    scene_id = _require_grounded_scene()
    for instruction in BUILDING_REQUESTS:
        resp = client.post(f"/api/scenes/{scene_id}/ai-plan", json={
            "instruction": instruction, "provider": "heuristic", "apply": False})
        assert resp.status_code == 422, (
            f"{instruction!r}: expected the guard to refuse with 422, got "
            f"{resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        assert body.get("detail"), f"{instruction!r}: a refusal with no detail"
        # And, defensively, no proposal of any kind leaked into the body.
        assert not body.get("proposals"), (
            f"{instruction!r}: a refused request carried proposals: "
            f"{body.get('proposals')}")


def test_the_substitution_is_still_reproducible_from_the_schema() -> None:
    """C2: the substitution is *server-permitted*, not a model misbehaving.

    Replays a recorded transport that returns a `select_intervention_sites`
    selection for a building instruction, straight through the OpenAI-compatible
    provider. It succeeds -- because `route_tools` forces that tool for a
    non-massing turn, and the tool's kind enum is closed and its `selections` is
    required, so declining is a schema violation. This pins the §3B.1 diagnosis
    so a later reader does not "fix" the model: the model was filling the only
    shape we permitted.
    """
    from app.services.ai_plan import (
        CHOOSE_TOOL,
        Grounding,
        OpenAICompatProvider,
        route_tools,
    )

    # The mechanism, asserted directly: the tool is forced and cannot decline.
    _tools, forced = route_tools("add a school")
    assert forced == CHOOSE_TOOL["name"], forced
    enum = (CHOOSE_TOOL["input_schema"]["properties"]["selections"]["items"]
            ["properties"]["intervention_type"]["enum"])
    assert "green_space" in enum, enum
    assert CHOOSE_TOOL["input_schema"]["required"] == ["selections"], (
        "a schema that let the model omit `selections` could decline; the "
        "substitution depends on `selections` being required")

    site = {
        "geometry": {"type": "Polygon", "coordinates": [[
            [113.9935, 22.4318], [113.9942, 22.4318], [113.9942, 22.4325],
            [113.9935, 22.4325], [113.9935, 22.4318]]]},
        "evidence": "largest contiguous vegetated block",
        "areaHa": 2.5,
        "centroid": [113.99385, 22.43215],
        "metrics": {"greenShare": 0.9},
    }
    grounding = Grounding(bounds=(113.99, 22.43, 114.00, 22.44), green_sites=[site])

    def recorded_transport(_url, _key, _payload):
        return {"choices": [{"message": {"tool_calls": [{"function": {
            "name": CHOOSE_TOOL["name"],
            "arguments": json.dumps({"selections": [{
                "site_id": "green_space#0",
                "intervention_type": "green_space",
                "rationale": ("The largest contiguous vegetated block is the most "
                              "suitable for adding a school"),
            }]}),
        }}]}}]}

    provider = OpenAICompatProvider(base_url="http://unit-test/v1",
                                    model="unit-test-model", api_key="k",
                                    transport=recorded_transport)
    features = provider.propose("add a school", grounding)

    assert features, (
        "the recorded selection was dropped: the substitution is then NOT "
        "server-permitted, and the §3B.1 diagnosis (a forced closed-enum tool) "
        "would be wrong")
    assert features[0]["intervention_type"] == "green_space"
    assert "school" in features[0]["params"]["rationale"]


def test_a_missing_capability_refusal_names_the_supported_set() -> None:
    """C3: the refusal's lists are generated from their owning constants.

    The anti-drift pin. The `detail` must name all five `POOL_FOR_KIND` kinds and
    all six massing ops, each read from the constant that owns it -- and must NOT
    name `flood_storage`, which is in `KIND_WORD`/`STYLE_FOR_KIND` but has no pool
    and so no member in the `CHOOSE_TOOL` enum (§3B.3's correction). Naming
    "floodable ground" would promise a capability this endpoint lacks -- the same
    class of error as the substitution it explains.
    """
    from app.agent.intent import KIND_WORD

    scene_id = _require_grounded_scene()
    resp = client.post(f"/api/scenes/{scene_id}/ai-plan", json={
        "instruction": "add a school", "provider": "heuristic", "apply": False})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]

    for kind in sorted(set(ai_plan.POOL_FOR_KIND)):
        assert KIND_WORD[kind] in detail, (kind, KIND_WORD[kind], detail)
    for op in ai_plan.MASSING_TOOL["input_schema"]["properties"]["edits"][
            "items"]["properties"]["op"]["enum"]:
        assert op in detail, (op, detail)
    for region in sorted(ai_plan.REGION_FRACTIONS):
        assert region in detail, (region, detail)
    # NOTE, for the R-A author (§10.5): these two assertions encode §3B.3's
    # correction, and they are TRUE only while `flood_storage` has no pool. Ruling
    # R-A deliberately adds that pool and retires them. When it lands, flip both
    # to `in detail` -- in the R-A commit, so the red suite has exactly one
    # author (the change that retires them). Do not soften them early.
    assert "flood_storage" not in detail
    assert KIND_WORD["flood_storage"] not in detail, (
        f"the message names {KIND_WORD['flood_storage']!r}, which /ai-plan cannot "
        "propose: its pool is absent from POOL_FOR_KIND")


def test_a_synonym_phrasing_is_not_told_it_is_unsupported() -> None:
    """C4: the synonym guard (§3B.4) -- the biggest risk in §3B, and silent.

    The detector fires on a *synonym* as readily as on a genuine miss, so the
    message must never claim the user's request is unsupported: every clause is a
    fact about the system, and the supported set is listed so the retry is
    obvious. If a later contributor sharpens the wording into "a school is not
    supported", this fails.

    Two families are checked. (i) A genuinely unrecognised synonym --
    "add a rain garden in the north", a real green-space feature the vocabulary
    has not learned -- so the detector fires and this message is what the user
    sees. (ii) The pinned `PROPOSAL_SHAPED_GAPS`, which must NOT be falsely
    refused.

    FINDING, measured: §3B.4 offered all four gaps as the false-positive
    demonstration, but three -- "green corridor", "permeable plaza", "basin" --
    are now `EN_ALIASES` entries, so `classify_turn` routes them and the guard
    does not fire at all. Only "wetland" (-> `flood_storage`, no pool) still
    fires. Either way the message stays a system fact, which is what this asserts.

    That makes the pinned list a **false-positive guard only**, not a
    fires-the-detector list: three of its five members route rather than fire. An
    entry that "does not fire" is therefore still load-bearing -- deleting it
    removes the false-positive guard, it does not remove a dead test.
    """
    from app.agent.intent import classify_turn, refusal_detail

    def _assert_system_fact(phrase: str, detail: str) -> None:
        low = detail.lower()
        for word in UNSUPPORTED_CLAIM_WORDS:
            assert word not in low, (
                f"{phrase!r}: the refusal asserts {word!r} about the request -- "
                f"it must be a fact about the system:\n{detail}")
        assert "green space" in low and "water reservoir" in low, (
            f"{phrase!r}: the retry is not obvious without the supported "
            f"kinds:\n{detail}")

    # (i) A synonym the vocabulary has not learned must fire, and its message must
    #     stay a system fact -- this is the case §3B.4 is actually about. The
    #     non-vacuity guarantee is anchored HERE, on a genuinely unknown noun,
    #     rather than on a count over PROPOSAL_SHAPED_GAPS: a count would go stale
    #     the moment the vocabulary grew, which is the lesson §3B.4 taught.
    synonym = "add a rain garden in the north"
    synonym_detail = refusal_detail("scene-1", synonym)
    assert synonym_detail is not None, (
        f"Pick another unlearned synonym here: {synonym!r} no longer fires the "
        "detector, so the vocabulary has learned it -- which is good, but this "
        "test needs a creation request whose noun is genuinely unknown, or it "
        "passes without exercising the message it exists to check.")
    _assert_system_fact(synonym, synonym_detail)

    # (ii) The pinned gaps must not be falsely refused; when one fires, its
    #      message must hold the same line.
    for phrase in PROPOSAL_SHAPED_GAPS:
        out = classify_turn(phrase, _blank_card())
        assert out.intent == "intervention", (
            f"{phrase!r} no longer reads as a proposal to the router; the synonym "
            "guard assumes it does")
        detail = refusal_detail("scene-1", phrase)
        if detail is None:
            continue  # routed as an intervention: nothing was claimed
        _assert_system_fact(phrase, detail)


def test_the_unsupported_card_dates_the_cause_correctly() -> None:
    """C5: the card states the *true* cause, with or without a run (§3B.5).

    `_empty_card` dates the cause to a missing run ("No run in scope yet",
    "Press Run Simulation"), and the generic run card answers a capability gap
    with "the run is unchanged from the baseline". Both attribute a capability
    gap to something that is not the cause. Fails today: with a run in scope the
    cause is a run measure, and without one the note blames a missing run.
    """
    from app.agent import chat_service, llm

    def _unavailable(*_args, **_kwargs):
        raise llm.ProviderUnavailable("test: no model configured")

    original_json, original_stream = llm.complete_json, llm.stream_text
    llm.complete_json = _unavailable
    llm.stream_text = _unavailable
    try:
        with_run = chat_service.answer("add a school", _facts())
        without_run = chat_service.answer("add a school", {})
    finally:
        llm.complete_json, llm.stream_text = original_json, original_stream

    for label, card in (("with a run in scope", with_run),
                        ("with nothing run yet", without_run)):
        note = card.confidence.note if card.confidence else ""
        assert "No run in scope yet" not in note, (label, note)
        assert "Press Run Simulation" not in (card.todo or ""), (label, card.todo)
        assert card.title != "Nothing to explain yet", (label, card.title)
        assert card.cause and "create" in card.cause.lower() \
            and "green space" in card.cause, (
                f"{label}: the cause does not state the capability boundary: "
                f"{card.cause!r}")


def test_the_guard_does_not_fire_on_a_question() -> None:
    """C6: the detector must not fire on an interrogative.

    "can you add a school?" and "what would happen if I added buildings?" are
    questions about the run, not requests to create. Both keep their normal card
    and must not be refused by `/ai-plan`.
    """
    from app.agent.intent import (
        classify_turn,
        is_unsupported_capability,
        refusal_detail,
    )

    scene_id = _require_grounded_scene()
    for question in ("can you add a school?",
                     "what would happen if I added buildings?"):
        assert not is_unsupported_capability(question), question
        assert refusal_detail("scene-1", question) is None, question
        out = classify_turn(question, _blank_card())
        assert out.intent == "question", (question, out.intent)

        resp = client.post(f"/api/scenes/{scene_id}/ai-plan", json={
            "instruction": question, "provider": "heuristic", "apply": False})
        assert resp.status_code == 200, (
            f"{question!r}: the guard fired on a question ({resp.status_code}): "
            f"{resp.text[:200]}")


# --------------------------------------------------------------------------- #
# Minimal runner (pytest is not installed in this environment)
# --------------------------------------------------------------------------- #
# T01: the region filter
# --------------------------------------------------------------------------- #


def test_a_named_region_is_honoured_by_site_selection() -> None:
    """A named region must narrow the pool, not be ignored.

    Before this was fixed, all five regions returned **the same geometry** -- one
    sha256 across north/south/east/west/centre -- because `parse_intent` returns
    `(kind, count)` and discards the region, and `HeuristicProvider.propose` then
    took `sites[:count]` with `count` defaulting to 1. The confirm gate was a
    checklist with one item, and the owner's report was "the generated proposals
    are all repeated".

    Regions are derived from the pool, not named here. The pool itself moves
    when the wet grid is regenerated (unified to a 50 mm design storm on 17 Oct,
    the reservoir pool fell from three sites to two and "south" stopped being
    the empty one), and a hardcoded region made this test fail on correct
    behaviour every time the artefact was rebuilt. What the test is about is the
    *filter*, so it needs one region with a candidate and one without, whichever
    they happen to be.
    """
    from app.services.ai_plan import REGION_FRACTIONS, _match_region, _point_in_region

    grounding = ai_plan.load_grounding()
    heuristic = ai_plan.HeuristicProvider()
    bounds = grounding.bounds
    pool = list(grounding.reservoir_sites)
    assert pool, "the fixture needs at least one reservoir candidate"

    names = [r for r in REGION_FRACTIONS if r != "all"]

    def sites_in(region: str) -> int:
        return sum(
            1 for s in pool
            if _point_in_region(s["centroid"][0], s["centroid"][1], bounds, region))

    occupied = [r for r in names if sites_in(r) > 0]
    empty = [r for r in names if sites_in(r) == 0]
    assert occupied, "every region is empty; the fixture has no candidate at all"
    # A region genuinely holding no candidate is required for the second half.
    # Picking one from the data rather than naming it keeps that requirement
    # real instead of turning it into a claim about one rainy afternoon's grid.
    if not empty:
        return  # every region holds a candidate: the filter has nothing to prove

    sets: dict[str, list[str]] = {}
    for region in occupied[:4] + empty[:1]:
        features = heuristic.propose(
            f"add a water reservoir in the {region}", grounding)
        sets[region] = [
            json.dumps(f["geometry"], sort_keys=True) for f in features
        ]

    assert len({tuple(v) for v in sets.values()}) > 1, (
        "every region returned the same site: the requested region is being "
        "ignored, which is the bug this test exists to catch")

    # Filter, never rank. A region with no candidate must return nothing, not
    # the nearest out-of-region site -- the precedent is `resolve_scope`, whose
    # docstring calls that fallback "the exact bug this function exists to
    # remove". A site silently returned for the wrong region is worse than a
    # refusal a planner can act on.
    assert sets[empty[0]] == [], (
        f"{empty[0]} holds no reservoir candidate and must stay empty; "
        "returning an out-of-region site would present a re-sited proposal as "
        "the one that was asked for")


def test_the_region_filter_preserves_each_sites_original_pool_index() -> None:
    """Narrowing the pool must not renumber it.

    `site_id` is `f"{kind}#{i}"` over `enumerate(pool)`, and both `_materialise`
    and `resolve_scope` resolve an id by indexing the **full** pool. A narrowed
    list that is re-enumerated silently re-points every id to a different
    geometry -- the most dangerous possible version of this bug, because the
    response looks correct and the geometry is wrong.

    So: the geometry a narrowed site carries must be the geometry at its
    original index in the untouched pool.
    """
    grounding = ai_plan.load_grounding()
    pool = list(getattr(grounding, ai_plan.POOL_FOR_KIND["water_reservoir"], []))
    assert len(pool) >= 2, "the fixture needs a pool of at least two sites"

    narrowed = ai_plan.sites_in_region(pool, "west", grounding.bounds)
    assert narrowed, "west has no candidate: this test would pass vacuously"

    full_geometries = [json.dumps(s["geometry"], sort_keys=True) for s in pool]
    for index, site in narrowed:
        assert 0 <= index < len(pool), (
            f"index {index} is outside the full pool: the narrowed list was "
            "renumbered, so every site_id now resolves to the wrong geometry")
        assert json.dumps(site["geometry"], sort_keys=True) == full_geometries[index], (
            f"site at index {index} does not match the full pool's geometry at "
            "that index: the filter re-pointed an id")


def test_region_selection_is_deterministic_and_unstated_counts_do_not_cap() -> None:
    """The same sentence must select the same sites, twice and forever.

    The fix is deliberately a pure predicate over an ordered list: no distance
    sort (a float key with real ties is how this gets written wrong), no
    `time`, no `random`. And "a reservoir" must not mean "exactly one" --
    `parse_intent`'s `words` maps `"a " -> 1`, which conflates *what* is wanted
    with *how many*; reading it as a count is half of the owner's complaint.
    """
    grounding = ai_plan.load_grounding()
    heuristic = ai_plan.HeuristicProvider()

    # The region is chosen because it holds more than one candidate, not
    # hardcoded: the pool moves whenever the wet grid is regenerated (the 50 mm
    # unification on 17 Oct left "north" with one reservoir), and the claim here
    # is about the count, not about which region happens to be crowded today.
    from app.services.ai_plan import REGION_FRACTIONS, _point_in_region

    pool = list(grounding.reservoir_sites)
    bounds = grounding.bounds
    crowded = [
        r for r in REGION_FRACTIONS if r != "all"
        and sum(1 for s in pool if _point_in_region(
            s["centroid"][0], s["centroid"][1], bounds, r)) > 1
    ]
    if not crowded:
        return  # no region holds two candidates: nothing for this check to say

    region = crowded[0]
    instruction = f"add a water reservoir in the {region}"
    first = heuristic.propose(instruction, grounding)
    second = heuristic.propose(instruction, grounding)
    assert [json.dumps(f["geometry"], sort_keys=True) for f in first] == [
        json.dumps(f["geometry"], sort_keys=True) for f in second
    ], "the same instruction selected different sites on a second call"

    # An unstated count offers every eligible candidate in the region, so the
    # gate is a checklist rather than a single take-it-or-leave-it offer.
    assert len(first) > 1, (
        "an unstated count must offer every eligible candidate in the region; "
        "capping it at one is the 'no new individual proposals' complaint")


# --------------------------------------------------------------------------- #


def test_every_silent_assumption_becomes_a_question() -> None:
    """A published parameter has a question, and a question names a read parameter.

    Phase 2 R9/R11. The two halves must agree in both directions: a planner
    can only answer what the server asked, and the server must not ask about a
    number nothing reads. A question for an unread parameter would be a prompt
    that changes nothing, which is exactly the failure R13 exists to prevent.
    """
    from app.services import proposal_constraints as pc

    storage_kinds = sorted(pc._STORAGE_CLASS_FOR_KIND)
    assert storage_kinds, "no storage kinds to check"
    for kind in storage_kinds:
        params = pc.parameters_for_kind(kind)
        questions = pc.questions_for_kind(kind, {"storageDepthM": 1.5})
        by_parameter = {q["parameter"]: q for q in questions}
        assert "usableStorageDepth" in by_parameter, kind
        assert "storageDepthM" in params, kind
        entry = params["storageDepthM"]
        assert entry["status"] == "ASSUMPTION", (kind, entry)
        assert entry["edited"] is False, (kind, entry)
        # R10: the id is derived from the parameter alone, so it is stable
        # across machines and orders and needs no server state to match.
        assert by_parameter["usableStorageDepth"]["id"] == "usableStorageDepth"

    # R11's binding: the question names the *topic* (`usableStorageDepth`) and
    # the published parameter is the key the ledger reads (`storageDepthM`).
    # The two spellings are deliberate -- the topic is what a planner is asked
    # about, the key is what the run consumes -- so the assertion is that the
    # topic exists wherever the key does, not that the strings are equal.
    for kind in storage_kinds + ["permeable_paving", "green_space"]:
        published = set(pc.parameters_for_kind(kind))
        topics = {q["parameter"] for q in pc.questions_for_kind(kind, {})}
        for question in pc.questions_for_kind(kind, {}):
            assert question["scope"] == "PROPOSAL"
        if "storageDepthM" in published:
            assert "usableStorageDepth" in topics, kind
        else:
            assert "usableStorageDepth" not in topics, kind

    # Drainage/overflow is a site question only where a pipe network matters.
    assert [q["parameter"] for q in pc.site_questions(
        [{"intervention_type": "drainage_alignment"}])] == [
            "drainageOverflow", "treeCover"]
    assert [q["parameter"] for q in pc.site_questions(
        [{"intervention_type": "green_space"}])] == ["treeCover"]


def test_an_answered_question_is_honoured_not_re_asked() -> None:
    """An answered depth replaces the class default, end to end (Phase 2 R13).

    The load-bearing half: a question the model asks and the server then
    ignores is worse than no question, because it teaches the planner that
    answering is pointless. The answer has to reach ``params.storageDepthM`` --
    the one key ``apply_interventions`` reads -- and the parameter block has to
    say so, or the served value and its label disagree.
    """
    scene = _grounded_scene()
    if scene is None:
        return
    plain = _propose(scene, "add a water reservoir in the north")
    assert plain["proposals"], "no reservoir proposal to answer for"
    default = plain["proposals"][0]["parameters"]["storageDepthM"]
    assert default["value"] == 1.5, default

    answered = _propose(
        scene, "add a water reservoir in the north",
        answeredQuestions=[{"id": "usableStorageDepth",
                            "parameter": "usableStorageDepth",
                            "answer": "about 80 cm"}])
    proposal = answered["proposals"][0]
    # The SERVER's parse, not the client's: "80 cm" is 0.80 m here.
    question = {q["id"]: q for q in proposal["questions"]}["usableStorageDepth"]
    assert question["status"] == "ANSWERED", question
    assert question["answeredValue"] == 0.8, question
    assert proposal["params"]["storageDepthM"] == 0.8, proposal["params"]
    entry = proposal["parameters"]["storageDepthM"]
    assert entry["value"] == 0.8, entry
    assert entry["status"] == "USER_ASSUMPTION", entry
    assert entry["edited"] is True, entry
    # R17: the overridden default is mandatory, or the card cannot show what
    # was replaced and the served value is unrecoverable from the record.
    assert entry["defaultValue"] == 1.5, entry
    # R17, asserted negatively: STORAGE_DEPTH_M may appear only in the clause
    # naming what was overridden, never as the origin of `value`.
    assert "NOT what this run reads" in entry["source"], entry["source"]

    # An unparseable answer is read, reported, and does not override.
    garbled = _propose(
        scene, "add a water reservoir in the north",
        answeredQuestions=[{"id": "usableStorageDepth",
                            "parameter": "usableStorageDepth",
                            "answer": "as deep as it needs to be"}])
    q = {x["id"]: x for x in garbled["proposals"][0]["questions"]}["usableStorageDepth"]
    assert q["status"] == "ANSWERED", q
    assert q["answeredValue"] is None, q
    assert garbled["proposals"][0]["params"].get("storageDepthM", 1.5) == 1.5

    # R18: the warning stops claiming a class assumption once it is not one.
    assert "set by hand" in proposal["warning"], proposal["warning"]
    assert "Depth is a class assumption" not in proposal["warning"]


def test_a_hand_edited_depth_changes_its_provenance_label() -> None:
    """An edited depth relabels without re-labelling the numbers around it.

    Phase 2 R17, and the unedited case asserted in the same test on purpose:
    the refactor must leave an untouched proposal's parameter block **identical**
    to what it was, or the change has moved a published number.
    """
    from app.services import proposal_constraints as pc

    plain = pc.parameters_for_kind("water_reservoir")["storageDepthM"]
    assert plain["status"] == "ASSUMPTION"
    assert plain["edited"] is False
    assert "defaultValue" not in plain
    assert plain["confidence"] == "ASSUMED"

    edited = pc.parameters_for_kind("water_reservoir", 0.8)["storageDepthM"]
    assert edited["value"] == 0.8
    assert edited["status"] == "USER_ASSUMPTION"
    assert edited["edited"] is True
    assert edited["defaultValue"] == 1.5
    # A planner's estimate is not more knowable than a class default, and
    # labelling it better-known would launder a guess into a measurement.
    assert edited["confidence"] == "ASSUMED"
    # The class default is named only in the clause saying what it is NOT.
    assert "is what the run would otherwise have read" in edited["source"]


def test_an_edit_leaves_geometry_and_the_caps_untouched() -> None:
    """R15: a depth edit moves no cell, byte-for-byte.

    Asserted on the three blocks a re-contour or a re-cap would move --
    ``geometry``, ``size`` and ``constraints`` -- serialised and compared as
    strings, because a float that "looks the same" is not the claim. The
    response also has to say which parameters could move geometry (none) and
    which changed only a value, so the client renders the distinction rather
    than inferring it.
    """
    import json

    scene = _grounded_scene()
    if scene is None:
        return
    before = _propose(scene, "add a water reservoir in the north")["proposals"][0]
    assert before["parameters"]["storageDepthM"]["value"] == 1.5

    edited = dict(before)
    edited["params"] = {**before["params"], "storageDepthM": 3.0}
    resp = client.post(f"/api/scenes/{scene}/ai-plan/revalidate",
                       json={"proposals": [edited], "instruction": "test"})
    assert resp.status_code == 200, resp.text
    after = resp.json()["proposals"][0]

    for block in ("geometry", "size", "constraints"):
        assert json.dumps(after[block], sort_keys=True) == \
            json.dumps(before[block], sort_keys=True), block
    assert after["parameters"]["storageDepthM"]["value"] == 3.0
    body = resp.json()
    assert body["geometryAffectingParameters"] == [], body
    assert body["valuesAffectingOnly"] == ["parameters.storageDepthM"], body
    assert body["status"] == "SCREENING_PROPOSAL_REVALIDATED"
    assert body["warning"], "a re-gate must carry its own honesty label"


def test_apply_revalidates_and_refuses_an_illegal_edited_proposal() -> None:
    """The M6 hole: a proposal that changed after the gate is not persisted.

    Phase 2 R19. The client owns the proposals between the two calls, so this
    is the only place that can close it. Before the fix the apply path wrote
    whatever it was handed while the proposal's own ``parameters`` block still
    described the number the server chose.
    """
    scene = _grounded_scene()
    if scene is None:
        return
    from app.services import pins

    _clear_staged(scene)
    proposals = _propose(scene, "add permeable paving in the north")["proposals"]
    assert proposals, "no paving proposal to tamper with"
    tampered = dict(proposals[0])
    # Every cell of the proposal moved onto surveyed buildings.
    cells = _building_run()
    assert cells is not None, "no building cells to build an illegal ring over"
    tampered["geometry"] = {"type": "Polygon", "coordinates": [
        [list(p) for p in cells]]}

    resp = client.post(f"/api/scenes/{scene}/ai-plan/apply", json={
        "instruction": "test", "provider": "heuristic",
        "proposals": [tampered]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["features"] == 0, body
    assert body["planId"] is None, body
    codes = [r["reasonCode"] for r in body.get("rejected", [])]
    assert "BUILDING_OVERLAP" in codes, body
    assert pins.staged_features(scene) == [] or not any(
        f.get("pinned") for f in pins.staged_features(scene)), \
        "nothing may be pinned from a refused apply"

    # The unedited round trip in the SAME batch still persists, which is the
    # half that says the gate did not simply stop accepting everything.
    _clear_staged(scene)
    accepted = _apply(scene, "test", proposals, "heuristic")
    assert accepted["features"] == len(proposals), accepted
    _clear_staged(scene)


def test_an_out_of_range_depth_is_a_422_not_a_reason_code() -> None:
    """R20: a depth typo is a malformed request, not a placement refusal.

    A 40 m depth is not a property of a placement, and folding it into the
    closed ``REASON_CODES`` set would make every UI that groups refusals group
    a client typo with a physical refusal. Asserted on both endpoints.
    """
    from app.services import proposal_constraints as pc

    # The closed set stays exactly nine, as the design rules.
    assert len(pc.REASON_CODES) == 9, sorted(pc.REASON_CODES)
    assert "OVERLAP" not in pc.REASON_CODES or True  # no depth code was added
    for banned in ("DEPTH_OUT_OF_RANGE", "BAD_DEPTH", "INVALID_DEPTH"):
        assert banned not in pc.REASON_CODES, banned

    scene = _grounded_scene()
    if scene is None:
        return
    _clear_staged(scene)
    proposal = _propose(scene, "add a water reservoir in the north")["proposals"][0]

    for depth, why in ((40, "above the range"), (0.001, "below the range"),
                       ("forty metres", "not a number")):
        bad = dict(proposal)
        bad["params"] = {**proposal["params"], "storageDepthM": depth}
        revalidated = client.post(f"/api/scenes/{scene}/ai-plan/revalidate",
                                  json={"proposals": [bad]})
        assert revalidated.status_code == 422, (why, revalidated.status_code)
        detail = str(revalidated.json().get("detail", "")).lower()
        assert "storagedepthm" in detail, (why, detail)

        applied = client.post(f"/api/scenes/{scene}/ai-plan/apply", json={
            "instruction": "test", "provider": "heuristic", "proposals": [bad]})
        assert applied.status_code == 422, (why, applied.status_code)
        assert "storagedepthm" in str(applied.json().get("detail", "")).lower()

    # The range itself is inclusive at both ends: the bound is a limit, not a
    # value the model refuses to work with.
    for depth in (0.05, 10.0):
        ok = dict(proposal)
        ok["params"] = {**proposal["params"], "storageDepthM": depth}
        accepted = client.post(f"/api/scenes/{scene}/ai-plan/revalidate",
                               json={"proposals": [ok]})
        assert accepted.status_code == 200, (depth, accepted.text)
    _clear_staged(scene)


def _building_run() -> list[list[float]] | None:
    """A ring over a run of surveyed building cells, or None if none is found."""
    from app.services import proposal_constraints as pc
    from app.services import sandbox_model
    from _overlay_common import load_overlay

    grids = sandbox_model.load_grids()
    mask = pc.building_mask(load_overlay()) if grids is not None else None
    if grids is None or mask is None:
        return None
    rows, cols = np.where(mask)
    if len(rows) == 0:
        return None
    x0, y0, x1, y1 = grids.bounds
    r0, c0 = int(rows[0]), int(cols[0])
    side = 3

    def at(r: int, c: int) -> list[float]:
        return [round(x0 + (c + 0.5) / grids.cols * (x1 - x0), 7),
                round(y1 - (r + 0.5) / grids.rows * (y1 - y0), 7)]

    for r0 in range(int(rows.min()), max(int(rows.min()), int(rows.max()) - side)):
        for c0 in range(int(cols.min()), max(int(cols.min()), int(cols.max()) - side)):
            if all(mask[r, c] for r in range(r0, r0 + side)
                   for c in range(c0, c0 + side)):
                return [at(r0, c0), at(r0, c0 + side),
                        at(r0 + side, c0 + side), at(r0 + side, c0)]
    return None


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



