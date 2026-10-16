"""The confirm gate's re-gate: what an edit did, before Apply (Phase 2 R19.1).

## Why this exists

The gate hands the client a set of proposals and the client owns them from then
on. Between the gate and the Apply, a planner can change a parameter -- most
often the usable storage depth, which is the one number ``apply_interventions``
reads. Without this call the planner has no way to see what that did until the
run has already been re-solved, and no way at all to see that an edit made a
placement illegal until the Apply refuses it.

So this runs the **same** constraint engine the gate ran, over the proposals as
they now stand, and reports the result. It writes nothing, persists nothing and
calls no model.

## The two rules that shape the code

**R15 -- a parameter edit does not move geometry.** A depth is metres over
cells that are already fixed. So the response says which parameters could have
moved a cell (``geometryAffectingParameters``, empty on this path) and which
changed only a value (``valuesAffectingOnly``). The client renders the
distinction rather than inferring it, and the claim is checkable: the tests
assert the geometry is byte-identical across an edit.

**R20 -- an out-of-range depth is a 422, not a reason code.** ``REASON_CODES``
are placement predicates on geometry; a depth of 40 m is a malformed request.
Overloading a reason code would make a UI group a client typo with a physical
refusal, so the range check happens here and raises ``RevalidationError``,
which the router turns into a 422 naming the parameter, the value, the range
and the proposal's ``siteId``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.services import proposal_constraints as constraints

#: The one parameter a planner can edit that changes no cell. It is named as a
#: full wire path so the client can match it against ``parameters`` without a
#: mapping table, and it is a list rather than a bare string so a second
#: value-only parameter is additive rather than a shape change.
VALUE_ONLY_PARAMETERS: tuple[str, ...] = ("parameters.storageDepthM",)


class RevalidationError(ValueError):
    """A malformed edit. The router turns this into a 422 (R20)."""


def revalidate(scene_id: str, proposals: list[dict[str, Any]],
               answers: Mapping[str, Any] | None = None,
               ) -> dict[str, Any]:
    """Re-gate ``proposals`` as they now stand. Writes nothing.

    Returns ``accepted`` (the gated proposals), ``rejected`` (the refusals, one
    per placement that no longer survives) and the two honesty numbers an edit
    moves: the gross storage the set claims and the run's own net capacity
    change. Both are recomputed so the consequence is visible before Apply, and
    they are returned as separate fields because conflating them would overstate
    every intervention built on ground that already stored something.
    """
    from app.services.ai_plan import _constraint_inputs

    _reject_malformed(proposals)

    parts = _constraint_inputs(scene_id)
    if parts is None:
        raise RevalidationError(
            "no ledger grid is available (run scripts/make_wetgrid.py), so "
            "these proposals cannot be checked; nothing was changed")
    grids, occupied, claimed = parts

    accepted, rejected = constraints.validate_batch(
        grids, [_client_feature(p) for p in proposals], occupied, claimed,
        answers=answers)

    accepted = [_carry_clip_disclosure(p, proposals) for p in accepted]

    gross = _explicit_storage(grids, accepted)
    net = None
    try:
        from app.services.sandbox_model import ledger_for_scenario

        ledger = ledger_for_scenario(grids, accepted)
        net = float(ledger["scenario"]["storageCapacityM3"]
                    - ledger["baseline"]["storageCapacityM3"])
    except Exception:  # noqa: BLE001 - a ledger failure must not fail the gate
        # The constraint result is still the answer the planner asked for; a
        # missing net figure is reported as missing rather than as zero, which
        # would read as "this edit changes nothing".
        net = None

    questions = constraints.site_questions(accepted, answers=answers)
    return {
        "proposals": accepted,
        "rejected": rejected,
        "questions": questions,
        "explicitStorageAddedM3": gross,
        "netStorageCapacityChangeM3": None if net is None else round(net, 1),
        "geometryAffectingParameters": [],
        "valuesAffectingOnly": list(VALUE_ONLY_PARAMETERS),
        "warning": _warning(accepted, rejected, net is None),
    }


def _carry_clip_disclosure(proposal: dict[str, Any],
                           submitted: list[Any]) -> dict[str, Any]:
    """Keep the clip count the gate published, on a re-gate of the same geometry.

    ``clippedCells`` counts the difference between what the *pool offered* and
    what the user is shown -- buildings plus any fragment that ``contour_ring``
    stranded when it returned the largest ring only. A client echoes back the
    **served** geometry, which is already clipped, so a re-gate recomputed from
    that geometry sees nothing left to remove and reports ``0``.

    That number silently going to zero would be a disclosure regression on
    exactly the edit path this endpoint exists for: a planner changing a depth
    would watch "2 cells under buildings were removed" become "no cells were
    removed" without any ground changing. So the gate's own figure is carried
    through when the geometry is the same one, and only the geometry-identical
    case qualifies -- a proposal whose geometry actually changed is recounted,
    because then the old number genuinely does not apply.

    Matched on the geometry's coordinates, not its index: a refused proposal
    drops out of the accepted list, so position is not identity.
    """
    carried = {_geometry_key(p): p for p in submitted}
    previous = carried.get(_geometry_key(proposal))
    if previous is None:
        return proposal
    constraints_before = (previous.get("constraints") or {})
    if not isinstance(constraints_before, Mapping):
        return proposal
    # Both spellings: the accepted side is the raw camelCase dict
    # `_constraints_block` builds, while a proposal that came in over the wire
    # has been through `ProposalConstraints` and dumps snake_case. Reading one
    # spelling only would make this silently do nothing on the real path while
    # passing on a dict-shaped test.
    served_before = _first_of(constraints_before, "clippedCells", "clipped_cells")
    if not served_before:
        return proposal
    if _first_of(proposal["constraints"], "clippedCells", "clipped_cells"):
        return proposal  # this run still clipped something; recount stands
    constraints_after = dict(proposal["constraints"])
    constraints_after["clippedCells"] = int(served_before)
    constraints_after.pop("clipped_cells", None)
    constraints_after["clippedFraction"] = float(
        _first_of(constraints_before, "clippedFraction", "clipped_fraction") or 0.0)
    return {**proposal, "constraints": constraints_after}


def _first_of(mapping: Mapping[str, Any], *keys: str) -> Any:
    """The first present key's value, across spellings."""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _geometry_key(proposal: Any) -> str:
    """A stable identity for a proposal's geometry, for matching across a call."""
    if isinstance(proposal, Mapping):
        raw = proposal
    else:
        dump = getattr(proposal, "model_dump", None)
        raw = dump(by_alias=False) if callable(dump) else dict(proposal)
    return json.dumps(raw.get("geometry"), sort_keys=True, separators=(",", ":"))


def _client_feature(proposal: Any) -> dict[str, Any]:
    """One proposal as the plain feature dict ``validate_batch`` expects.

    The wire ``InterventionProposal`` is a superset of a feature -- it adds the
    derived display fields and the four §2.2 blocks -- so it is narrowed rather
    than passed straight through. ``params`` is carried verbatim because it is
    what ``apply_interventions`` reads, and an edit that did not survive to
    ``params`` is an edit that will not reach the run.
    """
    if isinstance(proposal, Mapping):
        raw = dict(proposal)
    else:
        dump = getattr(proposal, "model_dump", None)
        raw = dump(by_alias=False) if callable(dump) else dict(proposal)
    return {
        "geometry": raw.get("geometry"),
        "intervention_type": raw.get("intervention_type")
        or raw.get("interventionType"),
        "params": raw.get("params") or {},
        "source": raw.get("source", "ai"),
        "style": raw.get("style") or {},
        "siteId": raw.get("site_id") or raw.get("siteId"),
        "requested": raw.get("requested") or (raw.get("params") or {}).get("requested"),
    }


def _reject_malformed(proposals: list[dict[str, Any]]) -> None:
    """Raise on a depth outside the accepted range, naming it (R20).

    Checked here rather than left to the constraint engine, which has no
    vocabulary for it: a depth is not a property of a placement, and a reason
    code that meant "you typed 40" would sit in the same closed set as
    ``BUILDING_OVERLAP`` and be grouped with it in every UI that reads it.
    """
    from app.services.sandbox_model import (
        MAX_STORAGE_DEPTH_M,
        MIN_STORAGE_DEPTH_M,
        depth_is_acceptable,
    )

    for partner in proposals:
        raw = partner if isinstance(partner, Mapping) else _client_feature(partner)
        params = raw.get("params") or {}
        depth = params.get("storageDepthM", params.get("storage_depth_m"))
        if depth is None:
            continue
        if depth_is_acceptable(depth):
            continue
        if not isinstance(depth, (int, float)) or isinstance(depth, bool):
            raise RevalidationError(
                "storageDepthM %r is not a number (proposal %s); pass a depth "
                "in metres, or omit it to keep the class default"
                % (depth, _site_label(raw)))
        raise RevalidationError(
            "storageDepthM %r is outside the %s-%s m range this screening "
            "model accepts (proposal %s); pass a depth in metres inside that "
            "range, or omit it to keep the class default"
            % (depth, MIN_STORAGE_DEPTH_M, MAX_STORAGE_DEPTH_M,
               _site_label(raw)))


def _site_label(raw: Mapping[str, Any]) -> str:
    """Which proposal the complaint is about, for a client that sent several."""
    for key in ("siteId", "site_id"):
        if raw.get(key):
            return str(raw[key])
    params = raw.get("params") or {}
    for key in ("siteId", "site_id"):
        if params.get(key):
            return str(params[key])
    return "without a siteId"


def _explicit_storage(grids: Any, accepted: list[dict[str, Any]]
                      ) -> float | None:
    """The brief's gross ``area x depth`` over the accepted set (R4).

    ``None`` when no accepted proposal carries a depth, rather than ``0.0``: a
    scenario with nothing to add has no gross storage, and zero would read as
    "measured it, and it is nothing".
    """
    total = None
    for proposal in accepted:
        params = proposal.get("params") or {}
        depth = params.get("storageDepthM", params.get("storage_depth_m"))
        if depth is None:
            continue
        cells = (proposal.get("size") or {}).get("cells")
        if not cells:
            continue
        contribution = float(cells) * float(grids.cell_area_m2) * float(depth)
        total = contribution if total is None else total + contribution
    return None if total is None else round(total, 1)


def _warning(accepted: list[dict[str, Any]], rejected: list[dict[str, Any]],
             net_unavailable: bool) -> str:
    parts = [
        "Screening re-check of the edited proposals. Nothing was saved: this "
        "call reports what the edit does, and the run itself is unchanged "
        "until you apply."
    ]
    if rejected:
        parts.append(
            "%d placement(s) no longer satisfy the constraints and would be "
            "refused on apply." % len(rejected))
    if accepted:
        parts.append(
            "A parameter edit moves no geometry: the stored cells and the caps "
            "are what they were, and only the values below changed.")
    if net_unavailable:
        parts.append(
            "The run's net storage change could not be recomputed for this "
            "edit, so it is reported as unknown rather than as zero.")
    return " ".join(parts)
