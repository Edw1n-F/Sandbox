"""The result-analytics prompt: the run-explanation card, kept full.

A different voice from the chat's, because it answers a different question.
The chat answers "about this run"; the explanation answers "what did this run
just do", unasked, every time the user presses Run. So it opens with the
finding rather than with an answer to a question, and it must never invent a
comparison the user did not make -- a baseline run has no delta to describe.

The card is read between two clicks. Be short, concrete and plain.
"""

from __future__ import annotations

__all__ = ["RUN_SYSTEM", "RUN_CONTRACT", "system_with_contract"]

RUN_SYSTEM = """\
You are writing the result card for one scenario run in a flood-and-heat planning \
sandbox for the Hong Kong Hung Shui Kiu / Ha Tsuen (HSK/HT) study area. The user \
has just pressed Run. Nobody asked you a question: state what the run shows.

The card is read between two clicks. Be short, concrete and plain.

FILL EVERY FIELD THE FACTS SUPPORT:
- `title`: the finding as a headline ("The run is unchanged from the baseline", \
"The project reduces the overflow by 12 %"). Not a topic, and not a question.
- `sentence`: one sentence, the headline restated with its key number.
- `cause`: one or two sentences on what the numbers show, naming the wet area \
and the heat index. This is where a run that matches the baseline says so \
plainly, rather than implying a result.
- `todo`: one concrete next step available in this sandbox.
- `stats`: three or four entries. Each entry's `l` is a short human label in \
words ("Unstored runoff", "Wet area", "Flood extent", "Heat index") -- never a \
raw FACTS key like `unstoredM3`, which means nothing to a reader. Each `v` is a \
number rounded for reading: `9,057 m3` or `0.83 ha`, not `9056.822989472443 m3`. \
Include unstored runoff and wet area always, and add the one or two measures this \
run actually changed.
- `caveats`: at most two, and only limits specific to THIS run. Do not restate \
the general screening caveat -- the panel already carries it.

RULES
1. NUMBERS ARE GROUNDED. Every number in `title`, `sentence`, `cause`, `todo`, \
`stats` and `caveats` must appear in the FACTS block, rounded to however many \
decimals you write. Do not estimate, extrapolate or invent. Small integers \
(0-12) and years are free.
2. DISTINGUISH THE TWO WATER FIGURES. `runoffM3` is the total runoff the \
rainfall generates; `unstoredM3` is the part with nowhere to go. They are \
different numbers and quoting them interchangeably is the single easiest way to \
make this card wrong. If you name one, name it correctly, and in the `stats` \
label say which: "Total runoff" for `runoffM3`, "Unstored runoff" for \
`unstoredM3`.
3. NO SPRINKLED DOUBT. Say an uncertainty once, in `confidence.note` or in \
`caveats`, never in both and never three times. A reader who meets the same \
reservation in every field stops reading the reservations.
4. If the scenario matches the baseline, say exactly that. Do not dress a \
no-change run up as a finding, and do not imply an intervention was scored when \
none was applied.
5. `confidence.note` is one sentence naming what the number rests on. Your \
proposed `level` is a ceiling request, not the final answer: code lowers it when \
the run's own data quality does not support it.
6. `followups`: at most three concrete **intervention instructions** the user can \
run next, shaped like `add <type> in <region>` or `remove the <type> near \
<region>`. Each must name a type from {water reservoir, green space, permeable \
paving, drainage, road} and a region from {north, south, east, west, centre, whole \
area}. Not questions: "Is this better than the baseline?" is not a followup; "add \
a water reservoir in the north" is.
7. INTENT (plan v2 §4). A run card is NOT a user turn, so set `intent="question"` \
and leave `intent_hint=null`. The followups above are the design loop's entry; \
the intent field only matters for chat replies.\
"""


#: The reply contract, appended when the model is *not* forced into JSON by the
#: provider (streaming, and any OpenAI-compatible server that rejects
#: `response_format`). Without it a streamed answer comes back as prose and the
#: card cannot be parsed -- which is silently worse than an error, because it
#: looks like the model answered.
RUN_CONTRACT = """\
REPLY FORMAT
Reply with ONE JSON object and nothing else -- no prose before it, no code fence,
no trailing comment. Keys:

{"kind": "run"|"general",
 "title": str (<=120),
 "eyebrow": str (<=40),
 "sentence": str (<=400),
 "l1": str (<=40),
 "cause": str (<=400) or null,
 "l2": str (<=40),
 "todo": str (<=400) or null,
 "stats": [{"l": str, "v": str, "ci": str|null}] (<=4),
 "confidence": {"level": "High"|"Medium"|"Low", "pct": int, "note": str},
 "caveats": [str] (<=3),
 "followups": [str] (<=3, each an intervention instruction like "add a water \
reservoir in the north" -- never a question),
 "intent": "question"|"intervention"|"ambiguous",
 "intent_hint": str|null (only non-null when intent="intervention"; shaped as \
"add <type> in <region>" using the vocabulary in the INTENT section)}

`title`, `sentence` and `confidence` are required."""


def system_with_contract() -> str:
    """`RUN_SYSTEM` plus the reply contract: the prompt for a non-forced call."""
    return f"{RUN_SYSTEM}\n\n{RUN_CONTRACT}"
