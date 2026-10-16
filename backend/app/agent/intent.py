"""Deterministic turn routing: is this a question, or an intervention request?

Why this module exists (DESIGN §2.5, causes R1/R2). With no model key configured
the chat card is assembled by code and `AnswerCard.intent` stays at its safe
default `"question"` -- so a proposal-shaped instruction ("dig a retention basin
in the north") never reaches the confirm gate and comes back as a result card.
The client's 12-verb regex is a *second, smaller* vocabulary than the server's,
so `dig`/`create`/`grow`/`retain` slip through even when a model does answer.

The fix is a pure, deterministic classifier that runs *after* the card is built
and may only ever **raise** the intent to `"intervention"`; the model can never
lower it. No model call, no clock, no randomness: the same sentence always
classifies the same way, so a test can assert it and a reader can predict it.

One authority for the vocabulary, so the two lists cannot drift:

- the nouns come from `ai_plan.EN_ALIASES` (unchanged -- this module adds no
  synonyms of its own);
- the region comes from `ai_plan._match_region` (reused, never re-implemented);
- the massing check comes from `ai_plan.parse_massing_intent`, so "make it 25%
  taller" stays a question -- massing is not a proposal (DESIGN R4).

The emitted hint is the exact shape `agent.prompts` already demands of
`intent_hint` -- ``add <type> in <region>`` / ``remove the <type> near
<region>`` -- so the client needs no new parsing.
"""

from __future__ import annotations

import re

from app.agent.answer import AnswerCard
from app.services.ai_plan import (
    EN_ALIASES,
    MASSING_TOOL,
    POOL_FOR_KIND,
    REGION_FRACTIONS,
    _match_region,
    parse_massing_intent,
)

__all__ = [
    "INTERVENTION_VERBS",
    "REMOVAL_VERBS",
    "KIND_WORD",
    "KIND_PHRASE",
    "REGION_WORDS",
    "NOUNS",
    "classify_turn",
    "parse_intervention_intent",
    # The refusal path (§3.2 removal, §3B missing capability). Two predicates,
    # two generated messages, one shared helper -- see `refusal_detail`.
    "names_a_creation",
    "is_removal_instruction",
    "is_unsupported_capability",
    "capability_create_words",
    "capability_statement",
    "removal_refusal_detail",
    "refusal_detail",
]


#: Verbs that read as *adding* something to the scene. The list is the design's
#: §2.5 verb set, widened past the client's original twelve so `dig`, `create`,
#: `grow` and `retain` route on the server too (cause R2).
INTERVENTION_VERBS: tuple[str, ...] = (
    "add", "place", "create", "build", "dig", "plant", "grow", "fill",
    "retain", "retrofit", "convert", "replace", "install", "put",
    "increase", "expand",
)

#: Verbs that read as *removing* something. Kept apart so the hint can be shaped
#: as "remove the <type> near <region>" rather than "add <type> in <region>".
REMOVAL_VERBS: tuple[str, ...] = ("remove", "delete", "drop", "take out")


#: The five kinds that are actually proposals (they have a grounding pool). A
#: noun from `EN_ALIASES` that maps outside this set (`wind`, `podium`) is not a
#: proposal, so it must not raise the intent.
KIND_WORD: dict[str, str] = {
    "water_reservoir": "water reservoir",
    "green_space": "green space",
    "permeable_paving": "permeable paving",
    "flood_storage": "floodable ground",
    "drainage": "drainage",
    "road": "road",
}

#: The same kinds as a noun phrase, with the article that reads correctly. The
#: hint keeps the article so it matches the design's own example verbatim
#: ("add a water reservoir in the north") and `ChatPanel`'s fallback chips, while
#: a mass noun ("green space") takes none.
KIND_PHRASE: dict[str, str] = {
    "water_reservoir": "a water reservoir",
    "green_space": "green space",
    "permeable_paving": "permeable paving",
    "flood_storage": "floodable ground",
    "drainage": "drainage",
    "road": "a road",
}

#: `_match_region`'s region codes, as words that read inside a hint. Each
#: carries its article so the phrase matches the design's own example verbatim
#: ("add a water reservoir in the north") and `ChatPanel`'s fallback chips.
REGION_WORDS: dict[str, str] = {
    "north": "the north",
    "south": "the south",
    "east": "the east",
    "west": "the west",
    "centre": "the centre",
    "north-east": "the north-east",
    "north-west": "the north-west",
    "south-east": "the south-east",
    "south-west": "the south-west",
    "all": "the whole area",
}

#: The proposal nouns, derived from `EN_ALIASES` rather than re-typed, filtered
#: to the kinds that are proposals. Insertion order is preserved; matching sorts
#: by length, so this order does not matter.
NOUNS: dict[str, str] = {
    alias: kind for alias, kind in EN_ALIASES.items() if kind in KIND_WORD
}


_VERB_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in INTERVENTION_VERBS + REMOVAL_VERBS) + r")\b",
    re.IGNORECASE,
)
_REMOVAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in REMOVAL_VERBS) + r")\b",
    re.IGNORECASE,
)
#: The *creation* verbs on their own. `_VERB_RE` mixes creation and removal so a
#: hint can be shaped either way; the missing-capability detector (§3B) needs the
#: creation half alone, because "remove the school" is a removal, not a request
#: for a capability we lack.
_CREATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in INTERVENTION_VERBS) + r")\b",
    re.IGNORECASE,
)

#: A turn that opens with a wh-word or an auxiliary, or ends in a question mark,
#: is a question about the run rather than a request to change it.
_INTERROGATIVE_RE = re.compile(
    r"^\s*(what|why|how|which|who|when|where|whose|is|are|was|were|does|do|did|"
    r"can|could|should|would|will|shall|has|have|had)\b",
    re.IGNORECASE,
)


def _is_interrogative(text: str) -> bool:
    """True when the turn reads as a question about the run."""
    stripped = str(text or "").strip()
    return bool(stripped.endswith("?") or _INTERROGATIVE_RE.match(stripped))


def _match_noun(text: str) -> tuple[str, str] | None:
    """The proposal noun in a phrase, as ``(alias, kind)``, or None.

    Longest alias wins, ties break on earliest position. That ordering is what
    keeps "water reservoir" from being shadowed by the shorter "reservoir", and
    makes "convert the road to permeable paving" read as *paving* (the longer,
    more specific phrase) rather than as a road. Hyphens are flattened first,
    the same normalisation `_match_region` uses.

    The noun may carry an English plural (``s``/``es``): "add two water
    reservoirs in the north" is a request, and a whole-word match on
    "reservoir" read it as ambiguous -- which is how a proposal-shaped turn came
    back as a result card. Matching the plural costs one optional group and keeps
    the single vocabulary (`ai_plan.EN_ALIASES`) rather than typing the plurals
    into it, where they would drift from the singulars.
    """
    lowered = text.lower().replace("-", " ")
    best_key: tuple[int, int] | None = None
    best: tuple[str, str] | None = None
    for alias, kind in NOUNS.items():
        found = re.search(rf"\b{re.escape(alias)}(?:e?s)?\b", lowered)
        if not found:
            continue
        key = (-len(alias), found.start())
        if best_key is None or key < best_key:
            best_key = key
            best = (alias, kind)
    return best


def parse_intervention_intent(question: str) -> str | None:
    """Normalise an intervention request, or None when none is recognised.

    Fires only on a recognised **verb** *and* a recognised **noun**, so
    "what changed?" (no verb) and "add a bike lane" (no proposal noun) both fall
    through to the chat. The region is optional and defaults to the whole area,
    because "add a water reservoir" is a complete request without one.

    Returns the normalised imperative, e.g. ``"add a water reservoir in the
    north"`` or ``"remove the road near the south"`` -- exactly the shape
    `agent.prompts` demands of ``intent_hint``.
    """
    text = str(question or "")
    if not text.strip() or not _VERB_RE.search(text):
        return None

    noun = _match_noun(text)
    if noun is None:
        return None
    _, kind = noun

    region_code = _match_region(text) or "all"
    region = REGION_WORDS.get(region_code, "the whole area")

    if _REMOVAL_RE.search(text):
        return f"remove the {KIND_WORD[kind]} near {region}"
    return f"add {KIND_PHRASE[kind]} in {region}"


def classify_turn(question: str, card: AnswerCard) -> AnswerCard:
    """Return the card with intent/intent_hint decided by code, not only by the model.

    Precedence (first match wins), and the model can never *lower* the intent:

      1. `parse_intervention_intent(question)`  -> intent="intervention",
         intent_hint=<normalised>, intent_source="parser" (or "both" when the
         model agreed).
      2. `card.intent == "intervention"`        -> keep the model's intent AND its
         hint, intent_source="model".
      3. `parse_massing_intent(question)`       -> intent="question" (massing is
         not a proposal).
      4. question is interrogative              -> intent="question".
      5. otherwise                              -> intent="ambiguous".

    `intent_source` records which rule fired, so the routing is auditable and a
    test can assert *why* a turn went where it did.
    """
    hint = parse_intervention_intent(question)
    model_says_intervention = card.intent == "intervention"

    # 1. The parser recognised an intervention. It can only ever raise, so this
    #    wins whether or not the model agreed; when both fired, say so.
    if hint and model_says_intervention:
        return card.model_copy(update={
            "intent": "intervention",
            "intent_hint": card.intent_hint or hint,
            "intent_source": "both",
        })
    if hint:
        return card.model_copy(update={
            "intent": "intervention",
            "intent_hint": hint,
            "intent_source": "parser",
        })

    # 2. The model decided, and the parser did not disagree. Keep its hint too.
    if model_says_intervention:
        return card.model_copy(update={"intent_source": "model"})

    # 3. A massing edit ("make it 25% taller") is a change, but not a proposal.
    if parse_massing_intent(question):
        return card.model_copy(update={
            "intent": "question", "intent_hint": None, "intent_source": "parser",
        })

    # 4. A question about the run.
    if _is_interrogative(question):
        return card.model_copy(update={
            "intent": "question", "intent_hint": None, "intent_source": "parser",
        })

    # 5. Neither clearly: let the client ask the user to disambiguate.
    return card.model_copy(update={
        "intent": "ambiguous", "intent_hint": None, "intent_source": "parser",
    })


# --------------------------------------------------------------------------- #
# The refusal path -- one mechanism, two detectors, two messages (§3.2, §3B)
# --------------------------------------------------------------------------- #
#
# Why this lives here and not in the router: `classify_turn` already decides what
# a sentence *means* from the server's one vocabulary, and both detectors are
# statements about a sentence against that vocabulary. The router only raises the
# 422; the judgement is this pure function, so a test can drive it with no HTTP
# and no model.
#
# Why the lists are generated and never typed (§3B.3): three kind vocabularies
# disagree by design -- `KIND_WORD` (6, the router's nouns), `POOL_FOR_KIND` (5,
# what /ai-plan can actually propose), `STYLE_FOR_KIND` (6, what the renderer can
# draw). Typing the list, or reading the wrong one, produces a message that lies:
# naming `flood_storage` ("floodable ground") would promise a capability this
# endpoint does not have, because it is in `KIND_WORD` but has no pool in
# `POOL_FOR_KIND` and so no member in the `CHOOSE_TOOL` enum.

def names_a_creation(text: str) -> bool:
    """True when the sentence contains a pinned *creation* verb.

    The chat seam (§3B.5) selects the unsupported-capability card on this,
    gated on the classifier already having returned `ambiguous`/`parser`, so the
    two together mean exactly "a creation was asked for and no supported noun
    matched".
    """
    return bool(_CREATION_RE.search(str(text or "")))


def is_removal_instruction(text: str) -> bool:
    """True when the sentence contains a pinned removal verb.

    The detector for §3.2, and deliberately just the verb class: "/ai-plan can
    only create" is true whatever the noun, so a removal with no supported known
    noun ("remove the masts") must still be refused rather than quietly created.
    """
    return bool(_REMOVAL_RE.search(str(text or "")))


def is_unsupported_capability(text: str) -> bool:
    """True when a creation is asked for and no *proposable* noun matches.

    The detector for §3B: a creation verb is present, the turn is not a question,
    and no noun maps to a kind with a grounding pool. A noun that maps only to a
    kind the endpoint cannot propose (`flood_storage` -- "wetland", "floodable
    park") does **not** count as a match: /ai-plan has no such member, so leaving
    it to the forced tool is exactly the substitution this guard closes.

    The predicate is *also true of a synonym the vocabulary has not learned yet*.
    That is why the message it produces is a fact about the system and never a
    claim about the sentence -- see :func:`capability_statement`.
    """
    t = str(text or "")
    if not t.strip() or _is_interrogative(t) or not _CREATION_RE.search(t):
        return False
    noun = _match_noun(t)
    return noun is None or noun[1] not in POOL_FOR_KIND


def capability_create_words() -> list[str]:
    """The kinds /ai-plan can create, as words, generated from `POOL_FOR_KIND`.

    Built by mapping the *pool* dictionary's keys through `KIND_WORD`, so it
    cannot drift from the enum `CHOOSE_TOOL` is built from (`sorted(set(
    POOL_FOR_KIND))`), and `flood_storage` is absent by construction rather than
    by remembering to omit it.
    """
    return [KIND_WORD[kind] for kind in sorted(set(POOL_FOR_KIND))]


def capability_statement() -> str:
    """The capability boundary, every clause a fact about *the system*.

    Written so it answers "add a school" (the reshape line shows what buildings
    are for) without ever asserting that a school is impossible -- we do not know
    that; we know only that no supported noun matched. It also answers a synonym
    like "plant a green corridor": `green space` is listed, so the retry is
    obvious. See §3B.4 -- a message that told those users their request is
    unsupported would be worse than today's silence.
    """
    ops = MASSING_TOOL["input_schema"]["properties"]["edits"]["items"]["properties"]["op"]["enum"]
    regions = ", ".join(sorted(REGION_FRACTIONS))
    return (
        "I could not match this to an intervention I can create.\n"
        "  create: " + ", ".join(capability_create_words()) + "\n"
        "  reshape existing buildings: " + ", ".join(ops) + "\n"
        "  regions it understands: " + regions + "\n"
        "Existing buildings come from the surveyed city model and can be "
        "reshaped, not created.\n"
        "If you meant one of the above, name it - e.g. \"add a water reservoir "
        "in the north\"."
    )


def removal_refusal_detail(scene_id: str, text: str) -> str:
    """The 422 `detail` for a removal sent to /ai-plan (§3.2).

    Names the route that *does* honour a removal, so the refusal is actionable
    rather than merely negative, and says so explicitly when the sentence mixes a
    removal with an addition -- silently executing the addition half of a
    destructive+constructive sentence is the same class of lie as the inversion
    it replaces.
    """
    lines = [
        "I cannot take anything away on this endpoint: /ai-plan only creates "
        "interventions, so a removal here would build the very thing you asked "
        "to remove.",
        f"Removals are handled by POST /api/scenes/{scene_id}/sandbox/"
        "resolve-removal.",
    ]
    if _CREATION_RE.search(str(text or "")):
        lines.append(
            "This sentence mixes a removal with an addition; send the two "
            "instructions separately so each reaches the route that honours it.")
    lines.append("This endpoint can create: "
                 + ", ".join(capability_create_words()) + ".")
    return "\n".join(lines)


def refusal_detail(scene_id: str, instruction: str) -> str | None:
    """The shared refusal path: the 422 `detail`, or None to proceed.

    One mechanism with two detectors and two distinguishable messages. They are
    kept separate on purpose: a client grouping by status alone must still be
    able to tell "you asked to destroy -- a different endpoint" from "you asked
    to create something this endpoint does not make". Removal is checked first,
    so a mixed sentence is refused as a removal (its destructive half is the
    dangerous one) rather than as a missing creation.
    """
    if is_removal_instruction(instruction):
        return removal_refusal_detail(scene_id, instruction)
    if is_unsupported_capability(instruction):
        return capability_statement()
    return None
