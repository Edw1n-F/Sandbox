"""The sandbox preview: what a change would do, before it is committed.

## What this is

The dashed layer already shows the **shape** of a proposal. This shows its
**consequence**: it composes *what is on the sandbox now* with the *supplied
candidates*, runs the project's own ledger over both, and returns three states
per figure — now / with the change / the difference.

## What it deliberately is not

It is **not a run**. `run_scenario` writes overlay PNGs and their sidecars,
prunes the run history to 20 entries, mints a `runId` and calls a language model
for an explanation card. A preview must do none of those, and adding a
`dryRun` flag to `/run` would make one endpoint return two shapes with different
side effects — the shape-failure class that has already produced two live bugs
in this project. So this calls the ledger directly and skips everything else.

The response therefore has **no `runId`, no `overlayUrls` and no
`explanationCard` key at all** — absent by construction, not empty. An empty
`runId` is a value a client can render; a missing key cannot be.

## The three deltas, and why they must never be conflated

| Figure | Definition |
|---|---|
| `runDelta` | scenario − **today's land cover** (no interventions) — the run's published meaning |
| `now` | the **current scenario state**: staged plan + toggled projects, measured against today |
| `previewDelta` | preview − `now` = the **marginal** effect of the candidates |

Once prior work exists `previewDelta != runDelta`, and they differ by exactly the
prior work. Quoting the marginal figure under the key `delta` would publish the
run's number while showing the change's number — a wrong figure under a `source`
string that points at real code, which this project names as its worst failure
mode. So `previewDelta` does not reuse the key `delta`, both are present, and
each carries its own source naming the two ledger readings it is the difference of.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: The preview's own honesty label, stated on every response and deliberately
#: distinct from a run's `SCREENING_LIVE_RUN_FLOOD_EXTENT`: a preview whose
#: numbers look like a run's on the wire is the confusion this label prevents.
PREVIEW_STATUS = "PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE"

#: What the preview measures, and — the part that matters — what it does not.
#: The level-pool ledger does not consume `tideState`, so the route does not
#: accept it: accepting an input it ignores would imply the preview reflects the
#: tide. Declining a figure is honest; approximating it is not.
PREVIEW_SCOPE = (
    "the level-pool ledger only: runoff, unstored volume, storage capacity, "
    "green, paved, impervious share, green-and-water area. NOT the flood "
    "extent, NOT the overlay, NOT the surface-temperature field."
)

PREVIEW_WARNING = (
    "This is a preview of an UNCOMMITTED change. Nothing has been applied and "
    "nothing has been saved. The arithmetic is the ledger's own and is the "
    "number a run would produce, but this is not a run's record: there is no "
    "runId, no overlay and no result card. " + PREVIEW_SCOPE
)

#: The figures a preview reports, in a stable order. Named here rather than read
#: off one ledger call, because `previewDelta` must cover exactly the keys both
#: readings have -- a key present in one and absent in the other is a difference
#: against nothing.
_LEDGER_KEYS: tuple[str, ...] = (
    "runoffM3", "unstoredM3", "storageCapacityM3", "greenAreaM2",
    "pavedAreaM2", "imperviousShare",
)


def compose_features(scene_id: str, plan_ids: Iterable[str] | None = None,
                     staged: Iterable[Mapping[str, Any]] | None = None,
                     ) -> list[dict[str, Any]]:
    """Prior work as a feature list, in a **stable order** (T01).

    The staged plan's pinned rows first, in id order, then the toggled projects
    in the order given. Stable because `interventionsApplied`'s order — and hence
    any `claimed |=` accumulation downstream — must not vary between two
    identical calls: the preview is asserted byte-identical on repeat.

    The plan side reuses `routers.scenes.intervention_features_many` rather than
    composing a second time, so a preview and a run see the same features in the
    same shape.
    """
    out: list[dict[str, Any]] = []
    if staged is None:
        from app.services import pins

        rows = [f for f in pins.staged_features(scene_id) if f.get("pinned")]
        staged = sorted(rows, key=lambda r: str(r.get("id") or ""))
    out.extend(dict(f) for f in staged)

    ids = [p for p in (plan_ids or []) if p]
    if ids:
        from app.routers.scenes import intervention_features_many

        seen = {_feature_id(f) for f in out}
        for feature in intervention_features_many(ids):
            if _feature_id(feature) in seen:
                continue
            out.append(feature)
    return out


def _feature_id(feature: Mapping[str, Any]) -> str:
    """A feature's identity, across the two spellings the codebase uses."""
    for key in ("id", "featureId", "feature_id"):
        if feature.get(key):
            return str(feature[key])
    params = feature.get("params") or {}
    return str(params.get("siteId") or params.get("site_id") or "")


def preview(scene_id: str,
            interventions: Iterable[Mapping[str, Any]] | None = None,
            values: Iterable[Mapping[str, Any] | None] | None = None,
            plan_ids: Iterable[str] | None = None,
            rainfall_mm: float = 450.0) -> dict[str, Any]:
    """Measure the candidates against what is on the sandbox. Writes nothing.

    The `now` reading and the `preview` reading come from the **same**
    `ledger_for_scenario` call, so "where am I" and "what would change" cannot
    disagree by construction — equality with an eventual run is a property of
    there being one implementation, not of two agreeing.

    Each candidate is run through `sandbox.apply_inputs` first, the same
    function `/ai-plan/apply` uses: the input card changes the ledger
    (`storageDepthM` is read by `apply_interventions`), so a preview that ignored
    the card's answers would preview a *different* intervention than the one
    about to be confirmed.
    """
    from app.services import sandbox
    from app.services.sandbox_model import ledger_for_scenario, load_grids

    grids = load_grids()
    if grids is None:
        raise PreviewUnavailable(
            "no ledger grid is available (run scripts/make_wetgrid.py), so no "
            "preview can be measured")

    now_features = compose_features(scene_id, plan_ids)
    candidates = [_with_inputs(f, i, values) for i, f in enumerate(
        interventions or [])]

    now = ledger_for_scenario(grids, now_features, rainfall_mm)
    with_change = ledger_for_scenario(grids, now_features + candidates,
                                      rainfall_mm)

    now_block = dict(now["scenario"])
    preview_block = dict(with_change["scenario"])
    return {
        "status": PREVIEW_STATUS,
        "warning": PREVIEW_WARNING,
        "scope": PREVIEW_SCOPE,
        "rainfallMm": rainfall_mm,
        "now": now_block,
        "preview": preview_block,
        "previewDelta": _delta(preview_block, now_block),
        "runDelta": _run_delta(with_change),
        "interventionsApplied": with_change.get("interventionsApplied", []),
        "interventionsSkipped": with_change.get("interventionsSkipped", []),
        "sources": {
            "terrain": getattr(grids, "terrain_source", None),
            "landCover": getattr(grids, "landcover_source", None),
        },
        "ledgerSource": (
            "sandbox_model.ledger_for_scenario / apply_interventions — the "
            "same arithmetic a run uses"),
    }


def _with_inputs(feature: Mapping[str, Any], index: int,
                 values: Iterable[Mapping[str, Any] | None] | None
                 ) -> dict[str, Any]:
    """One candidate with the input card's answers applied (T01, §1.6).

    The candidates arrive over the wire, where the type is camelCase
    (``interventionType``); the ledger reads snake_case (``intervention_type``).
    Both spellings are normalised here for the same reason `scenes.py`'s
    composition accepts both: guessing one wrong makes every candidate read as
    ``unknown``, and the ledger then reports the whole batch as skipped for
    having no land-cover effect -- which looks like a data problem, not a
    spelling one.
    """
    from app.services import sandbox

    answers: dict[str, Any] | None = None
    if values is not None:
        listed = list(values)
        if index < len(listed):
            answers = listed[index]
    raw = dict(feature)
    kind = str(raw.get("intervention_type") or raw.get("interventionType")
               or raw.get("kind") or "")
    params = sandbox.apply_inputs(kind, raw.get("params"), answers)
    raw["intervention_type"] = kind
    raw["params"] = params
    return raw


def _delta(after: Mapping[str, Any], before: Mapping[str, Any]
           ) -> dict[str, float]:
    """Per-key ``after - before``, rounded, over the keys both readings share."""
    out: dict[str, float] = {}
    for key in _LEDGER_KEYS:
        if key not in after or key not in before:
            continue
        out[key] = round(float(after[key]) - float(before[key]), 4)
    return out


def _run_delta(ledger: Mapping[str, Any]) -> dict[str, float]:
    """The run's own delta, under its own keys — never merged with `previewDelta`.

    `ledger_for_scenario` already publishes this as `deltaUnstoredM3` and
    friends; it is surfaced under a distinct key so a client can show both and
    see that they differ by exactly the prior work.
    """
    out: dict[str, float] = {}
    for key in ("deltaUnstoredM3", "deltaRunoffM3", "deltaGreenAreaM2"):
        if key in ledger:
            out[key] = float(ledger[key])
    return out


class PreviewUnavailable(RuntimeError):
    """The preview cannot be measured here. The router turns this into a 422."""
