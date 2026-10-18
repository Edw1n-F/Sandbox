"""Natural-language -> 3D interventions (spec §1.1, ADR-030).

## What the spec already hands us

`技术文档` anticipates this exactly. Three fields exist for it and were reserved
even though nothing filled them:

- ``plan.nl_instruction`` -- "若为 AI 生成，记录原始自然语言指令"
- ``plan.source`` / ``intervention.source`` ∈ ``{manual, ai}``
- ADR-030: an AI-originated feature is **always** ``status="draft"``, and the
  engine refuses to compute on draft geometry.

And §3.3.2 is explicit that this is not a separate pipeline: "用户绘制与 AI
方案落地共用同一套「归一化 → 校验 → 3D 渲染」管线" -- user-drawn and
AI-proposed features share one normalise/validate/render path. So the output of
this module is nothing more exotic than GeoJSON that the existing endpoints
already accept, and it renders through the layer built for interventions.

## The honesty rule this module is built around

An LLM asked to "add a reservoir" will happily emit plausible-looking
coordinates anywhere on the map. That is worse than useless in a planning tool,
because it *looks* like a site investigation. So every feature produced here is
**grounded in the analysis that already exists** -- a reservoir goes in the
deepest connected basin of the computed wet grid, permeable paving goes on the
most impervious contiguous block -- and records the raster it came from in
``params.basis``. If no grounding data is available, the module says so and
refuses to invent geometry.

## Scope: the missing idea, and why it is the important one

An LLM asked to "make the district 25% taller" and then "set the western blocks
to 60 m" produces two sentences that differ in exactly one respect: **where**.
The first version of the massing tool had no way to express that, so ``scope``
did not exist and both sentences collapsed onto ``scale_all`` for the entire
study area. The second request silently returned the first request's answer,
which is the worst failure mode this module has: not an error, not a wrong
number, but a plausible 3D result that answers a different question.

So every massing operation now carries a **scope**, and it is resolved in this
module from three sources and nowhere else:

- ``scope="west"`` -- one of ten named regions of the locked study area, as a
  fraction of the bbox. Deterministic arithmetic over a constant we already own.
- ``site_id="water_reservoir#0"`` -- a site from the grounded catalogue, whose
  polygon the analysis already derived.
- neither -- the whole study area.

The model supplies a *name* or an opaque id. It never supplies a coordinate, so
the guarantee that made site selection safe survives the addition of spatial
scope: **the model chooses where to point, the data decides where that is.**

## Two providers, and why

``ANTHROPIC_API_KEY`` decides which runs. Both produce the same structures.

- :class:`LLMProvider` -- a real Anthropic Messages API client. This is the
  GenAI path the hackathon asks for.
- :class:`HeuristicProvider` -- a deterministic parser, **not AI**, which needs
  no credentials. It exists because a demo that dies without an API key is a
  demo that dies, and because the non-AI baseline is what any AI output has to
  be compared against to be worth anything.

The LLM path is tested against recorded fixtures rather than live calls, so it
is exercised on every test run without a key or a network.
"""

from __future__ import annotations

import json
import math
import re
import sys
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

# This file lives at backend/app/services/, so three parents to reach backend/.
# (The scripts/ modules use two, being one level shallower -- an easy off-by-one
# that silently puts backend/app on the path instead of backend/.)
BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.config import settings  # noqa: E402

# The contour geometry the candidate sites are drawn with. Imported, never
# re-derived: a second implementation of "the shape of the cells I found"
# would be a second chance to disagree with the ledger's own rasteriser
# about which cells an intervention actually covers.
from app.services.intervention_geometry import (  # noqa: E402
    connected_components,
    contour_ring,
    polygon_area_ha,
    ring_centroid,
)

# English aliases, because the brief and the model are both English-speaking.
EN_ALIASES: dict[str, str] = {
    "green space": "green_space",
    "park": "green_space",
    "urban green": "green_space",
    "tree planting": "green_space",
    "permeable paving": "permeable_paving",
    "permeable pavement": "permeable_paving",
    "water reservoir": "water_reservoir",
    "flood storage": "water_reservoir",
    "retention basin": "water_reservoir",
    "storage basin": "water_reservoir",
    "drain": "drainage",
    "drainage": "drainage",
    "channel": "drainage",
    "swale": "drainage",
    "road": "road",
    "street": "road",
    "railing": "road",
    # Synonyms the routing test found missing (16 Oct). They live here, in the one
    # vocabulary both `parse_intent` and `agent/intent.py::_match_noun` read,
    # rather than in the classifier -- two vocabularies would drift, and the
    # classifier's noun list is derived from this dict by design. "basin" bare
    # (not "retention basin") and "wetland" were the two that read as ambiguous,
    # so a proposal-shaped turn came back as a result card.
    "green corridor": "green_space",
    "tree corridor": "green_space",
    "permeable plaza": "permeable_paving",
    "basin": "water_reservoir",
    "floodable park": "flood_storage",
    "wetland": "flood_storage",
    "wind": "wind",
    "windbreak": "wind",
    "ventilation": "wind",
    "podium": "podium",
    # "rotate" / "reorient" / "angle" used to be here, mapped to a `reorient`
    # kind that has no grounding pool -- so the phrase parsed successfully and
    # then resolved to nothing. They are real operations on the massing now, so
    # they are handled by parse_massing_intent() instead. Leaving them here would
    # mean one sentence producing a phantom intervention intent as well.
    "reservoir": "water_reservoir",
    "pond": "water_reservoir",
}


@dataclass
class Grounding:
    """Candidate sites derived from the analysis, not invented.

    Everything the generator can place, with the evidence for each. If this is
    empty the generator has nothing legitimate to do and must say so.
    """

    bounds: tuple[float, float, float, float] | None = None
    reservoir_sites: list[dict[str, Any]] = field(default_factory=list)
    green_sites: list[dict[str, Any]] = field(default_factory=list)
    paving_sites: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The loaded Overlay, kept so placement can re-read the paved mask without
    # loading it a second time. Not part of the prompt: it is numpy, not data.
    overlay: Any = field(default=None, repr=False, compare=False)

    def any_available(self) -> bool:
        return bool(self.reservoir_sites or self.green_sites or self.paving_sites)


class PlanGenerationError(RuntimeError):
    """Raised when a proposal cannot be grounded. Never swallowed."""


# --------------------------------------------------------------------------- #
# Grounding: candidate sites from the analysis
# --------------------------------------------------------------------------- #


def _claimed_for_pools(scene_id: str,
                       plan_ids: Any = None) -> set[tuple[int, int]]:
    """Prior work as a cell set, for the proposer's pool filter (Phase 4 T04b).

    Returns the claimed cells of the scene's staged plan **plus every toggled
    project** the request named. Returns an empty set rather than raising when
    the ledger grid is unavailable: the pools are then built unfiltered and the
    gate still refuses anything that collides, which is the honest degradation --
    a proposer that cannot see prior work offers a site the gate will refuse,
    but it does not invent one.

    Failure is deliberately *not* silent: the reason is appended as a grounding
    note by the caller, so "could not check" never reads as "nothing is there".
    """
    try:
        from app.services import proposal_constraints as _constraints

        return _constraints.claimed_cells_for(scene_id, plan_ids=plan_ids)
    except Exception:  # noqa: BLE001 - see the docstring
        return set()


def load_grounding(claimed: Any = None) -> Grounding:
    """Derive candidate intervention sites from the DEM, land cover and wet grid.

    This is the part that makes an AI-generated plan defensible. Every candidate
    carries the raster it came from, and the geometry handed back to the model is
    a *choice among these sites*, never a free coordinate.

    ``claimed`` (Phase 4 T04b, additive) is the set of cells already covered by
    prior work -- the staged plan's pinned rows plus every toggled project. A
    candidate whose ground is (partly) claimed is filtered out here, at
    selection, so the proposer **skips to the next candidate** instead of
    offering a site the gate will then refuse with ``OVERLAPS_STAGED``.

    That dead-end was the live defect: with prior work on the sandbox, the
    proposal path returned an empty list while free candidates existed. The
    overlap guard is **not** weakened by this -- it stays exactly as it is, as
    the backstop for anything that still collides (a coordinate request, or a
    race with a concurrent pin). Skip at selection, refuse at the gate; both,
    for the same reason the region work needed both.

    Returns an empty :class:`Grounding` (with notes) when the analysis has not
    been run, so the caller can refuse rather than invent.
    """
    import numpy as np

    from _overlay_common import load_overlay
    # The coverage cap and the site floor, read from the modules that own them
    # rather than re-typed here. `_split_under_cap` exists precisely to satisfy
    # the cap, so a second copy of the number would be a second promise about
    # what the ledger will accept -- and it would drift the moment either
    # constant moved.
    from app.services.sandbox_model import MAX_INTERVENTION_COVERAGE
    from app.services.siting import MIN_SITE_CELLS

    g = Grounding()
    try:
        ov = load_overlay()
        g.bounds = ov.bounds
        g.overlay = ov
    except Exception as exc:  # noqa: BLE001
        g.notes.append(f"analysis unavailable ({exc}); refusing to place features")
        return g

    grid = ov.grid
    cell_w = float(grid["cell_width_m"])
    cell_h = float(grid["cell_height_m"])
    lon_min, lat_min, _, lat_max = ov.bounds
    import math

    cw = cell_w / (111_320.0 * math.cos(math.radians((lat_min + lat_max) / 2)))
    ch = cell_h / 110_574.0

    def extent_ring(min_r, min_c, max_r, max_c):
        west = lon_min + min_c * cw
        east = lon_min + (max_c + 1) * cw
        north = lat_max - min_r * ch
        south = lat_max - (max_r + 1) * ch
        r = lambda v: round(v, 7)  # noqa: E731
        return [[
            r(west), r(south)], [r(east), r(south)], [r(east), r(north)],
            [r(west), r(north)], [r(west), r(south)],
        ]

    def site(cells, kind, evidence, metrics):
        """One candidate site: the contour of its own cells, with the evidence.

        The geometry is the component's **true mask**, contoured by
        ``app/services/intervention_geometry.py`` -- not the axis-aligned
        bounding box this function shipped first. The box claimed the empty
        corners of an irregular basin as intervention area, which overstates
        the site and trips the coverage guard on the wrong evidence. The area
        is the contour's own shoelace area and the centroid its own area
        centroid, for the same reason.
        """
        contour = contour_ring(
            cells, bounds=ov.bounds, rows=ov.rows, cols=ov.cols)
        if contour is None:  # pragma: no cover - components are non-empty here
            return None
        geometry, enclosed, of_component = contour
        return {
            "kind": kind,
            "geometry": geometry,
            "centroid": ring_centroid(geometry["coordinates"][0]),
            # Holes included: a site is what would actually be converted, and
            # quoting the outline alone would claim the gaps as converted too.
            "areaHa": polygon_area_ha(geometry["coordinates"], lat_min, lat_max),
            "evidence": evidence,
            "metrics": {
                **metrics,
                "contourCells": enclosed,
                "contourOfComponent": of_component,
            },
        }

    def densest_block(mask_arr, bbox, weight=None, size=15):
        """Within a component's bbox, find a size×size sub-window with the
        highest hit count (or sum of `weight`). The returned bbox is clamped
        so the window never leaves the component's own extent.

        Why this exists: the raw connected-component bbox for a sparse mask
        can span most of the scene while the cells themselves only fill ~30%.
        Rendering that bbox as a polygon paints the whole scene blue. A fixed
        size window on the densest spot gives the model a bounded, readable
        site that still sits where the evidence is strongest.

        **No longer called by the pools** (merge of 16 Oct 2026): the siting
        path now contours each component's *true cell mask*
        (``intervention_geometry.contour_ring``), which fixes the same
        whole-scene-bbox problem without claiming cells that are not in the
        component and without discarding its shape. Kept because it is the
        right tool the moment a fixed-size window is genuinely wanted; the
        siting rule it implemented is recorded in this docstring.
        """
        min_r, min_c, max_r, max_c = bbox
        rows, cols = mask_arr.shape
        half = size // 2
        if weight is None:
            weight = mask_arr.astype(np.float64)
        else:
            weight = weight * mask_arr  # only count cells inside the mask

        # cumulative sum trick: window score = integral over sub-rectangle.
        best_r, best_c, best_score = min_r, min_c, -1.0
        r0_lo, r0_hi = max(0, min_r), min(rows - size, max_r - size + 1)
        c0_lo, c0_hi = max(0, min_c), min(cols - size, max_c - size + 1)
        if r0_lo > r0_hi or c0_lo > c0_hi:
            # Component smaller than the window; take the component's own bbox,
            # clipped to a readable minimum of 5 cells on each side.
            m = 2
            r0 = max(0, (min_r + max_r) // 2 - m)
            c0 = max(0, (min_c + max_c) // 2 - m)
            return r0, c0, min(rows - 1, r0 + 2 * m), min(cols - 1, c0 + 2 * m)
        for r0 in range(r0_lo, r0_hi + 1):
            for c0 in range(c0_lo, c0_hi + 1):
                score = float(weight[r0:r0 + size, c0:c0 + size].sum())
                if score > best_score:
                    best_r, best_c, best_score = r0, c0, score
        return best_r, best_c, best_r + size - 1, best_c + size - 1

    # --- existing massing is not available to be built on -------------------
    # Every pool below is a set of *candidate sites*, and a site that sits on a
    # roof is not a site. Without this subtraction a reservoir or paving site
    # could be offered on top of a surveyed building and then rejected by the
    # constraint guard -- so the proposal list would be empty, a worse failure
    # than the one being fixed.
    #
    # This is the **gate's** mask (`proposal_constraints.building_mask`), not
    # `_overlay_common.building_footprint_mask` alone. The two differ by a
    # couple of dozen cells in how a footprint edge rasterises (7,812 vs 7,789
    # on this data), and filtering the pools with the looser one offers sites
    # the gate then clips -- the pool and the gate disagreeing about the same
    # building is exactly the drift DESIGN §2.1 warns about. Measured before the
    # swap: the largest basin falls 1,878 -> 1,876 cells, the largest paving
    # site 3,596 -> 3,580, both far inside the caps, and the smallest site (83
    # cells) is unchanged and well above the 40-cell floor, so no site vanishes.
    #
    # The footprint mask is kept as the fallback when no city model is present,
    # because it is the one that reports `None` rather than an all-False mask.
    try:
        from app.services.proposal_constraints import building_mask
        from _overlay_common import building_footprint_mask

        occupied = building_mask(ov)
        if occupied is None:
            occupied = building_footprint_mask(ov)
    except ImportError:  # pragma: no cover - scripts/ off sys.path
        occupied = None

    if occupied is None:
        # None is "no city model is present", which is not the same as "there
        # are no buildings". Subtracting nothing is the only honest option, and
        # the note says so rather than letting a site on a roof look like a site
        # on the ground.
        g.notes.append(
            "no city model: existing buildings NOT excluded from candidate "
            "sites, so a proposed site may sit under a building")
    else:
        g.notes.append(
            f"existing buildings excluded from candidate sites "
            f"({int(occupied.sum())} cells)")

    def free_of_buildings(mask):
        """A pool mask minus the surveyed footprints, unchanged when none exist.

        Returns a new array (``&``, never ``&=``), so a caller can never mutate
        the overlay's own class grid by accident.
        """
        return mask if occupied is None else (mask & ~occupied)

    # --- reservoirs: the deepest connected basins of the wet grid ----------
    depth_file = settings.rasters_dir / "wetgrid_hsk.npy"
    if depth_file.exists():
        depth = np.load(depth_file).astype(np.float64)
        if depth.shape == (ov.rows, ov.cols):
            mask = free_of_buildings(depth >= 0.35)
            # The true cell mask, contoured -- not a 15x15 window at the
            # deepest spot (main's approach at the merge) and not the bounding
            # box (the original). A window is a box: it claims cells that are
            # not in the basin, and it discards the basin's own shape, which is
            # what the catalog and the suggester both site on.
            #
            # The pool is ordered by AREA (`_components_cells` is largest-first),
            # so the evidence names the size rank and not "deepest". The two
            # happen to agree for the first two basins today, but they are not
            # the same ordering: measured on this data, the 4th-largest basin is
            # 2.28 m deep against the 2nd's 1.90 m, so "deepest" on `sites[1]`
            # is simply untrue. The depth itself is published per site as
            # `maxDepthM`, which is the number a planner should rank on.
            for rank, comp in enumerate(
                    _components_cells(mask, 40, connectivity=4)[:3], start=1):
                min_r, min_c = comp["min_r"], comp["min_c"]
                max_r, max_c = comp["max_r"], comp["max_c"]
                sub = depth[min_r:max_r + 1, min_c:max_c + 1]
                g.reservoir_sites.append(site(
                    comp["cells"],
                    "water_reservoir",
                    "%s connected basin of the computed wet grid, contoured "
                    "to its true cell mask" % _ranked(rank),
                    {
                        "maxDepthM": round(float(sub.max()), 2),
                        "wetCells": comp["size"],
                        "wetAreaHa": round(comp["size"] * cell_w * cell_h / 10_000.0, 1),
                    },
                ))
        else:
            g.notes.append(
                f"wetgrid_hsk.npy is {depth.shape}, expected "
                f"{(ov.rows, ov.cols)}; re-run make_wetgrid.py"
            )
    else:
        g.notes.append("no wetgrid_hsk.npy; run scripts/make_wetgrid.py")

    # --- green space: the largest contiguous vegetated blocks --------------
    # Bound to a local variable and freed of buildings first, so the component
    # labelling below sees only ground a site could actually occupy.
    #
    # The evidence string carries the component's RANK, not just "largest".
    # `_components_cells` returns largest-first, so `green[0]` is the largest
    # block and `green[1]`/`green[2]` are the second and third -- labelling all
    # three "largest" is a claim about ground the second and third sites do not
    # contain. These strings are the provenance a planner reads next to a
    # `source` pointing at real code, so they have to be true of *this* site.
    green_mask = free_of_buildings(ov.class_mask("green"))
    for rank, comp in enumerate(
            _components_cells(green_mask, 120, connectivity=4)[:3], start=1):
        g.green_sites.append(site(
            comp["cells"], "green_space",
            "%s contiguous vegetated block (ESA WorldCover class 'green'), "
            "contoured to its true cell mask" % _ranked(rank),
            {"greenCells": comp["size"],
             "greenAreaHa": round(comp["size"] * cell_w * cell_h / 10_000.0, 1)},
        ))

    # --- permeable paving: the biggest contiguous impervious blocks ---------
    #
    # Split, because the largest paved component is a district, not a site. On
    # this study area it is ~6,800 cells -- 39% of the grid against a 25%
    # single-intervention cap -- so serving it whole means the gate refuses it
    # `OVER_SINGLE_CAP` and the proposal path emits *nothing* for the kind. A
    # cap that cannot be satisfied by any site is not a cap, it is an outage.
    #
    # The split is by whole raster rows (row-coherent bands) and then by
    # 4-connected component inside each band, rather than by a flood fill at the
    # cap. Both alternatives were measured and rejected: a BFS fill at the cap
    # produces 13 fragments, six of them under `MIN_SITE_CELLS` and so refused
    # `TOO_SMALL` -- trading one outage for a stream of rejections; and
    # row-major chunking cuts the component into slivers that contour badly,
    # because a contour closes over the gaps between them and the served
    # polygon stops matching the cells it was drawn from.
    #
    # The `[:3]` truncation is applied to the emitted sub-sites, not to the
    # pre-split components. Truncating first would cap the *input* at three and
    # the split would buy nothing.
    paved_mask = free_of_buildings(ov.class_mask("paved"))
    paving_cap = int(math.floor(MAX_INTERVENTION_COVERAGE * ov.rows * ov.cols))
    paving_sites: list[dict[str, Any]] = []
    for comp in _components_cells(paved_mask, 150, connectivity=4):
        for band_index, rank_in_band, band_cells in _split_under_cap(
                comp["cells"], paving_cap, MIN_SITE_CELLS,
                rows=ov.rows, cols=ov.cols):
            entry = site(
                band_cells, "permeable_paving",
                "%s 4-connected impervious block within row band %d of the "
                "largest paved component (ESA WorldCover class 'paved'), "
                "contoured to its true cell mask"
                % (_ranked(rank_in_band), band_index),
                {
                    # Recomputed from THIS sub-site's own cells. Reusing the
                    # parent component's size here would publish a number the
                    # served geometry does not support, under a `source` string
                    # that points at real code -- the most dangerous failure
                    # mode this project has, because it looks sourced.
                    "pavedCells": len(band_cells),
                    "pavedAreaHa": round(
                        len(band_cells) * cell_w * cell_h / 10_000.0, 1),
                    "bandIndex": band_index,
                    "rankInBand": rank_in_band,
                },
            )
            if entry is not None:
                paving_sites.append(entry)
    g.paving_sites.extend(paving_sites[:3])

    if claimed:
        _drop_claimed_sites(g, claimed, occupied)
    g.notes.append(f"terrain={ov.terrain_source}, landcover={ov.landcover_source}")
    return g


def _drop_claimed_sites(g: "Grounding", claimed: Any, occupied: Any = None) -> None:
    """Remove prior-work candidates from every pool, in place (Phase 4 T04b).

    A site whose ground is already covered by the staged plan or a toggled
    project is **not offered**: the proposer skips to the next candidate instead
    of handing the gate a placement the overlap guard will refuse, which used to
    empty the whole kind while free candidates existed.

    Membership is on the *candidate's own cells* against the claimed set, not on
    a geometry intersection -- the candidate is a cell mask and the claimed set
    is a cell set, so there is nothing to rasterise and nothing to round.

    ``occupied`` (the surveyed building mask the gate applies) is subtracted
    from each candidate before the comparison, because the gate serves the
    **clipped** geometry: the largest basin is 1,878 cells as a candidate and
    1,876 as a served placement, and the 2 building cells it sheds would
    otherwise read as free ground and keep a fully-claimed site in the pool.

    A site is dropped when **any** of its servable cells are claimed, not only
    when all of them are. The gate's overlap tolerance is zero cells -- one
    shared cell is a refusal -- so a partly-claimed site is a site the gate will
    refuse, and keeping it reproduces exactly the dead-end this filter exists to
    remove: measured on this data, a staged reservoir shares a cell or two with
    each green block, and the green kind came back empty with
    ``OVERLAPS_STAGED`` twice. Suggesting only wholly-free candidates is the
    same rule the suggestion path already applies.
    """
    from app.services.sandbox_model import _polygon_cells

    try:
        taken = {(int(r), int(c)) for r, c in claimed}
    except TypeError:  # pragma: no cover - a malformed set is not worth failing on
        return
    if not taken:
        return

    # The overlay is the grids the candidate geometries were contoured against,
    # so this rasterises through the same function the ledger and the gate use
    # (`sandbox_model._polygon_cells`), and the cells are directly comparable
    # with the claimed set.
    grids = g.overlay
    if grids is None or not hasattr(grids, "cell_of"):
        return

    # The gate's own building authority, not the pool filter's: the served
    # geometry is what the gate clips, so the comparison has to use the mask the
    # gate clipped with. The two masks are close but not identical (they differ
    # by how a building's own footprint is rasterised), and using the wrong one
    # leaves a cell or two reading as free ground on a wholly-claimed site --
    # which is exactly the off-by-two that kept the largest basin in the pool.
    buildings: set[tuple[int, int]] = set()
    try:
        import numpy as np

        from app.services import proposal_constraints as _constraints

        mask = _constraints.building_mask(g.overlay)
        if mask is not None:
            rows, cols = np.where(mask)
            buildings = {(int(r), int(c)) for r, c in zip(rows, cols)}
    except Exception:  # noqa: BLE001 - the pool filter degrades to the loose mask
        buildings = set()

    dropped = 0
    for name in ("reservoir_sites", "green_sites", "paving_sites"):
        pool = getattr(g, name, None)
        if not pool:
            continue
        kept = []
        for candidate in pool:
            coords = (candidate.get("geometry") or {}).get("coordinates") or []
            try:
                cells = {(int(r), int(c))
                         for r, c in _polygon_cells(grids, coords)}
            except Exception:  # noqa: BLE001 - a bad ring keeps the candidate
                kept.append(candidate)
                continue
            served = cells - buildings
            if served and (served & taken):
                dropped += 1
                continue
            kept.append(candidate)
        setattr(g, name, kept)
    if dropped:
        g.notes.append(
            f"{dropped} candidate site(s) already covered by work on the "
            f"sandbox were not offered; the proposer moved on to free ground "
            f"rather than proposing a placement that would be refused")


def _claimed_empty_message(grounding: "Grounding",
                           instruction: str = "") -> str | None:
    """Why prior work emptied a pool, in the instruction's own terms (T04b).

    ``None`` unless prior work is the reason something asked for has nothing:
    the note the pool filter appended says how many candidates it dropped, and
    a pool for a **kind the instruction actually named** is now empty. A kind
    the instruction never mentioned is not reported -- "add green space" must
    not be told that the reservoirs are full, which is a true sentence about
    the wrong thing.

    The generic "no candidate survived the constraints" blames the constraint
    engine for ground that is simply taken, and the planner's next move is to
    move or remove something rather than to pick a different kind -- so the
    message names that, not just the fact.
    """
    if not any("already covered by work on the sandbox" in n
               for n in grounding.notes):
        return None

    pools = {
        "water_reservoir": ("water reservoir", grounding.reservoir_sites),
        "green_space": ("green space", grounding.green_sites),
        "urban_green": ("green space", grounding.green_sites),
        "permeable_paving": ("permeable paving", grounding.paving_sites),
    }
    asked = [kind for kind, _count in parse_intent_counts(instruction)] if instruction else []
    # Falls back to every empty pool when the instruction named no kind at all
    # (or named one this map does not carry), so the sentence is never a claim
    # about a kind nobody asked for while a relevant pool sits empty.
    considered = asked or list(pools)
    empty: list[str] = []
    for kind in considered:
        label, pool = pools.get(kind, (None, None))
        if label is None or pool:
            continue
        if label not in empty:
            empty.append(label)
    if not empty:
        return None
    kinds = ", ".join(empty)
    return (
        f"No candidate site for the {kinds} was left to offer: the work "
        "already on the sandbox -- the staged plan and the toggled projects -- "
        "claims all of that ground, so nothing was proposed over it. Move or "
        "remove one of them, and the free ground comes back. Nothing was "
        "invented."
    )


def _ranked(rank: int) -> str:
    """The ordinal phrase for a candidate's rank **within its own pool**.

    Pools are emitted largest-first, so the first site is *the* largest and
    every later one is the second, third, ... Calling all of them "the largest"
    is a provenance claim about ground the later sites do not contain, and these
    strings sit next to a `source` that points at real code -- which is exactly
    where an untrue label does the most damage, because it looks sourced.

    Ordinals are spelled out rather than numeric: this string is read aloud in
    a UI tooltip and quoted in a planner's notes, where "2nd" beats "2".
    """
    if rank <= 1:
        return "the largest"
    if rank == 2:
        return "the second-largest"
    if rank == 3:
        return "the third-largest"
    return "the %dth-largest" % rank


def _split_under_cap(cells: Iterable[tuple[int, int]], cap: int, min_cells: int,
                     *, rows: int, cols: int
                     ) -> list[tuple[int, int, list[tuple[int, int]]]]:
    """Split one component into sub-sites that each fit under ``cap`` cells.

    Returns ``(band_index, rank_in_band, cells)`` per surviving sub-site. Only
    the band's own cells come back -- never the parent component's ``size`` or
    bounding box -- because the caller contours and measures from this list, and
    a metric carried over from the parent would describe ground the served
    polygon does not contain. The two indices are what let the caller label each
    site truthfully (see :func:`_ranked`).

    Two deterministic steps, and both matter:

    1. **Row-coherent bands.** Group the cells by row, walk the rows in
       ascending ``r``, and close the band when adding the next row would
       exceed ``cap``. Accumulating *whole rows* rather than filling to the cap
       is what keeps a band contiguous: a row-major fill slices the component
       into vertical slivers whose contour closes across the gaps, so the
       served polygon covers cells the band never contained.
    2. **4-connected components within each band,** via
       ``intervention_geometry.connected_components`` -- the same component
       definition every other pool uses (D7). A band can still be several
       pieces, and serving only the largest would drop real cells with no
       record, which is the failure D5 exists to prevent.

    Fragments below ``min_cells`` are dropped: the gate would refuse them
    ``TOO_SMALL``, so offering them spends a rejection to say nothing.

    Ordering is a **total** order, ``(-size, min_r, min_c)``: no two distinct
    components share all three, so no tie can fall back to set or dict
    iteration order (invariant 7). The row walk is likewise ordered, never
    "whatever the set gave". ``rank_in_band`` is that sort's 1-based position,
    so it is a total order too.
    """
    import numpy as np

    by_row: dict[int, list[int]] = {}
    for r, c in cells:
        by_row.setdefault(int(r), []).append(int(c))

    bands: list[list[tuple[int, int]]] = []
    band: list[tuple[int, int]] = []
    n = 0
    for r in sorted(by_row):
        row_cells = sorted(by_row[r])
        k = len(row_cells)
        if band and n + k > cap:
            bands.append(band)
            band = []
            n = 0
        band.extend((r, c) for c in row_cells)
        n += k
    if band:
        bands.append(band)

    out: list[tuple[int, int, list[tuple[int, int]]]] = []
    for band_index, band_cells in enumerate(bands):
        mask = np.zeros((rows, cols), dtype=bool)
        for r, c in band_cells:
            mask[r, c] = True
        survivors = [comp for comp in _components_cells(mask, 1, connectivity=4)
                     if comp["size"] >= min_cells]
        survivors.sort(key=lambda comp: (-comp["size"], comp["min_r"],
                                          comp["min_c"]))
        out.extend((band_index, rank, comp["cells"])
                   for rank, comp in enumerate(survivors, start=1))
    return out


def _components(mask, min_size: int) -> list[tuple[int, int, int, int, int]]:
    """8-connected components -> (min_r, min_c, max_r, max_c, size), largest first."""
    return [
        (c["min_r"], c["min_c"], c["max_r"], c["max_c"], c["size"])
        for c in _components_cells(mask, min_size)
    ]


def _components_cells(mask, min_size: int,
                      connectivity: int = 8) -> list[dict[str, Any]]:
    """Connected components with their cell lists kept, largest first.

    Thin alias for ``intervention_geometry.connected_components``: one fill,
    one definition of "component" for grounding, siting and the catalog
    generator alike.
    """
    return connected_components(mask, min_size, connectivity=connectivity)



# --------------------------------------------------------------------------- #
# Placement: the one generator of new massing blocks
# --------------------------------------------------------------------------- #

# Edge length of a placed block. 40 m is the pitch the demo's blocks use, and it
# is a *stated* assumption, not a derived optimum: nothing in the data says a new
# building is 40 m square.
PLACEMENT_PITCH_M = 40.0

# A block is emitted only where this fraction of its cells are placeable (paved,
# inside the scope, not already under a building). Below it the block would be
# half in a field or half on an existing tower.
PLACEMENT_FILL_FRACTION = 0.9


def _ring_mask(ov: Any, ring: list[list[float]]) -> Any:
    """Boolean raster: True at each cell centre inside ``ring`` (even-odd rule).

    Vectorised ray casting over the grid rather than a per-cell Python loop:
    the scope ring is arbitrary GeoJSON, so a bounding-box test would accept a
    diagonal or concave region's empty corner.
    """
    import numpy as np

    west, south, east, north = ov.bounds
    cw = (east - west) / ov.cols
    ch = (north - south) / ov.rows
    lon = np.repeat((west + (np.arange(ov.cols) + 0.5) * cw)[None, :], ov.rows, 0)
    lat = np.repeat((north - (np.arange(ov.rows) + 0.5) * ch)[:, None], ov.cols, 1)
    inside = np.zeros(lon.shape, dtype=bool)
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        straddles = (yi > lat) != (yj > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
        inside ^= straddles & (lon < cross)
        j = i
    return inside


def _block_ring(ov: Any, row0: int, col0: int, pitch: int) -> list[list[float]]:
    """The lon/lat ring of a block of cells, corners in GeoJSON order."""
    west, _south, _east, north = ov.bounds
    cw = (_east - west) / ov.cols
    ch = (north - _south) / ov.rows
    x0 = west + col0 * cw
    x1 = west + (col0 + pitch) * cw
    y1 = north - row0 * ch
    y0 = north - (row0 + pitch) * ch
    r = lambda v: round(v, 7)  # noqa: E731
    return [[r(x0), r(y0)], [r(x1), r(y0)], [r(x1), r(y1)], [r(x0), r(y1)],
            [r(x0), r(y0)]]


def placement_footprints(scope_ring: list[list[float]], count: int,
                         grounding: Grounding) -> list[dict[str, Any]]:
    """Up to ``count`` new blocks on the paved class inside ``scope_ring``.

    The **only** generator of new massing: ``scenario.apply_massing`` calls this
    so a "place" op in a run and the demo's placement are the same function with
    the same pitch and the same exclusions. A second generator would be exactly
    the drift this project forbids.

    Exclusions are what make a placement defensible rather than a stamped square:
    a cell must be paved (new massing goes on already-built ground, so the
    drainage axis is honest about height not being a flood lever), inside the
    scope, and **not already under a building** -- the existing district is not
    available to be built on. ``heightM`` is ``None``: this function places
    footprints and does not invent a height, so the caller's own height (or its
    stated default) applies.

    An empty list is a real answer -- a fully built scope has nowhere to place --
    and the caller reports a placed count of zero rather than inventing a block.
    """
    ov = grounding.overlay
    if ov is None or count <= 0 or not scope_ring:
        return []
    pitch = max(1, int(round(PLACEMENT_PITCH_M / float(ov.cell_m))))
    target = ov.class_mask("paved") & _ring_mask(ov, scope_ring)

    # Existing massing is excluded using the project's one footprint mask.
    try:
        from _overlay_common import building_footprint_mask  # noqa: PLC0415

        occupied = building_footprint_mask(ov)
    except ImportError:  # pragma: no cover - scripts/ off sys.path
        occupied = None
    if occupied is not None:
        target &= ~occupied

    blocks: list[dict[str, Any]] = []
    for min_r, min_c, max_r, max_c, _size in _components(target, pitch * pitch):
        for row0 in range(min_r, max_r - pitch + 2, pitch):
            for col0 in range(min_c, max_c - pitch + 2, pitch):
                if len(blocks) >= count:
                    return blocks
                window = target[row0:row0 + pitch, col0:col0 + pitch]
                if window.size and window.mean() >= PLACEMENT_FILL_FRACTION:
                    blocks.append(
                        {"ring": _block_ring(ov, row0, col0, pitch),
                         "heightM": None}
                    )
    return blocks


# --------------------------------------------------------------------------- #
# Scope: turning "the west" into a polygon the model never got to choose
# --------------------------------------------------------------------------- #

# Regions as fractions of the locked study-area bbox, in (lon_min, lat_min,
# lon_max, lat_max) order. Plain arithmetic over a constant this project already
# owns, which is what makes a region name safe to accept from a language model:
# there is nothing to hallucinate, only a box to divide.
REGION_FRACTIONS: dict[str, tuple[float, float, float, float]] = {
    "all": (0.0, 0.0, 1.0, 1.0),
    "west": (0.0, 0.0, 0.5, 1.0),
    "east": (0.5, 0.0, 1.0, 1.0),
    "south": (0.0, 0.0, 1.0, 0.5),
    "north": (0.0, 0.5, 1.0, 1.0),
    "centre": (0.25, 0.25, 0.75, 0.75),
    "north-west": (0.0, 0.5, 0.5, 1.0),
    "north-east": (0.5, 0.5, 1.0, 1.0),
    "south-west": (0.0, 0.0, 0.5, 0.5),
    "south-east": (0.5, 0.0, 1.0, 0.5),
}

# What a model or a user might say. Order matters: `_match_region` returns the
# first phrase that appears, so the two-word compounds must precede the single
# words they contain. Keys are space-separated because hyphens are flattened
# before matching -- otherwise "north-east" matches "east" and half the request
# is applied to the wrong half of the district.
REGION_SYNONYMS: dict[str, str] = {
    "whole study area": "all", "entire district": "all", "the district": "all",
    "whole area": "all", "everywhere": "all", "study area": "all",
    "north west": "north-west", "north east": "north-east",
    "south west": "south-west", "south east": "south-east",
    "northwestern": "north-west", "northeastern": "north-east",
    "southwestern": "south-west", "southeastern": "south-east",
    "western": "west", "eastern": "east", "northern": "north", "southern": "south",
    "central": "centre", "middle": "centre", "center": "centre", "centre": "centre",
    "west": "west", "east": "east", "north": "north", "south": "south", "all": "all",
    "nw": "north-west", "ne": "north-east", "sw": "south-west", "se": "south-east",
}


def _match_region(text: str) -> str | None:
    """The region a phrase names, or None. Deterministic; no model involved.

    Hyphens become spaces first, because "north-west" would otherwise match the
    single word "west" -- `\b` treats the hyphen as a boundary -- and a request
    for the north-west would be applied to the entire western half.
    """
    lowered = text.lower().replace("-", " ")
    for phrase, region in REGION_SYNONYMS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return region
    return None


def _point_in_region(lon: float, lat: float,
                     bounds: tuple[float, float, float, float],
                     region: str) -> bool:
    """True when a point (a site's centroid) lies inside a region rectangle.

    **Centroid-in-rectangle, inclusive** -- deliberately *not* "does the
    polygon overlap the region". That distinction is measured, not stylistic:
    ``reservoir#0``'s ring spans x in [0.34, 1.00] and y in [0.00, 1.00] -- the
    outer two-thirds of the scene in *both* axes -- so an overlap test would put
    that single site in **every** region and reproduce the exact bug this
    predicate exists to remove ("add a water reservoir in the north / south /
    east / west / centre" returning identical geometry). The centroid is already
    the site's published identity (``params.siteCentroid``), so membership adds
    no new number about a site -- it reads one the site already carries.

    The test is inclusive (``w <= lon <= e and s <= lat <= n``): at a seam a
    candidate is offered rather than hidden, which is the safe direction for a
    filter -- it can never silently empty a pool that has a legitimate
    candidate. A name that is not one of :data:`REGION_FRACTIONS` matches
    nothing, so an unrecognised region is never a licence to place anything.

    A pure function of ``(lon, lat, bounds, REGION_FRACTIONS)``: no clock, no
    RNG, no state. One authority, used by every selection path.
    """
    fr = REGION_FRACTIONS.get(region)
    if fr is None:
        return False
    lon_min, lat_min, lon_max, lat_max = bounds
    width = lon_max - lon_min
    height = lat_max - lat_min
    west = lon_min + width * fr[0]
    east = lon_min + width * fr[2]
    south = lat_min + height * fr[1]
    north = lat_min + height * fr[3]
    return west <= lon <= east and south <= lat <= north


def empty_region_message(
    instruction: str,
    grounding: "Grounding",
) -> str | None:
    """Why a named region produced nothing, in the instruction's own terms.

    ``None`` when the instruction names no region (or names ``all``): the
    generic "nothing could be placed" is then the honest sentence, because the
    whole area was considered.

    When a region *is* named and holds no candidate of a requested kind, the
    answer is a fact about the site, not about the request or the model -- so it
    names the region, the kind, and **where that kind does have candidates**.
    The last clause is what makes the answer actionable: "no water reservoir in
    the south" alone leaves a planner with nowhere to go, and the region the
    kind does occupy is a number this module already has.

    Deliberately *not* a ``rejected[]`` entry. That array is defined as the
    proposals that failed the **constraint** engine and carries a closed
    ``reasonCode``; "there was never a candidate here" is not a constraint
    outcome, and filing it there would make two different physical facts
    indistinguishable to a client grouping refusals by code.
    """
    region = _match_region(instruction)
    if region is None or region == "all":
        return None
    bounds = getattr(grounding, "bounds", None)
    if bounds is None:
        return None

    intents = parse_intent_counts(instruction)
    if not intents:
        return None

    parts: list[str] = []
    any_empty = False
    for kind, _count in intents:
        pool_name = POOL_FOR_KIND.get(kind)
        pool = getattr(grounding, pool_name, []) if pool_name else []
        if not pool:
            continue
        eligible = sites_in_region(pool, region, bounds)
        if eligible:
            continue
        any_empty = True
        # Which regions this kind *does* occupy, computed with the same
        # predicate that emptied the pool -- one authority, so the sentence and
        # the filter cannot disagree.
        elsewhere = sorted({
            name for name in REGION_FRACTIONS
            if name not in ("all", region)
            and sites_in_region(pool, name, bounds)
        })
        # A local label map, not `sandbox.KIND_LABEL`: `sandbox` imports this
        # module, so reaching back for it would be a cycle. Same wording, so a
        # reader meets one name for one kind across both surfaces.
        label = kind.replace("_", " ")
        if elsewhere:
            parts.append(
                f"no {label} candidate lies in the {region} region "
                f"(this kind's candidates are in: {', '.join(elsewhere)})")
        else:
            parts.append(
                f"no {label} candidate exists anywhere in the study area")

    if not any_empty:
        return None
    return (
        f"Nothing was placed for the {region} region: "
        + "; ".join(parts)
        + ". Nothing was invented — a site outside the region you asked for "
          "would be a different answer, not this one."
    )


def sites_in_region(
    pool: list[dict[str, Any]],
    region: str | None,
    bounds: tuple[float, float, float, float] | None,
) -> list[tuple[int, dict[str, Any]]]:
    """Narrow an ordered pool to the sites whose centroid is inside ``region``.

    Returns ``(original_index, site)`` pairs, a **view** over the pool and never
    a replacement for it. Each site keeps its **original pool index**, because
    ``site_id`` is ``f"{kind}#{i}"`` over ``enumerate(pool)`` and both
    ``_materialise`` and ``resolve_scope`` resolve an id by indexing the *full*
    pool -- renumbering a narrowed list would silently re-point every id to the
    wrong geometry.

    ``region`` of ``None`` or ``"all"`` returns the pool unchanged, in the
    original order, carrying the original indices. That is what keeps ``None``
    meaning "today's behaviour" for every caller, and it is why ``"all"``
    short-circuits rather than falling through the arithmetic.

    Single-purpose and order-preserving: a list comprehension over the ordered
    pool, with **no distance sort** (a float key with real ties is how this fix
    gets written wrong -- the tie would fall back on iteration order) and **no
    occupancy logic** (a later step in the chain owns that). It therefore
    composes with whatever runs before or after it without assuming it is last.

    When no site is in the requested region the result is **empty**: it never
    falls back to an out-of-region site. That is the same posture as
    :func:`resolve_scope`, whose docstring calls the fallback-to-"all" "the
    exact bug this function exists to remove".
    """
    if region is None or region == "all":
        return list(enumerate(pool))
    if bounds is None:
        # No study-area bbox means no rectangle to test, so a named region
        # cannot be honoured. Offer nothing rather than everything.
        return []
    return [
        (index, site)
        for index, site in enumerate(pool)
        if _point_in_region(site["centroid"][0], site["centroid"][1], bounds, region)
    ]


def _fraction_ring(bounds: tuple[float, float, float, float],
                   fr: tuple[float, float, float, float]) -> list[list[float]]:
    """A closed rectangular ring for one region of the study area."""
    lon_min, lat_min, lon_max, lat_max = bounds
    r = lambda v: round(v, 7)  # noqa: E731
    west = r(lon_min + (lon_max - lon_min) * fr[0])
    east = r(lon_min + (lon_max - lon_min) * fr[2])
    south = r(lat_min + (lat_max - lat_min) * fr[1])
    north = r(lat_min + (lat_max - lat_min) * fr[3])
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def resolve_scope(hint: str | None, site_id: str | None,
                  grounding: Grounding) -> dict[str, Any] | None:
    """Turn a scope *reference* into a real polygon. None if it is not one we offered.

    Three inputs, and the precedence between them is not arbitrary:

    - ``hint`` -- a region name, normalised through :data:`REGION_SYNONYMS`. A
      region the model named **explicitly** wins, because a region is what an
      instruction tends to say ("the western blocks") and it is the coarser,
      more deliberate choice.
    - ``site_id`` -- an opaque catalogue id such as ``water_reservoir#0``, used
      when no region was named. The polygon is the one the analysis derived.
    - neither -- the whole study area.

    Site ids do **not** outrank a named region. `qwen2.5:3b-instruct` fills in
    every field of the schema, so "move the western blocks 40 m north" came back
    carrying both `scope: "west"` and a placeholder `site_id: "road#0"` -- and
    site-id precedence silently answered a different question, moving every
    building in the district. The region is trusted first; the site id is still
    recorded in the record either way.

    Returning None for anything unrecognised is deliberate. The alternative --
    falling back to "all" -- is the exact bug this function exists to remove: a
    scoped request would then be answered with an unscoped edit, and the user
    would be looking at a 3D result for a question they did not ask.
    """
    if grounding.bounds is None:
        return None

    region = "all"
    if hint:
        # Normalised the same way _match_region does, so the region name a
        # parser produces ("north-west") and the phrase a user typed
        # ("north west") resolve identically. Without this the two halves
        # disagreed and every compound region was silently dropped.
        key = str(hint).strip().lower().replace("-", " ")
        region = REGION_SYNONYMS.get(key) or (key if key in REGION_FRACTIONS else "")
        if not region:
            return None

    if site_id and region == "all":
        pool_name, _, index = str(site_id).partition("#")
        pool = POOL_FOR_KIND.get(pool_name)
        sites = getattr(grounding, pool, []) if pool else []
        if not index.isdigit() or int(index) >= len(sites):
            return None
        site = sites[int(index)]
        return {
            "kind": "site",
            "region": None,
            "siteId": site_id,
            "label": f"{pool_name} site {index}",
            "evidence": site["evidence"],
            "ring": [list(p) for p in site["geometry"]["coordinates"][0]],
        }

    return {
        "kind": "all" if region == "all" else "region",
        "region": region,
        "siteId": str(site_id) if site_id else None,
        "label": "the whole study area" if region == "all" else f"the {region}",
        "evidence": f"{region} of the locked study-area bbox",
        "ring": _fraction_ring(grounding.bounds, REGION_FRACTIONS[region]),
    }


# Hong Kong residential floor-to-floor. A storey count in a prompt is converted
# with it so the model can think in storeys, which is how the planning
# conversation actually goes.
STOREY_M = 3.1


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon, shared with ``scenario._land_class_after``.

    The rings here are 5-point rectangles, but a site polygon from
    ``_components`` is a bounding box too -- so this stays correct if either ever
    becomes a real outline.
    """
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < crossing:
                inside = not inside
    return inside


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _pick(edit: dict[str, Any], *keys: str) -> Any:
    """First present key. The tool schema is snake_case (JSON Schema for a model)
    and the heuristic parser is camelCase, and both reach this function."""
    for key in keys:
        if edit.get(key) is not None:
            return edit[key]
    return None


def _massing_feature(edit: dict[str, Any], grounding: Grounding,
                     instruction: str, provider: str,
                     rationale: str) -> dict[str, Any] | None:
    """Turn a `modify_massing` op into a feature carrying REAL geometry.

    The feature's own geometry is the **resolved scope** -- the area the edit
    actually affects -- so the record states what was changed rather than always
    claiming the whole study area. It draws no block (`opacity: 0`); it exists so
    the feature is a valid, listable GeoJSON polygon.

    An unknown op, an unrecognised scope, a non-numeric multiplier or a site id
    that was never offered returns None rather than a plausible-looking default.
    """
    op = str(_pick(edit, "op") or "")
    if op not in ("scale_all", "set_site", "reset", "rotate", "move", "resize"):
        return None

    scope = resolve_scope(
        _pick(edit, "scope", "region"), _pick(edit, "site_id", "siteId"), grounding,
    )
    if scope is None:
        return None

    params: dict[str, Any] = {
        "op": op,
        "scope": scope,
        "siteId": scope["siteId"],
        "basis": (
            f"{scope['evidence']}"
            if scope["kind"] == "site"
            else f"existing procedural massing within {scope['label']}"
        ),
        "rationale": rationale,
        "instruction": instruction,
        "provider": provider,
        "status": "draft_pending_human_review",
    }

    multiplier = _pick(edit, "multiplier")
    height = _pick(edit, "height_m", "heightM")
    delta = _pick(edit, "delta_m", "deltaM")
    degrees = _pick(edit, "degrees", "degrees_cw")
    count = _pick(edit, "count")
    dx = _pick(edit, "dx_m", "dxM")
    dy = _pick(edit, "dy_m", "dyM")
    # A direction word outranks the model's own trigonometry -- **for a move only**.
    #
    # `qwen2.5:3b-instruct` asked to move blocks "40 m north" returned
    # `dx_m: 40, dy_m: 0` -- north and east transposed, a plausible pair of
    # numbers and completely the wrong direction. Copying the word out of the
    # instruction cannot transpose an axis; doing the conversion can.
    #
    # The `op == "move"` guard is not decoration. A small model fills in the whole
    # schema, so a `place` op arrived carrying `direction: "north"` and
    # `distance_m: 0` -- fields meaningless for placing -- and this block rejected
    # the whole operation over them. That is the identical failure to the
    # `height_m: 0` case fixed above, reintroduced six lines away, and it silently
    # cost every placement the model got otherwise right. The rule holds: only the
    # fields an operation actually uses may invalidate it.
    direction = str(edit.get("direction") or "").strip().lower().replace("-", " ")
    if op == "move" and direction in DIRECTIONS:
        distance = _pick(edit, "distance_m", "distanceM")
        if distance is None and (dx or dy):
            try:
                distance = math.hypot(float(dx), float(dy))
            except (TypeError, ValueError):
                distance = None
        try:
            ux, uy = DIRECTIONS[direction]
            metres = float(distance) if distance is not None else None
        except (TypeError, ValueError):
            return None
        if metres is None or not 0.0 < abs(metres) <= 500.0:
            return None
        dx, dy = ux * metres, uy * metres

    # Coerce first, range-check per operation *afterwards*.
    #
    # Validating every field for every op looks tidier and is wrong: a real model
    # fills in the schema, so `move` arrived carrying `height_m: 0` -- a field that
    # means nothing for a move -- and the shared 1..800 m check rejected the whole
    # operation. The instruction was answered with nothing. Only the fields an op
    # actually uses are allowed to invalidate it.
    try:
        multiplier = float(multiplier) if multiplier is not None else None
        height = float(height) if height is not None else None
        delta = float(delta) if delta is not None else None
        degrees = float(degrees) if degrees is not None else None
        dx = float(dx) if dx is not None else None
        dy = float(dy) if dy is not None else None
        count = int(count) if count is not None else None
        target_lon = float(_pick(edit, "target_lon", "targetLon")) \
            if _pick(edit, "target_lon", "targetLon") is not None else None
        target_lat = float(_pick(edit, "target_lat", "targetLat")) \
            if _pick(edit, "target_lat", "targetLat") is not None else None
        width_scale = float(_pick(edit, "width_scale", "widthScale")) \
            if _pick(edit, "width_scale", "widthScale") is not None else None
        depth_scale = float(_pick(edit, "depth_scale", "depthScale")) \
            if _pick(edit, "depth_scale", "depthScale") is not None else None
    except (TypeError, ValueError):
        return None

    if op == "scale_all":
        if multiplier is None or not 0.05 <= multiplier <= 8.0:
            return None
    elif op == "set_site":
        # Exactly one of an absolute height and a delta. Neither means the model
        # did not say; both means it contradicted itself.
        if (height is None) == (delta is None):
            return None
        if height is not None and not 1.0 <= height <= 800.0:
            return None
        if delta is not None and not -400.0 <= delta <= 400.0:
            return None
    elif op == "rotate":
        if degrees is None or not -180.0 <= degrees <= 180.0:
            return None
    elif op == "move":
        # Two modes: metric offset or direct target coordinate. The coordinate
        # is validated against the study-area bounds in apply_massing, but here
        # we check that at least one mode has a non-trivial value.
        has_coord = (target_lon is not None and target_lat is not None)
        has_offset = bool(dx or dy)
        if not has_coord and not has_offset:
            return None
        if not has_coord:
            for axis in (dx, dy):
                if axis is not None and not -500.0 <= axis <= 500.0:
                    return None
    elif op == "resize":
        if width_scale is None and depth_scale is None:
            return None
        for s in (width_scale, depth_scale):
            if s is not None and not 0.1 <= s <= 10.0:
                return None

    params["multiplier"] = multiplier
    params["heightM"] = height
    params["deltaM"] = delta
    params["degrees"] = degrees
    params["dxM"] = dx
    params["dyM"] = dy
    params["targetLon"] = target_lon
    params["targetLat"] = target_lat
    params["widthScale"] = width_scale
    params["depthScale"] = depth_scale

    # Carried for provenance: the deterministic parser says which readings were
    # defaults, so a demo cannot present a guess as something that was asked for.
    for flag in ("countDefaulted", "heightDefaulted", "angleDefaulted"):
        if edit.get(flag):
            params[flag] = True
    params["note"] = (
        "Annotates the existing massing. Adds no building and "
        "returns no coordinate. 'resize' changes the footprint size; 'move' "
        "with targetLon/targetLat repositions to a stated coordinate inside "
        "the study area."
    )

    return {
        "geometry": {"type": "Polygon", "coordinates": [scope["ring"]]},
        "intervention_type": "massing_height",
        "source": "ai",
        "params": params,
        "style": dict(MASSING_STYLE),
    }


class Provider(Protocol):
    """Anything that can turn an instruction plus grounding into features."""

    name: str

    def propose(
        self, instruction: str, grounding: Grounding
    ) -> list[dict[str, Any]]:  # pragma: no cover - protocol
        ...


def _feature(
    kind: str,
    site: dict[str, Any],
    instruction: str,
    provider: str,
    rationale: str,
) -> dict[str, Any]:
    """Assemble one validated feature dict from a chosen site.

    `source` is ``ai`` and `status` is ``draft`` for everything this module
    produces, whatever the provider: ADR-030 requires it, and a draft feature is
    the thing the engine will refuse to compute on, which is the correct posture
    for machine-proposed geometry.
    """
    return {
        "geometry": site["geometry"],
        "intervention_type": kind,
        "source": "ai",
        "params": {
            "basis": site["evidence"],
            "rationale": rationale,
            "instruction": instruction,
            "provider": provider,
            "siteAreaHa": site["areaHa"],
            "siteCentroid": site["centroid"],
            **site["metrics"],
            "status": "draft_pending_human_review",
        },
        "style": STYLE_FOR_KIND.get(kind, {"color": "#8250df", "opacity": 0.6}),
    }


# Style per intervention kind, so a legend is possible (see interventionLayer.ts).
STYLE_FOR_KIND: dict[str, dict[str, Any]] = {
    "water_reservoir": {"color": "#1f6feb", "opacity": 0.55, "extrusionHeight": 14.0},
    "green_space": {"color": "#2ea043", "opacity": 0.45, "extrusionHeight": 6.0},
    # A floodable park stays flat and low: it is landscaped ground that ponds,
    # not a tank, and extruding it 14 m would read as a building.
    "flood_storage": {"color": "#3fb950", "opacity": 0.35, "extrusionHeight": 1.5},
    # The extrusion heights below are main's stage-1 work: permeable paving,
    # drainage and road are drawn as 2.5D slabs so the legend can read them.
    "permeable_paving": {"color": "#8957e5", "opacity": 0.45, "extrusionHeight": 2.0},
    "drainage": {"color": "#e3b341", "opacity": 0.95, "extrusionHeight": 3.0},
    "road": {"color": "#6e7681", "opacity": 0.8, "extrusionHeight": 2.0},
}

# Which grounding pool each kind draws from.
POOL_FOR_KIND: dict[str, str] = {
    "water_reservoir": "reservoir_sites",
    "green_space": "green_sites",
    "permeable_paving": "paving_sites",
    "drainage": "reservoir_sites",
    "road": "paving_sites",
}


# Quantity words that *state* a count. ``a``/``an`` are deliberately absent:
# the indefinite article names **what** is wanted ("add a reservoir"), not
# **how many**, so reading it as a count of 1 conflates the two and turns "a
# reservoir in the north" into "exactly one, and hide the rest" -- half of the
# owner's complaint. Only a numeral or one of these words counts as stated.
_STATED_COUNT_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "several": 3, "some": 2,
}


def _stated_count(text: str, alias: str) -> int | None:
    """The count a phrase *states*, or ``None`` when it states none.

    The scan is the one ``parse_intent`` has always used -- a numeral or a
    quantity word in the 24 characters before the kind's alias -- with one
    change: ``a ``/``an `` are **not** counts (see
    :data:`_STATED_COUNT_WORDS`). ``None`` is preserved rather than defaulted,
    because "no count stated" and "exactly one" are different requests: the
    caller offers every eligible candidate for the former and exactly one for
    the latter. ``text`` is expected already lower-cased.
    """
    if alias not in text:
        return None
    start = text.index(alias)
    window = text[max(0, start - 24):start]
    digits = re.findall(r"\b(\d{1,2})\b", window)
    if digits:
        return int(digits[-1])
    return next((v for w, v in _STATED_COUNT_WORDS.items() if w in window), None)


def parse_intent(instruction: str) -> list[tuple[str, int]]:
    """Extract (kind, count) intents from an instruction.

    Deliberately simple and inspectable: the count defaults to 1, and a phrase
    with no recognised verb yields nothing rather than a guess. This is the
    non-AI baseline -- if the LLM cannot beat this, it is not earning its place.

    A thin wrapper over :func:`parse_intent_counts` that maps an unstated count
    to 1 and clamps to ``max(1, min(count, 3))``. Its signature and return type
    are pinned by ``test_ai_plan.py::test_intent_parsing_reads_counts``, so the
    richer "was a count stated at all?" view lives in
    :func:`parse_intent_counts` rather than here.
    """
    return [
        (kind, max(1, min(count, 3)) if count is not None else 1)
        for kind, count in parse_intent_counts(instruction)
    ]


def parse_intent_counts(instruction: str) -> list[tuple[str, int | None]]:
    """As :func:`parse_intent`, but keeps an unstated count as ``None``.

    ``None`` means "the instruction names a kind but no quantity" -- a request
    for the kind, not for one of it. The region-aware selector offers every
    eligible candidate in that case (bounded by the pool's own ceiling of 3);
    an explicit count still caps. Order and de-duplication match
    :func:`parse_intent` exactly, so the two never disagree about *what* was
    asked for, only about whether a quantity was stated.
    """
    text = _mask_false_friends(instruction.lower().replace("-", " "))
    found: list[tuple[str, int | None]] = []
    for alias, kind in EN_ALIASES.items():
        # Whole words, with an optional English plural -- the same rule
        # `agent/intent.py::_match_noun` uses, so the parser and the classifier
        # cannot disagree about whether a noun is present. A bare substring test
        # read "car park" as a *park* and proposed three green spaces for it.
        if not re.search(rf"\b{re.escape(alias)}(?:e?s)?\b", text):
            continue
        raw = _stated_count(text, alias)
        count = None if raw is None else max(1, min(raw, 3))
        if all(kind != k for k, _ in found):
            found.append((kind, count))
    return found


#: Compound phrases that contain an intervention noun but are not that
#: intervention. Each is blanked out before the vocabulary is matched, so the
#: shorter noun inside it is never read. "car park" is a paved lot, the opposite
#: of a green space; "road" in "railroad" is not a street.
#:
#: Kept short and explicit rather than inferred: a phrase on this list is a
#: decision that the planner did *not* ask for the noun it contains, and that
#: decision should be readable in one place.
_FALSE_FRIENDS: tuple[str, ...] = (
    "car park", "carpark", "parking", "park and ride", "business park",
    "industrial park", "science park", "theme park", "railroad",
)


def _mask_false_friends(text: str) -> str:
    """``text`` with every false-friend phrase replaced by spaces of equal length.

    Same length, so the character offsets `_stated_count` reads for the count
    window still line up with the original sentence.
    """
    for phrase in _FALSE_FRIENDS:
        text = re.sub(rf"\b{re.escape(phrase)}\b", lambda m: " " * len(m.group(0)), text)
    return text



# --------------------------------------------------------------------------- #
# Massing phrasings -> operations
# --------------------------------------------------------------------------- #
#
# Checked before the intervention vocabulary, because "make the district 25%
# taller" contains no intervention noun at all and would otherwise fall through
# to "nothing recognised".
#
# Several patterns can match one sentence -- "add three 60 m towers and make the
# rest 20% shorter" is two operations -- so this returns a *list*, and the
# operations apply in the order they are found. `reset` is the exception: it
# supersedes everything, because "go back to the original heights" cannot also
# mean "and then scale by 1.25".
MASSING_PATTERNS: tuple[tuple[str, str, float | None], ...] = (
    (r"reset\s+(the\s+)?(building\s+)?heights?", "reset", None),
    (r"\b(?:original|default|base)\s+heights?\b", "reset", None),
    (r"(\d+(?:\.\d+)?)\s*(?:x|\u00d7)\s*(?:taller|higher)", "scale_all", None),
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:taller|higher)", "scale_all", None),
    (r"(\d+(?:\.\d+)?)\s*(?:x|\u00d7)\s*(?:shorter|lower)", "scale_all", None),
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:shorter|lower)", "scale_all", None),
    (r"\b(taller|higher|denser|more\s+dense)\b", "scale_all", 1.25),
    (r"\b(shorter|lower|less\s+dense)\b", "scale_all", 0.8),
)

# Resize: footprint width/depth changes. "Widen", "narrow", "broader", "make
# the blocks wider by 20%" -- these all change the footprint, not the height.
RESIZE_VERB = re.compile(
    r"\b(widen|wider|broaden|broader|narrow|narrower|shrink|smaller footprint|"
    r"enlarge|larger footprint)\b", re.IGNORECASE)
# Two word orders: "15% narrower" (adjective after) and
# "narrow ... 15%" / "widen ... by 20%" (adjective before, number after).
RESIZE_PERCENT = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:wider|broader|narrower|smaller|larger)|"
    r"\b(?:widen|broaden|narrow|shrink|enlarge)\w*\b[^.]{0,30}?"
    r"(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)

ROTATE_VERB = r"\b(rotate|rotating|rotated|turn|twist|reorient|reorienting|re-?align)"
ANGLE_RE = re.compile(r"(\d{1,3})\s*(?:deg|degree|degrees|\u00b0)")

STOREYS_RE = re.compile(r"(\d{1,2})\s*(?:storey|storeys|story|stories|floors?)\b")
METRES_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:m\b|metres?\b|meters?\b)")

# A move is expressed as an offset in metres, never as an angle: "30 m north"
# has to become the same number whatever the block's orientation, and a compass
# bearing invites a model to invent one. Direction words map to a unit vector.
DIRECTIONS: dict[str, tuple[float, float]] = {
    "north": (0.0, 1.0), "south": (0.0, -1.0),
    "east": (1.0, 0.0), "west": (-1.0, 0.0),
    "north-east": (0.7071, 0.7071), "north-west": (-0.7071, 0.7071),
    "south-east": (0.7071, -0.7071), "south-west": (-0.7071, -0.7071),
}
MOVE_VERB = r"\b(move|shift|slide|translate|nudge|displace|relocate)"


def _height_in_metres(text: str) -> tuple[float | None, bool]:
    """An absolute height, and whether it was stated or converted from storeys."""
    storeys = STOREYS_RE.search(text)
    if storeys:
        return round(float(storeys.group(1)) * STOREY_M, 1), True
    metres = METRES_RE.search(text)
    if metres:
        return float(metres.group(1)), False
    return None, False


def parse_massing_intent(instruction: str) -> list[dict[str, Any]]:
    """Extract massing operations from an instruction.

    Deterministic and inspectable, like `parse_intent`. Returns ops in the shape
    `_massing_feature` expects, so both providers converge on one materialiser,
    and it is the **no-key baseline**: if the deterministic parser cannot do
    something, then a demo without credentials silently loses a capability.

    Three things this gets right that the first version did not:

    - **"25% taller" is 1.25, not 25.** A bare capture of the digits is wrong by
      two orders of magnitude, and because `_massing_feature` accepts any
      multiplier up to 8.0 it would have been *rejected* rather than silently
      wrong -- which is better than nothing but still a failure to read the
      sentence. Percentages are now converted relative to 1.0.
    - **"20% shorter" is 0.8, not 1.2.** Shorter and taller are both relative to
      the current height, so a reduction is ``1 - pct/100``.
    - **"the western blocks" is a scope, not a modifier.** Previously the region
      word was simply dropped and the op came back as a global ``scale_all`` --
      the request was answered with a different, plausible-looking edit. Every op
      now carries the region it was scoped to.
    """
    # Hyphens are flattened to spaces for the whole parse. "12-storey housing"
    # and "north-west" are the two phrasings that need it, and both fail in a
    # quietly wrong direction without it: the first reads as "12 towers", the
    # second as "the west".
    text = instruction.lower().replace("-", " ")
    ops: list[dict[str, Any]] = []
    scope = _match_region(text)

    # --- reset: supersedes everything else in the sentence ------------------- #
    for pattern, op, _default in MASSING_PATTERNS:
        if op == "reset" and re.search(pattern, text):
            return [{"op": "reset", "scope": scope}]

    # --- rotate ------------------------------------------------------------- #
    if re.search(ROTATE_VERB, text):
        angle = ANGLE_RE.search(text)
        quarter = re.search(r"\b(quarter|half)\s*(?:turn|rotation)\b", text)
        if angle:
            degrees = float(angle.group(1))
        elif quarter:
            degrees = 90.0 if "quarter" in quarter.group(1) else 180.0
        else:
            degrees = 15.0
        if abs(degrees) <= 180.0:
            ops.append({
                "op": "rotate", "degrees": round(degrees, 2), "scope": scope,
                # Recorded so a default is never presented as an instruction.
                "angleDefaulted": angle is None and quarter is None,
            })

    # --- move: translate the footprints ------------------------------------- #
    if re.search(MOVE_VERB, text):
        amount = METRES_RE.search(text)
        direction = next(
            (d for d in DIRECTIONS
             if re.search(rf"\b{re.escape(d)}\b", text)), None,
        )
        if amount and direction:
            distance = float(amount.group(1))
            ux, uy = DIRECTIONS[direction]
            # A shift is a *request*, so it is clamped rather than rejected: a
            # planner asking for a 3 km move has made an arithmetic slip, not a
            # different question, and refusing to answer loses the sentence.
            # 500 m is more than three street widths on a 30 m block pitch.
            distance = max(-500.0, min(500.0, distance))
            ops.append({
                "op": "move",
                "dxM": round(ux * distance, 2),
                "dyM": round(uy * distance, 2),
                "direction": direction,
                "distanceM": distance,
                "scope": scope,
            })

    # --- set an absolute height, or a change by a number of metres ----------- #
    # "30 m taller" is a delta and "60 m tall" is an absolute height. Reading the
    # first as the second would set every building to 30 m, which is the kind of
    # mistake a 3D view hides completely.
    delta_match = re.search(
        r"(\d{1,3}(?:\.\d+)?)\s*(?:m\b|metres?\b|meters?\b)[^.]{0,16}?"
        r"\b(taller|higher|shorter|lower|more|less)\b",
        text,
    )
    absolute, _ = _height_in_metres(text)
    if delta_match:
        amount = float(delta_match.group(1))
        sign = -1.0 if re.search(r"shorter|lower|less", delta_match.group(2)) else 1.0
        ops.append({"op": "set_site", "deltaM": sign * amount, "scope": scope})
    elif absolute is not None and re.search(
        r"\b(set|make|all|uniform|every|each)\b|\b\d+\s*m\s*(?:tall|high)\b", text
    ):
        ops.append({"op": "set_site", "heightM": absolute, "scope": scope})

    # --- scale -------------------------------------------------------------- #
    # A bare "taller"/"shorter" with no magnitude is only read as a 25% / 20%
    # change when the sentence has not already stated one. Without this gate,
    # "make the eastern blocks 30 m taller" produced BOTH a +30 m set and a
    # x1.25 scale, and the second silently overwrote the first.
    quantified = any(op["op"] == "set_site" for op in ops)
    for pattern, op, default in MASSING_PATTERNS:
        if op != "scale_all":
            continue
        match = re.search(pattern, text)
        if not match:
            continue
        if default is not None:
            if quantified:
                break
            multiplier = default
        else:
            try:
                magnitude = float(match.group(1))
            except (IndexError, TypeError, ValueError):
                continue
            if magnitude <= 0:
                continue
            matched = match.group(0)
            if "%" in matched:
                # "25% taller" -> 1.25 ; "20% shorter" -> 0.80
                fraction = magnitude / 100.0
                multiplier = (1.0 - fraction if re.search(r"shorter|lower", matched)
                              else 1.0 + fraction)
            elif re.search(r"shorter|lower", matched):
                # "1.4x shorter" is nonsense; read it as a reduction factor.
                multiplier = 1.0 / magnitude if magnitude > 1.0 else magnitude
            else:
                multiplier = magnitude
        ops.append({"op": "scale_all", "multiplier": round(float(multiplier), 4),
                    "scope": scope})
        break

    # --- resize: footprint width/depth ------------------------------------- #
    # "Widen the blocks by 20%", "make the footprints narrower" -- these change
    # the footprint size, not the height. Width and depth scale together unless
    # the instruction names only one axis ("widen" = width only, "deepen" =
    # depth only).
    resize_match = RESIZE_PERCENT.search(text) or (
        RESIZE_VERB.search(text) and not any(op["op"] == "scale_all" for op in ops))
    if resize_match:
        pct = RESIZE_PERCENT.search(text)
        if pct:
            # Two word orders: "15% wider" (group 1) or "widen ... 20%" (group 2)
            raw = pct.group(1) or pct.group(2)
            magnitude = float(raw) / 100.0
            matched_text = pct.group(0)
            is_shrink = bool(re.search(
                r"narrow|shrink|smaller", matched_text, re.IGNORECASE))
            scale = 1.0 - magnitude if is_shrink else 1.0 + magnitude
        else:
            # Bare "widen"/"narrow" without a number: default 20%.
            verb_text = RESIZE_VERB.search(text).group(0)
            is_shrink = bool(re.search(
                r"narrow|shrink|smaller", verb_text, re.IGNORECASE))
            scale = 0.8 if is_shrink else 1.2
        # "widen"/"broaden" affects width only; "deepen" would affect depth only.
        # A generic "resize"/"enlarge" affects both.
        width_only = bool(re.search(r"\bwiden|wider|broaden|broader\b", text, re.IGNORECASE))
        depth_only = bool(re.search(r"\bdeepen|deeper\b", text, re.IGNORECASE))
        ws = round(scale, 4) if width_only or not depth_only else 1.0
        ds = round(scale, 4) if depth_only or not width_only else 1.0
        ops.append({"op": "resize", "widthScale": ws, "depthScale": ds,
                    "scope": scope})

    return ops


def _heuristic_rationale(kind: str, region: str, site: dict[str, Any]) -> str:
    """The heuristic's stated *basis* for offering one site.

    The basis is code-owned prose, never the model's, and it must be true of the
    row it sits on. Unscoped (``region == "all"``) the site is the pool's
    largest-first order, so the original wording is kept verbatim -- an
    unscoped request stays byte-identical to today's behaviour. Scoped, it names
    the region **and the rule that put the site in it** (centroid inside the
    region bounds), because ``reservoir#0`` is exactly the shape that makes the
    distinction load-bearing: its centroid is northern while its ring reaches
    the south edge, so "in the north" is a claim about the centroid and the
    string has to say so (ruling §9, the most-at-risk invariant).
    """
    if region == "all":
        return f"largest available {kind} site by {site['evidence']}"
    return (
        f"candidate {kind} site whose centroid lies in the {region} region, "
        f"ranked within the pool by {site['evidence']}"
    )


class HeuristicProvider:
    """Deterministic intent parser. **Not AI** -- the baseline and the fallback.

    Exists for two reasons. A demo that needs an API key is a demo that dies on
    submission day, and the LLM output is only worth anything if there is a
    non-AI thing to compare it against.

    It handles massing instructions too. If the fallback could only place
    interventions, then on a machine with no key the demo would silently lose
    the headline capability -- which is the worst way for a fallback to be
    worse.
    """

    name = "heuristic"

    def propose(self, instruction: str, grounding: Grounding) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        for op in parse_massing_intent(instruction):
            feature = _massing_feature(
                op, grounding, instruction, self.name,
                f"matched a massing phrasing in the instruction ({op['op']})",
            )
            if feature:
                out.append(feature)

        # The region is resolved **once**, from the instruction, and narrows the
        # pool **before** the count is applied. The order is load-bearing, not a
        # preference: "add two water reservoirs in the west" is
        # two-of-the-west = {#1, #2} -> 2. Cap-first would take the first two
        # overall = {#0, #1}, then keep only the one in the west = 1 -- answering
        # a question the user did not ask and reporting the shortfall as if the
        # west were empty. Narrow-first is the only order that answers "in the
        # west". `region` is "all" when the instruction names no region, which
        # keeps an unscoped request on today's exact code path.
        region = _match_region(instruction) or "all"
        for kind, count in parse_intent_counts(instruction):
            pool_name = POOL_FOR_KIND.get(kind)
            pool = getattr(grounding, pool_name, []) if pool_name else []
            # `eligible` is a view over the *full* pool, so each site keeps its
            # original index and its `site_id` still resolves to the right
            # geometry. Filter, never rank: an empty region yields nothing.
            eligible = sites_in_region(pool, region, grounding.bounds)
            # No count stated -> offer every eligible candidate in the region
            # (bounded by the pool's own ceiling). A stated count still caps:
            # a number is a user instruction and is never inflated.
            selected = eligible if count is None else eligible[:count]
            for _index, site in selected:
                out.append(_feature(
                    kind, site, instruction, self.name,
                    _heuristic_rationale(kind, region, site),
                ))
        return out


# --------------------------------------------------------------------------- #
# The GenAI provider
# --------------------------------------------------------------------------- #

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"

# The model is given the candidate sites and asked to *choose* among them. It
# never emits a coordinate. This is the single most important design decision in
# the module: it turns an open-ended generation problem -- where an LLM will
# confidently invent plausible coordinates anywhere on the map -- into a
# selection problem, which is what language models are actually reliable at.
# Hallucinated geometry becomes structurally impossible rather than filtered.
CHOOSE_TOOL = {
    "name": "select_intervention_sites",
    "description": (
        "Choose which candidate sites to intervene on. Pick only from the "
        "supplied site ids; never invent coordinates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "selections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "site_id": {"type": "string"},
                        "intervention_type": {
                            "type": "string",
                            "enum": sorted(set(POOL_FOR_KIND)),
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["site_id", "intervention_type", "rationale"],
                },
            },
            "caveats": {"type": "string"},
        },
        "required": ["selections"],
    },
}


# The third tool, and the one that changes what the model is *for*.
#
# `modify_massing` asks the model for numbers: a multiplier, a height, a count.
# Every one of those is a chance to be confidently wrong, and this project has
# watched qwen2.5:3b-instruct get four of them wrong in one afternoon. This tool
# asks the model for no numbers at all -- only an objective and the constraints
# it must respect. The arithmetic is done by app/services/massing_fit.py, which
# re-runs the project's own storage ledger for each candidate.
#
# The objective is an enum, and an unrecognised one is dropped rather than
# approximated. "Reduce flood exposure" and "make it nicer" are not the same
# request, and guessing which was meant would put a fabricated objective into a
# decision.
FIT_TOOL = {
    "name": "assess_building_fit",
    "description": (
        "Ask which change would actually be best for a building, given the "
        "ground it stands on. Provide the OBJECTIVE and the CONSTRAINTS only -- "
        "never a number to apply, never a height, never an area. The system "
        "enumerates the candidate changes, measures each one with its own "
        "flood-storage ledger, and returns which wins and by how much. Use this "
        "when the instruction asks what SHOULD be done, what the best option "
        "is, or asks to optimise something. Use modify_massing instead when the "
        "instruction states the change outright."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "enum": ["reduce_flood", "reduce_runoff", "reduce_heat",
                         "balanced"],
                "description": "What to optimise for. Pick the one the "
                               "instruction asks for; if it asks for several, "
                               "use 'balanced'.",
            },
            "scope": {
                "type": "string",
                "description": "Region the building is in, if the instruction "
                               "names one. Omit when it does not.",
            },
            "no_net_green_loss": {
                "type": "boolean",
                "description": "True unless the instruction permits removing "
                               "green space. Defaults to true.",
            },
            "min_green_share_100m": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "Refuse options that leave the site with less "
                               "green than this fraction within 100 m.",
            },
            "allow_height_change": {
                "type": "boolean",
                "description": "True only if the instruction permits changing "
                               "the building's height.",
            },
            "min_height_m": {"type": "number", "minimum": 1, "maximum": 400},
            "max_height_m": {"type": "number", "minimum": 1, "maximum": 400},
        },
        "required": ["objective"],
    },
}

MASSING_TOOL = {
    "name": "modify_massing",
    "description": (
        "Modify the height/orientation of the EXISTING 3D massing model. Use "
        "this whenever the instruction is about buildings: height, a height "
        "multiplier, density, or orientation. It never returns geometry and "
        "never invents buildings -- the operations adjust entities that already "
        "exist. Always set 'scope' so the edit lands where the instruction says, "
        "or 'site_id' when the instruction points at one of the listed candidate "
        "sites."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["scale_all", "set_site", "rotate",
                                     "move", "reset", "resize"],
                            "description": (
                                "scale_all: multiply heights. set_site: set or "
                                "shift heights. rotate: turn the blocks. "
                                "move: translate the blocks. "
                                "reset: restore generated heights and orientations. "
                                "resize: scale footprint width and/or depth."
                            ),
                        },
                        "scope": {
                            "type": "string",
                            "enum": sorted(REGION_FRACTIONS),
                            "description": (
                                "Which part of the study area the edit applies "
                                "to. Use 'all' when the instruction does not name "
                                "a part."
                            ),
                        },
                        "site_id": {
                            "type": "string",
                            "description": (
                                "A candidate site id, when the instruction points "
                                "at one of the listed sites. Takes precedence "
                                "over scope."
                            ),
                        },
                        "multiplier": {
                            "type": "number", "minimum": 0.25, "maximum": 4.0,
                            "description": "For scale_all. 1.25 means 25% taller.",
                        },
                        "height_m": {
                            "type": "number", "minimum": 1, "maximum": 400,
                            "description": (
                                "Absolute height in metres. For set_site."
                            ),
                        },
                        "delta_m": {
                            "type": "number", "minimum": -200, "maximum": 200,
                            "description": (
                                "Change in metres rather than an absolute height. "
                                "Use for '30 m taller'. Negative lowers."
                            ),
                        },
                        "degrees": {
                            "type": "number", "minimum": -180, "maximum": 180,
                            "description": "Clockwise rotation for rotate.",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["north", "south", "east", "west",
                                     "north-east", "north-west", "south-east",
                                     "south-west"],
                            "description": (
                                "Compass direction for move. Prefer this over "
                                "dx_m/dy_m: copy the word from the instruction "
                                "rather than converting it, because a "
                                "transposed axis is a plausible-looking wrong "
                                "answer. Requires distance_m."
                            ),
                        },
                        "distance_m": {
                            "type": "number", "minimum": 1, "maximum": 500,
                            "description": "How far to move, in metres.",
                        },
                        "dx_m": {
                            "type": "number", "minimum": -500, "maximum": 500,
                            "description": "East-positive offset in metres.",
                        },
                        "dy_m": {
                            "type": "number", "minimum": -500, "maximum": 500,
                            "description": "North-positive offset in metres.",
                        },
                        "target_lon": {
                            "type": "number", "minimum": 113.0, "maximum": 115.0,
                            "description": (
                                "For move: a direct target longitude. The "
                                "coordinate must be inside the study area; one "
                                "outside is refused. Use this when the instruction "
                                "names a specific location, e.g. 'move to the "
                                "centre'. Prefer dx_m/dy_m for relative moves."
                            ),
                        },
                        "target_lat": {
                            "type": "number", "minimum": 22.0, "maximum": 23.0,
                            "description": "For move: a direct target latitude.",
                        },
                        "width_scale": {
                            "type": "number", "minimum": 0.1, "maximum": 10.0,
                            "description": (
                                "For resize: multiply the footprint width. "
                                "1.2 = 20% wider, 0.8 = 20% narrower. "
                                "Defaults to 1.0 (no change)."
                            ),
                        },
                        "depth_scale": {
                            "type": "number", "minimum": 0.1, "maximum": 10.0,
                            "description": (
                                "For resize: multiply the footprint depth. "
                                "1.2 = 20% deeper, 0.8 = 20% shallower. "
                                "Defaults to 1.0 (no change)."
                            ),
                        },
                    },
                    "required": ["op"],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["edits"],
    },
}

MASSING_STYLE = {"color": "#ff8f3f", "opacity": 0.0}


def build_prompt(instruction: str, grounding: Grounding) -> tuple[str, list[dict]]:
    """Render the candidate sites as a compact, citable menu.

    The catalogue is sent **only when the routed tool can actually use it.** A
    bisect on 9 Oct 2026 found that the 4,431-character site catalogue combined
    with the system prompt was enough to stop ``qwen2.5:3b-instruct`` emitting any
    tool call at all: 18 completion tokens, empty content, ``finish_reason:
    stop``. The same call with the instruction alone, or the catalogue alone, or a
    short JSON payload all returned a correct tool call. And `modify_massing`
    never selects sites -- it places and reshapes buildings -- so the catalogue was
    4 KB of irrelevant text on the exact calls it broke.

    So the payload is built for the tool that was routed, not for every tool.
    Smaller, faster, and it fixes the failure rather than documenting it.
    """
    _tools, forced = route_tools(instruction)
    wants_catalogue = forced == CHOOSE_TOOL["name"]

    catalogue = []
    if wants_catalogue:
        for kind, pool in POOL_FOR_KIND.items():
            for i, site in enumerate(getattr(grounding, pool, [])):
                catalogue.append({
                    "site_id": f"{kind}#{i}",
                    "intervention_type": kind,
                    "area_ha": site["areaHa"],
                    "evidence": site["evidence"],
                    "metrics": site["metrics"],
                })
    from app.agent.prompts.intervention import INTERVENTION_SYSTEM

    system = INTERVENTION_SYSTEM.format(regions=", ".join(sorted(REGION_FRACTIONS)))
    payload: dict[str, Any] = {"instruction": instruction}
    if wants_catalogue:
        payload["candidate_sites"] = catalogue
    payload["grounding_notes"] = grounding.notes
    # Compact, not indent=2. Pretty-printing doubled the payload for no benefit to
    # a model reading JSON, and the model is the constraint here, not the reader.
    user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return system, user


class LLMProvider:
    """Anthropic Messages API client. The GenAI path the hackathon asks for.

    Gated on ``ANTHROPIC_API_KEY`` because there is no local model server
    reachable and no key in this environment. The request/response handling is
    real and is exercised against recorded fixtures in the test suite, so it is
    not untested code waiting for a key to be discovered.
    """

    name = "claude"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 transport=None):
        self.api_key = api_key or getattr(settings, "anthropic_api_key", "") or ""
        self.model = model or getattr(settings, "anthropic_model", "") or DEFAULT_MODEL
        # Injectable so tests can drive it without a network.
        self._transport = transport or _post_json

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def propose(self, instruction: str, grounding: Grounding) -> list[dict[str, Any]]:
        if not self.available:
            raise PlanGenerationError(
                "ANTHROPIC_API_KEY is not set. Either set it, or use the "
                "heuristic provider, which needs no credentials."
            )
        if not grounding.any_available():
            raise PlanGenerationError(
                "no grounded candidate sites are available; run "
                "scripts/fetch_dem.py, scripts/fetch_landcover.py and "
                "scripts/make_wetgrid.py first"
            )

        system, user = build_prompt(instruction, grounding)
        tools, forced = route_tools(instruction)
        payload = {
            "model": self.model,
            "max_tokens": 1500,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": tools,
            # The tool is chosen in route_tools(), not by the model -- see the
            # note there. Forcing it is what makes that guarantee real.
            "tool_choice": {"type": "tool", "name": forced},
        }
        raw = self._transport(ANTHROPIC_URL, self.api_key, payload)
        selections, edits, rationale = _parse_tool_use(raw)
        return self._materialise(selections, edits, instruction, grounding, rationale)

    def _materialise(self, selections: list[dict], edits: list[dict],
                     instruction: str, grounding: Grounding,
                     rationale: str = "") -> list[dict[str, Any]]:
        """Map chosen ids back to real geometry, dropping anything invalid.

        Handles both tool kinds. A massing edit carries no geometry from the
        model at all, so it goes through the same grounding checks and then
        gets the study-area or site polygon attached here.
        """
        out: list[dict[str, Any]] = []
        for sel in selections:
            site_id = str(sel.get("site_id", ""))
            kind = str(sel.get("intervention_type", ""))
            rationale = str(sel.get("rationale", "")).strip()

            if "#" not in site_id:
                continue
            pool_name, _, index = site_id.partition("#")
            pool = POOL_FOR_KIND.get(pool_name, "")
            sites = getattr(grounding, pool, []) if pool else []
            if not index.isdigit() or int(index) >= len(sites):
                # An id we did not offer. Dropped rather than guessed at.
                continue
            # Trust the id's pool over the model's own type field, so a feature
            # cannot claim to be a reservoir while sitting on a park.
            out.append(_feature(
                pool_name, sites[int(index)], instruction, self.name,
                rationale or f"selected {site_id}",
            ))
            _ = kind

        for edit in edits:
            feature = _massing_feature(edit, grounding, instruction,
                                       self.name, rationale)
            if feature:
                out.append(feature)
        return out


class OpenAICompatProvider:
    """Any OpenAI-compatible ``/chat/completions`` endpoint.

    The durability fix, and the only route to a *live* run with no vendor key at
    all. It exists because hard-coding one vendor means one outage, one billing
    problem or one blocked network kills the demo, and because:

    - **Ollama** serves an OpenAI-compatible API at ``/v1`` (verified in the gap
      brief, Appendix A), so a local model needs no key and no account;
    - OpenRouter, DeepSeek, Moonshot, Qwen/DashScope, Groq and vLLM all speak
      the same shape, so one implementation covers all of them.

    Only the transport and the response shape differ from
    :class:`LLMProvider`. ``build_prompt()`` and ``_materialise()`` are shared
    unchanged, which is deliberate: **the grounding guarantee lives in
    ``_materialise``, not in the tool plumbing**, so switching vendor cannot
    weaken it.
    """

    name = "openai-compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, transport=None):
        self.base_url = (base_url or getattr(settings, "llm_base_url", "")
                         or "").rstrip("/")
        self.api_key = api_key or getattr(settings, "llm_api_key", "") or ""
        self.model = model or getattr(settings, "llm_model", "") or ""
        self._transport = transport or _post_json

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model and
                    (self.api_key or getattr(settings, "llm_allow_no_key", False)))

    def _endpoint(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") \
            else f"{self.base_url}/chat/completions"

    def propose(self, instruction: str, grounding: Grounding) -> list[dict[str, Any]]:
        if not self.base_url or not self.model:
            raise PlanGenerationError(
                "LLM_BASE_URL and LLM_MODEL must be set for the "
                "OpenAI-compatible provider (e.g. http://localhost:11434/v1)"
            )
        if not grounding.any_available():
            raise PlanGenerationError(
                "no grounded candidate sites are available; run the fetch and "
                "make_wetgrid scripts first"
            )

        system, user = build_prompt(instruction, grounding)
        tools, forced = route_tools(instruction)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [_to_openai_tool(t) for t in tools],
            # Forced, not "auto". A small local model asked to choose between two
            # tools returns no tool call at all, which loses the feature
            # silently. route_tools() decides instead.
            "tool_choice": {"type": "function", "function": {"name": forced}},
            "max_tokens": 1500,
            # Greedy decoding. A small local model at default temperature gives a
            # different answer for the same sentence, which is unacceptable for
            # a demo: the plan a judge watches must be the plan you rehearsed.
            "temperature": 0,
        }
        raw = self._transport(self._endpoint(), self.api_key, payload)
        selections, edits, rationale = _parse_openai_tool_use(raw, forced)

        if not selections and not edits and STRUCTURED_FALLBACK and tools:
            # No usable tool call came back. Re-ask with the schema enforced.
            schema_payload = {
                **payload,
                "tools": None,
                "tool_choice": None,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": tools[0]["name"],
                        "schema": tools[0]["input_schema"],
                    },
                },
                "temperature": 0,
            }
            try:
                raw = self._transport(self._endpoint(), self.api_key, schema_payload)
            except Exception:  # noqa: BLE001 - fall through to the empty result
                raw = None
            if raw:
                content = ((raw.get("choices") or [{}])[0]
                           .get("message", {}).get("content"))
                if isinstance(content, str) and content.strip():
                    try:
                        args = json.loads(content)
                    except ValueError:
                        args = {}
                    if isinstance(args, dict):
                        # Only the routed tool's key is read. Reading both is how
                        # "add three 60 m towers in the east" once came back as
                        # three water reservoirs: the massing schema was enforced,
                        # the model filled in the *intervention* shape anyway, and
                        # the parser believed it. A schema-constrained answer is
                        # only trustworthy if it is read against the question.
                        if forced == MASSING_TOOL["name"]:
                            edits = _massing_edits(args)
                        else:
                            selections = _selections(args)
                        rationale = str(args.get("rationale", "") or "")

        return self._materialise(selections, edits, instruction, grounding, rationale)

    def _materialise(self, selections: list[dict], edits: list[dict],
                     instruction: str, grounding: Grounding,
                     rationale: str = "") -> list[dict[str, Any]]:
        """Identical validation to the Anthropic path. Shared on purpose."""
        out: list[dict[str, Any]] = []
        for sel in selections:
            site_id = str(sel.get("site_id", ""))
            pool_name, _, index = site_id.partition("#")
            pool = POOL_FOR_KIND.get(pool_name, "")
            sites = getattr(grounding, pool, []) if pool else []
            if not index.isdigit() or int(index) >= len(sites):
                continue
            out.append(_feature(
                pool_name, sites[int(index)], instruction, self.name,
                str(sel.get("rationale", "")) or f"selected {site_id}",
            ))
        for edit in edits:
            feature = _massing_feature(edit, grounding, instruction,
                                       self.name, rationale)
            if feature:
                out.append(feature)
        return out


# Fallback for models whose tool-calling is unreliable.
#
# `qwen2.5:3b-instruct` returns NO tool call at all for the massing schema --
# empty content, finish_reason "stop" -- so the feature is lost silently. Ollama
# and the OpenAI-compatible API both accept a JSON *schema* instead
# (`response_format: {"type": "json_schema"}`), which the same model satisfies
# correctly and instantly. So: try native tools first, and if no tool call comes
# back, ask for schema-constrained JSON and parse that.
#
# This does not weaken the grounding guarantee. It lives in `_massing_feature`
# and `_materialise`, which validate identically either way -- a schema-
# constrained answer still cannot invent a site id that gets used.
STRUCTURED_FALLBACK = True


def _to_openai_tool(tool: dict) -> dict:
    """Anthropic tool schema -> OpenAI function-tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


def _massing_edits(args: dict) -> list[dict]:
    """The edits in a `modify_massing` payload, tolerating a flattened answer.

    The schema says ``{"edits": [ ... ]}`` and a large model complies. A small
    local one reliably answers with the operation *itself* --
    ``{"op": "scale_all", "multiplier": 1.2, "scope": "east"}`` -- dropping the
    one-element array wrapper. Reading ``args["edits"]`` alone therefore found
    nothing and the instruction produced zero features: a working model, a correct
    answer, and a silent discard.
    """
    edits = args.get("edits")
    if isinstance(edits, list) and edits:
        return [e for e in edits if isinstance(e, dict)]
    if isinstance(args.get("op"), str):
        return [args]
    return []


def _selections(args: dict) -> list[dict]:
    selections = args.get("selections")
    return [s for s in selections if isinstance(s, dict)] if isinstance(selections, list) else []


def _parse_openai_tool_use(raw: dict, forced: str = "") -> tuple[list[dict], list[dict], str]:
    """Pull tool calls out of a chat/completions response.

    `forced` is the tool :func:`route_tools` chose. When a response somehow
    carries calls for both, only the routed one is read -- an earlier version
    merged both lists, which meant a `place` instruction could also produce
    interventions and vice versa.
    """
    selections: list[dict] = []
    edits: list[dict] = []
    rationale = ""
    choices = raw.get("choices") or []
    if not choices:
        return selections, edits, rationale
    calls = ((choices[0].get("message") or {}).get("tool_calls") or [])
    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        if forced and name != forced:
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        if name == CHOOSE_TOOL["name"] or name == MASSING_TOOL["name"]:
            selections.extend(_selections(args))
            edits.extend(_massing_edits(args))
            rationale = str(args.get("rationale", "") or "") or rationale
        else:
            rationale = rationale or str(args.get("rationale", "") or "")
    return selections, edits, rationale


def route_tools(instruction: str) -> tuple[list[dict], str]:
    """Decide which tool the model is offered, in code rather than by prompt.

    Both live providers originally advertised both tools with a permissive
    ``tool_choice``, on the reasoning that a system-prompt rule would be enough.
    A real model settled that argument: asked "make the district 25% taller to
    reduce flood risk" with both tools available, ``qwen2.5:3b-instruct``
    returned **no tool call at all** and the request produced zero features. The
    same instruction through the heuristic produced a correct ``scale_all``.

    A system-prompt instruction is a *request*. Offering one tool is a
    *guarantee*. Since the two intents are separable by a deterministic phrase
    check, the separation happens here and the model is never asked to route.
    That is strictly safer than either a prompt rule or a permissive
    ``tool_choice``, and it costs one regex.

    **``assess_building_fit`` is no longer offered** (PRD §1.2). Forcing it was
    a dead end: ``_parse_tool_use`` handles only ``select_intervention_sites``
    and ``modify_massing``, so a fit call was silently discarded and the model
    spent a turn on it while the user waited. The endpoint and its tests remain
    -- it is still reachable directly -- but a recommendation request now routes
    to the catalogue, whose answer the parser can actually deliver.
    :func:`parse_fit_intent` still reads the intent; it is simply no longer a
    routing target.
    """
    if parse_massing_intent(instruction):
        return [MASSING_TOOL], MASSING_TOOL["name"]
    return [CHOOSE_TOOL], CHOOSE_TOOL["name"]


# "What should I do here?" is a different request from "make it 60 m". These are
# the phrases that separate them. It is a phrase list rather than a model because
# the separation has to be a guarantee, not a request -- see route_tools.
FIT_PHRASES = re.compile(
    r"\b(best|best[- ]fit|optimi[sz]e|which (?:option|change)|what should|"
    r"recommend|improve|better|suit the environment|fit for|"
    r"(?:flood|heat|runoff) (?:risk|exposure|impact)|reduce (?:the )?\w+ "
    r"(?:risk|exposure))\b",
    re.IGNORECASE,
)

# "improve this building" is a fit request; "make it 60 m" is an edit. The second
# pattern below is the tie-break: a stated number wins, because a planner who says
# "60 m" has already decided and wants it applied, not optimised.
STATED_NUMBER = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:m\b|metres?|meters?|%|storeys?|floors?)", re.IGNORECASE
)


def parse_fit_intent(instruction: str) -> dict[str, Any] | None:
    """Recognise a request for a recommendation, and read its constraints.

    Returns None when the instruction states the change outright, because that is
    an edit and `modify_massing` is the right tool. A number in the instruction
    is the signal: "set it to 60 m" is a decision, "make it better for flooding"
    is a question.
    """
    text = str(instruction or "").strip()
    if not text or not FIT_PHRASES.search(text):
        return None
    if STATED_NUMBER.search(text) and not re.search(
            r"\b(best|optimi[sz]e|recommend|which|what should)\b", text,
            re.IGNORECASE):
        return None

    objective = "balanced"
    if re.search(r"\b(flood|runoff|storage|water)\b", text, re.IGNORECASE):
        objective = "reduce_flood"
    if re.search(r"\bheat|hotter|heatwave|warm\b", text, re.IGNORECASE):
        objective = "reduce_heat" if objective == "balanced" else "balanced"

    return {
        "objective": objective,
        "constraints": {
            # Permissive by instruction, conservative by default: a planner who
            # did not mention green has not given permission to remove it.
            "no_net_green_loss": not re.search(
                r"\b(even if|regardless of|if it means losing|trade off)\b",
                text, re.IGNORECASE),
            "allow_height_change": bool(re.search(
                r"\bheight|taller|shorter|storeys|floors\b", text, re.IGNORECASE)),
        },
    }


def _post_json(url: str, api_key: str, payload: dict) -> dict:
    """Minimal stdlib HTTP POST. urllib rather than a SDK, to keep deps at zero."""
    import urllib.request

    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "user-agent": "Hack4SDG-T1/1.0",
    })
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())


def _parse_tool_use(raw: dict) -> tuple[list[dict], list[dict], str]:
    """Pull both kinds of tool-use input out of a Messages API response.

    Returns ``(selections, massing_edits, rationale)``. Dispatching on the block
    name rather than assuming one tool is what lets a single call answer either
    intent. An unrecognised tool name yields empty lists, so a malformed
    response degrades to "nothing proposed" rather than an exception.
    """
    selections: list[dict] = []
    edits: list[dict] = []
    rationale = ""
    for block in raw.get("content", []) or []:
        if block.get("type") != "tool_use":
            continue
        payload = block.get("input", {}) or {}
        if not isinstance(payload, dict):
            continue
        if block.get("name") in (CHOOSE_TOOL["name"], MASSING_TOOL["name"]):
            selections.extend(_selections(payload))
            edits.extend(_massing_edits(payload))
            rationale = str(payload.get("rationale", "") or "") or rationale
        else:
            rationale = rationale or str(payload.get("rationale", "") or "")
    return selections, edits, rationale


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def live_providers() -> list[Provider]:
    """Every configured LLM provider, in preference order.

    Anthropic first because it is the path the code was written for and the
    cleanest story for judges. The OpenAI-compatible provider second, because it
    is the one that can run with no key at all via a local Ollama.
    """
    found: list[Provider] = []
    anthropic = LLMProvider()
    if anthropic.available:
        found.append(anthropic)
    compat = OpenAICompatProvider()
    if compat.available:
        found.append(compat)
    return found


def get_provider(prefer: str = "auto") -> Provider:
    """Pick a provider.

    ``auto`` uses a configured LLM and falls back to the heuristic otherwise, so
    the demo never dies for want of a credential. The fallback is reported, never
    silent: the response always names the provider that ran, and the UI shows a
    badge saying the heuristic is not AI.

    ``prefer="llm"`` is strict: with nothing configured it raises rather than
    downgrading, so a test or a demo cannot silently lose the GenAI path.
    """
    if prefer in ("auto", "llm", "claude", "openai", "local"):
        found = live_providers()
        if found:
            if prefer == "claude" and isinstance(found[0], OpenAICompatProvider):
                raise PlanGenerationError(
                    "provider=claude was requested but ANTHROPIC_API_KEY is not set"
                )
            if prefer in ("openai", "local") and not isinstance(
                found[0], OpenAICompatProvider
            ):
                raise PlanGenerationError(
                    "an OpenAI-compatible provider was requested but "
                    "LLM_BASE_URL / LLM_MODEL are not set"
                )
            return found[0]
        if prefer != "auto":
            raise PlanGenerationError(
                "no LLM provider is configured. Set ANTHROPIC_API_KEY, or "
                "LLM_BASE_URL + LLM_MODEL for any OpenAI-compatible endpoint "
                "(a local Ollama needs no key)."
            )
    if prefer in ("auto", "heuristic", "baseline"):
        return HeuristicProvider()
    raise PlanGenerationError(f"unknown provider {prefer!r}")


def _constraint_inputs(scene_id: str) -> tuple[Any, Any, Any] | None:
    """The three arguments ``validate_batch`` needs, or None when unavailable.

    One construction, read by both the propose path (`generate_proposals`) and
    the apply path (`apply_proposals`), so the gate a proposal passes on the way
    out is the gate it passes on the way in. Two copies could drift, and a
    proposal accepted under one set of masks and re-checked under another would
    mean the two halves of the confirm gate disagreed about what a constraint is.

    ``None`` means no ledger grid is on disk, which is an environment fact the
    callers report rather than an error: ``_overlay_common`` is a script-only
    module, hence the local imports.
    """
    from app.services import proposal_constraints as _constraints
    from app.services.sandbox_model import load_grids as _load_grids
    from _overlay_common import load_overlay as _load_overlay

    grids = _load_grids()
    if grids is None:
        return None
    return grids, _constraints.building_mask(_load_overlay()), \
        _constraints.claimed_cells_for(scene_id, grids)


def _answer_map(answered: Any) -> dict[str, str]:
    """The planner's answers as ``{question id: typed answer}`` (Phase 2 R13).

    Accepts either the wire ``AnsweredQuestion`` objects or plain dicts, so the
    service can be called from the router and from a test without converting
    first. An entry with no id is dropped rather than guessed at: the id is
    derived from the parameter alone, and inventing one would match a question
    the planner never answered.
    """
    out: dict[str, str] = {}
    for item in answered or []:
        if isinstance(item, Mapping):
            qid = item.get("id")
            answer = item.get("answer")
        else:
            qid = getattr(item, "id", None)
            answer = getattr(item, "answer", None)
        if qid and answer is not None:
            out[str(qid)] = str(answer)
    return out


def generate_proposals(
    scene_id: str,
    instruction: str,
    prefer: str = "auto",
    max_features: int = 6,
    allowed_types: list[str] | None = None,
    answered: Any = None,
    plan_ids: Any = None,
) -> dict[str, Any]:
    """Produce feature proposals **without persisting** them (plan v2 §5).

    Shares every grounding, fallback and parser rule with `generate_plan`:
    the only difference is this returns the proposals, where `generate_plan`
    writes them to the store. The confirm gate (§5) then lets the user accept
    a subset via `apply_proposals`.

    `allowed_types` (plan v2 §8 minimal template whitelist): if a non-empty
    list, proposals outside it are dropped and the LLM is told up front which
    types it may pick. `None` or `[]` disables the whitelist. Illegal names
    in the list are silently ignored, so an outdated client cannot wedge the
    endpoint.

    Returns a dict with keys:
      - `features`: list[dict] -- raw feature dicts (same shape as
        `_feature()`), echoed back to `apply_proposals` to persist.
      - `provider`, `groundingNotes`, `fallbackNote`, `message`: identical
        to `generate_plan`'s meta so the client can display provenance.

    `scene_id` is accepted but not written; it is kept in the signature so a
    future implementation that pre-validates against the scene (e.g. "this
    reservoir intersects a park") can without changing the wire.

    `answered` (Phase 2 R13) is the planner's answers to a previous response's
    questions. Matched by question id, an in-range numeric answer replaces the
    class default for that kind, so answering changes what the run reads rather
    than merely silencing a prompt. The answers are applied **before** the
    constraint predicates run, so a proposal is never validated at one depth
    and served at another.
    """
    _ = scene_id  # scene is read only by apply_proposals today.
    answer_map = _answer_map(answered)
    allowed_set: set[str] | None = None
    if allowed_types:
        # Clamp to the catalogue so a typo cannot silently empty the result.
        allowed_set = {t for t in allowed_types if t in POOL_FOR_KIND}
        if not allowed_set:
            raise PlanGenerationError(
                "allowedTypes contains no recognised intervention types "
                f"(valid: {sorted(POOL_FOR_KIND)})"
            )
    if not instruction or not instruction.strip():
        raise PlanGenerationError("instruction is empty")

    provider = get_provider(prefer)
    # Phase 4 T04b: the claimed set is computed BEFORE the pools are built, so a
    # candidate under prior work is never offered. The gate's own guard stays as
    # the backstop; this is prevention, not replacement.
    grounding = load_grounding(_claimed_for_pools(scene_id, plan_ids))
    if not grounding.any_available():
        # Two different reasons land here, and they need different sentences.
        # The pool filter can have emptied every pool because prior work claims
        # all of it -- and "run fetch_dem.py" would then send the planner to
        # rebuild rasters that are present and correct, while the real fix is to
        # move or remove something on the sandbox. Only when prior work is *not*
        # the cause is the missing-data sentence the true one.
        if any("already covered by work on the sandbox" in n
               for n in grounding.notes):
            raise PlanGenerationError(
                "every candidate site is already covered by work on the "
                "sandbox -- the staged plan and the toggled projects claim all "
                "of their ground, so nothing can be proposed over it. Move or "
                "remove one of them and the free ground comes back. Refusing to "
                "invent geometry."
            )
        raise PlanGenerationError(
            "no grounded candidate sites are available. Run fetch_dem.py, "
            "fetch_landcover.py and make_wetgrid.py so proposals can be tied to "
            "the area's own data. Refusing to invent geometry."
        )

    # Whitelist prefix (plan v2 §8): the LLM and the heuristic parser both read
    # `instruction` -- the heuristic greps for kind keywords, the LLM sees the
    # full text -- so prepending a constraint here does *most* of the filtering
    # without touching provider internals. The post-filter below is the
    # tie-breaker: it drops any feature a provider slipped through anyway.
    effective_instruction = instruction
    if allowed_set:
        effective_instruction = (
            "ONLY propose from these types: "
            + ", ".join(sorted(allowed_set))
            + ". " + instruction
        )

    used_provider = getattr(provider, "name", "unknown")
    provider_error = ""
    try:
        features = provider.propose(effective_instruction, grounding)[:max_features]
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # A rate limit, an outage or a timeout is the provider failing, not the
        # request being wrong. HTTPError is a URLError; its code says which. An
        # explicit llm request fails loudly (the same rule as "no key -> 422");
        # "auto" is allowed the labelled heuristic rescue below.
        code = getattr(exc, "code", None)
        provider_error = (
            "rate limited (HTTP 429)" if code == 429
            else f"HTTP {code}" if code else f"unreachable ({type(exc).__name__})"
        )
        if prefer == "llm":
            raise PlanGenerationError(
                f"The language model is {provider_error}. Nothing was generated; "
                "retry shortly or use provider=auto for the deterministic parser."
            ) from exc
        features = []

    # A provider that returns nothing has FAILED, and that is not the same thing as
    # the user having asked for nothing.
    #
    # On 9 Oct 2026 `qwen2.5:3b-instruct` was asked -- with `tool_choice` forcing
    # `modify_massing` -- to place six towers, and returned no tool call at all.
    # The structured retry then failed too, so `propose()` returned an empty list
    # and this function reported "The instruction named no recognised intervention
    # type". That was false. The instruction was recognised; the model had simply
    # not answered, and the user was told their own sentence was the problem.
    #
    # So: fall back to the deterministic parser, which handles massing
    # instructions too, and name in the response which provider actually produced
    # the result. A demo must not die because a 3B model declined to emit JSON.
    fallback_note = ""
    if not features and not isinstance(provider, HeuristicProvider):
        heuristic = HeuristicProvider()
        rescued = heuristic.propose(effective_instruction, grounding)[:max_features]
        if rescued:
            features = rescued
            fallback_note = (
                f" {used_provider} "
                + (f"was {provider_error}" if provider_error
                   else "returned no usable answer for this instruction")
                + f", so the deterministic parser answered instead "
                f"({len(rescued)} feature(s)). That is a fallback, not the "
                f"GenAI path."
            )
            used_provider = f"heuristic (after {used_provider} returned nothing)"

    # Defensive filter: a provider that ignored the prefix still cannot leak
    # non-whitelisted types. Silent, because the prefix already told the user
    # what we asked for.
    if allowed_set:
        features = [f for f in features if f.get("intervention_type") in allowed_set]

    # ---- constraints: code, not a request ---------------------------------- #
    #
    # The constraints block in the prompt (design §2.1) is a *request* to the
    # model; this is the guarantee. Every candidate -- the model's and the
    # heuristic's alike -- passes through `validate_batch` before it is returned
    # or persisted, so a provider that ignores the block cannot leak a feature
    # that sits on a surveyed building, overlaps ground already claimed, or
    # exceeds the coverage caps. `validate_batch` owns the per-batch claimed-set
    # loop, so two proposals in one batch cannot claim the same cells.
    #
    # Rejections travel back with a reason code rather than being dropped: a
    # short list must never look exhaustive, and a planner who is told "this
    # paving site contains 61 cells under surveyed buildings" can act on it.
    rejected: list[dict[str, Any]] = []
    try:
        from app.services import proposal_constraints as _constraints

        parts = _constraint_inputs(scene_id)
        if parts is None:
            grounding.notes.append(
                "constraints were not checked: no ledger grid is available "
                "(run make_wetgrid.py), so these proposals are unvalidated")
        else:
            grids, occupied, claimed = parts
            features, rejected = _constraints.validate_batch(
                grids, features, occupied, claimed, answers=answer_map)
    except Exception as exc:  # noqa: BLE001 - a validator bug must not empty the gate
        # Named, not swallowed: a proposal list that says it was validated when
        # it was not is worse than one that admits the gap.
        grounding.notes.append(
            f"constraint validation failed ({exc}); proposals are unvalidated")

    if not features:
        # A named region that holds no candidate of the requested kind is a fact
        # about the site, and it is reachable from the demo's own vocabulary
        # ("add a water reservoir in the south" -- every reservoir candidate is
        # in the north). Said in its own terms instead of the generic sentence,
        # which named neither the region nor the reason.
        region_message = empty_region_message(effective_instruction, grounding)
        # A pool emptied by prior work is a third reason, and the generic
        # sentence blames the constraints for it. "Add a water reservoir" with
        # all three basins already on the sandbox is not a constraint failure --
        # the ground is taken, and the fix is to move or remove something, not
        # to pick a different kind.
        claimed_message = _claimed_empty_message(grounding, effective_instruction)
        return {
            "features": [],
            "rejected": rejected,
            "questions": [],
            "provider": used_provider,
            "groundingNotes": grounding.notes,
            "candidateCounts": {
                "reservoir": len(grounding.reservoir_sites),
                "green": len(grounding.green_sites),
                "paving": len(grounding.paving_sites),
            },
            "message": region_message or claimed_message or (
                "Nothing could be placed: no candidate survived the "
                "constraints, or no provider returned a usable answer for this "
                "instruction. Nothing was invented."
                + (f" The language model was {provider_error}." if provider_error
                   else "")
            ),
        }

    return {
        "features": features,
        "rejected": rejected,
        # Phase 2 R8: the SITE-scope questions, once per batch. The
        # PROPOSAL-scope rows already travel on each feature's `questions`.
        "questions": _constraints.site_questions(features, answers=answer_map),
        # used_provider, not provider: after a fallback these differ, and the
        # response has to name whichever one actually produced the features.
        "provider": used_provider,
        "groundingNotes": grounding.notes,
        "candidateCounts": {
            "reservoir": len(grounding.reservoir_sites),
            "green": len(grounding.green_sites),
            "paving": len(grounding.paving_sites),
        },
        "fallbackNote": fallback_note or None,
    }


def apply_proposals(
    scene_id: str,
    instruction: str,
    features: list[dict[str, Any]],
    provider: str,
    grounding_notes: list[str],
    candidate_counts: dict[str, int],
    values: list[dict[str, Any] | None] | None = None,
    answered: Any = None,
) -> dict[str, Any]:
    """Put a confirmed subset of proposals on the sandbox (Stage 4).

    The features are pinned into the scene's **staged plan** -- the one plan
    the Interventions panel lists and every run composes -- rather than written
    as a new plan of their own. A plan per confirmation was never added to the
    run's selection, so "Applied. Run again to see the effect" ran without it;
    and it could not be listed or removed alongside everything else the user
    had added.

    ``values[i]`` are the input card's answers for ``features[i]``; a key the
    user left out goes to the AI's estimate, and which was which is recorded in
    the feature's ``params.inputSources``.

    **The batch is re-validated here, before anything is pinned** (Phase 2 R19).
    The client owns the proposals between the two calls, so a geometry or a
    parameter can change in between; without this the apply path would persist
    whatever it was handed while the proposal's own ``parameters`` block still
    described the number the server chose. A feature that no longer passes is
    refused into ``rejected[]`` rather than pinned.

    A feature already on the sandbox is *not* a conflict with itself: its own
    claimed cells are subtracted before the check, so re-confirming the same
    proposal stays the idempotent no-op `pins.pin`'s fingerprint already makes
    it.

    Returns ``planId`` (the staged plan), ``features``, ``types`` and
    ``rejected``.
    """
    from app.services import proposal_constraints as _constraints
    from app.services import revalidate as _revalidate
    from app.services import sandbox

    if not features:
        raise PlanGenerationError("no features to apply")

    # R20: an out-of-range depth is a malformed request, not a placement
    # refusal. Checked before the predicates so a client typo cannot come back
    # wearing a geometry reason code that a UI would group with a real refusal.
    try:
        _revalidate._reject_malformed(features)
    except _revalidate.RevalidationError as exc:
        raise PlanGenerationError(str(exc)) from exc

    try:
        parts = _constraint_inputs(scene_id)
    except Exception as exc:  # noqa: BLE001 - a validator bug must not empty the gate
        raise PlanGenerationError(
            f"could not check the constraints ({exc}); nothing was applied") from exc
    if parts is None:
        raise PlanGenerationError(
            "no ledger grid is available (run scripts/make_wetgrid.py), so "
            "these proposals cannot be checked; nothing was applied")
    grids, occupied, claimed = parts
    answer_map = _answer_map(answered)

    # Each feature's own ground is not a conflict with itself. Subtracting it is
    # what keeps a second confirmation of the same proposal a no-op rather than
    # an OVERLAPS_STAGED refusal against the row it would have resumed.
    mine = set()
    for f in features:
        try:
            coords = (f.get("geometry") or {}).get("coordinates")
            mine |= {(int(r), int(c))
                     for r, c in _constraints._polygon_cells(grids, coords)}
        except Exception:  # noqa: BLE001 - an unreadable geometry is refused below
            continue
    accepted, rejected = _constraints.validate_batch(
        grids, features, occupied, claimed - mine, answers=answer_map)
    if not accepted:
        return {
            "planId": None,
            "features": 0,
            "provider": provider,
            "types": [],
            "groundingNotes": grounding_notes,
            "rejected": rejected,
        }
    features = accepted

    try:
        staged = sandbox.stage(
            scene_id,
            [{
                "geometry": f["geometry"],
                "interventionType": f["intervention_type"],
                "source": f.get("source", "ai"),
                "params": {**(f.get("params") or {}),
                           "provenance": {"source": "ai-plan",
                                          "provider": provider,
                                          "instruction": instruction[:200]}},
                "style": f.get("style") or {},
            } for f in features],
            values,
            source="ai",
        )
    except sandbox.InputError as exc:
        raise PlanGenerationError(str(exc)) from exc
    return {
        "planId": staged["planId"],
        "features": staged["features"],
        "provider": provider,
        "types": staged["types"],
        "groundingNotes": grounding_notes,
        "candidateCounts": candidate_counts,
        "rejected": rejected,
    }


def _legacy_apply_as_new_plan(
    scene_id: str,
    instruction: str,
    features: list[dict[str, Any]],
    provider: str,
    grounding_notes: list[str],
    candidate_counts: dict[str, int],
) -> dict[str, Any]:
    """Persist proposals as a plan of their own: the pre-sandbox behaviour.

    Kept only for ``generate_plan``, the legacy one-shot path some older test
    suites still drive. Nothing in the app calls it.
    """
    from app.schemas.plan import PlanCreate
    from app.services import scene_service

    if not features:
        raise PlanGenerationError("no features to apply")

    # Persist only what a *stored* intervention has. The proposal wire shape is
    # additive (design §2.2): it carries `size`, `requested`, `constraints`,
    # `parameters`, `status` and `warning` for the UI, and `InterventionIn` --
    # which PlanCreate validates every item through -- forbids unknown keys, so
    # passing a proposal dict straight through rejects the whole plan with an
    # `extra_forbidden` error. Projecting onto the five stored fields keeps this
    # a thin wrapper rather than a second validation pipeline; nothing the
    # record needs is lost, because the display fields the UI reads live in
    # `params` (the materialiser puts rationale/siteId/basis there).
    stored = [
        {
            "geometry": f["geometry"],
            "intervention_type": f["intervention_type"],
            "source": f.get("source", "ai"),
            "params": f.get("params") or {},
            "style": f.get("style") or {},
        }
        for f in features
    ]

    payload = PlanCreate.model_validate({
        "planType": "comprehensive",
        "label": f"AI: {instruction[:60]}",
        "source": "ai",
        "nlInstruction": instruction,
        "params": {
            "provider": provider,
            "groundingNotes": grounding_notes,
            "candidateCounts": candidate_counts,
            "disclaimer": (
                "Machine-proposed, screening level, status=draft. Every site is "
                "tied to a raster in params.basis; none is a design."
            ),
        },
        "interventions": stored,
    })
    created = scene_service.create_plan(scene_id, payload)
    return {
        "planId": created.plan_id,
        "features": created.intervention_count,
        "provider": provider,
        "types": sorted({f["intervention_type"] for f in features}),
        "groundingNotes": grounding_notes,
    }


def generate_plan(
    scene_id: str,
    instruction: str,
    prefer: str = "auto",
    max_features: int = 6,
    allowed_types: list[str] | None = None,
) -> dict[str, Any]:
    """Propose + persist, as one call (the legacy single-shot path).

    Equivalent to `generate_proposals` followed by `apply_proposals`. Kept
    for callers that bypass the confirm gate (currently none in production
    code; tests pre-dating plan v2 still use it). New callers should go
    through the two-step path so the user can accept a subset.
    """
    result = generate_proposals(scene_id, instruction, prefer, max_features,
                                allowed_types=allowed_types)
    features = result["features"]
    if not features:
        return {
            "planId": None,
            "features": 0,
            "provider": result["provider"],
            "groundingNotes": result["groundingNotes"],
            "message": result.get("message"),
        }

    applied = _legacy_apply_as_new_plan(
        scene_id,
        instruction,
        features,
        result["provider"],
        result["groundingNotes"],
        result["candidateCounts"],
    )
    applied["fallbackNote"] = result.get("fallbackNote")
    return applied
