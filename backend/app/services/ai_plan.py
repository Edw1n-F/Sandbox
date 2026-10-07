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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# This file lives at backend/app/services/, so three parents to reach backend/.
# (The scripts/ modules use two, being one level shallower -- an easy off-by-one
# that silently puts backend/app on the path instead of backend/.)
BACKEND_DIR = Path(__file__).resolve().parents[2]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.config import settings  # noqa: E402
from app.schemas.intervention import InterventionIn  # noqa: E402

# The intervention vocabulary the spec itself uses (§4.1 examples).
SPEC_TYPES: dict[str, str] = {
    "绿地": "green_space",
    "透水铺装": "permeable_paving",
    "蓄水池": "water_reservoir",
    "道路": "road",
}

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
    "wind": "wind",
    "windbreak": "wind",
    "ventilation": "wind",
    "podium": "podium",
    "reorient": "reorient",
    "rotate": "reorient",
    "angle": "reorient",
    "reservoir": "water_reservoir",
    "pond": "water_reservoir",
}


@dataclass
class Grounding:
    """Candidate sites derived from the analysis, not invented.

    Everything the generator can place, with the evidence for each. If this is
    empty the generator has nothing legitimate to do and must say so.
    """

    reservoir_sites: list[dict[str, Any]] = field(default_factory=list)
    green_sites: list[dict[str, Any]] = field(default_factory=list)
    paving_sites: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def any_available(self) -> bool:
        return bool(self.reservoir_sites or self.green_sites or self.paving_sites)


class PlanGenerationError(RuntimeError):
    """Raised when a proposal cannot be grounded. Never swallowed."""


# --------------------------------------------------------------------------- #
# Grounding: candidate sites from the analysis
# --------------------------------------------------------------------------- #


def load_grounding() -> Grounding:
    """Derive candidate intervention sites from the DEM, land cover and wet grid.

    This is the part that makes an AI-generated plan defensible. Every candidate
    carries the raster it came from, and the geometry handed back to the model is
    a *choice among these sites*, never a free coordinate.

    Returns an empty :class:`Grounding` (with notes) when the analysis has not
    been run, so the caller can refuse rather than invent.
    """
    import numpy as np

    from _overlay_common import load_overlay

    g = Grounding()
    try:
        ov = load_overlay()
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

    def site(ring, kind, evidence, metrics):
        return {
            "kind": kind,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "centroid": [
                round((ring[0][0] + ring[2][0]) / 2, 6),
                round((ring[0][1] + ring[2][1]) / 2, 6),
            ],
            "areaHa": round(
                (max(p[0] for p in ring) - min(p[0] for p in ring))
                * 111_320.0 * math.cos(math.radians((lat_min + lat_max) / 2))
                * (max(p[1] for p in ring) - min(p[1] for p in ring))
                * 110_574.0 / 10_000.0, 1),
            "evidence": evidence,
            "metrics": metrics,
        }

    # --- reservoirs: the deepest connected basins of the wet grid ----------
    depth_file = settings.rasters_dir / "wetgrid_hsk.npy"
    if depth_file.exists():
        depth = np.load(depth_file).astype(np.float64)
        if depth.shape == (ov.rows, ov.cols):
            mask = depth >= 0.35
            for min_r, min_c, max_r, max_c, size in _components(mask, 40)[:3]:
                sub = depth[min_r:max_r + 1, min_c:max_c + 1]
                g.reservoir_sites.append(site(
                    extent_ring(min_r, min_c, max_r, max_c),
                    "water_reservoir",
                    "deepest connected basin in the computed wet grid",
                    {
                        "maxDepthM": round(float(sub.max()), 2),
                        "wetCells": size,
                        "wetAreaHa": round(size * cell_w * cell_h / 10_000.0, 1),
                    },
                ))
        else:
            g.notes.append(
                f"wetgrid_hsk.npy is {depth.shape}, expected "
                f"{(ov.rows, ov.cols)}; re-run make_wetgrid.py"
            )
    else:
        g.notes.append("no wetgrid_hsk.npy; run scripts/make_wetgrid.py")

    # --- green space: largest contiguous vegetated blocks -------------------
    for min_r, min_c, max_r, max_c, size in _components(ov.class_mask("green"), 120)[:3]:
        g.green_sites.append(site(
            extent_ring(min_r, min_c, max_r, max_c), "green_space",
            "largest contiguous vegetated block (ESA WorldCover class 'green')",
            {"greenCells": size,
             "greenAreaHa": round(size * cell_w * cell_h / 10_000.0, 1)},
        ))

    # --- permeable paving: the biggest contiguous impervious blocks ---------
    for min_r, min_c, max_r, max_c, size in _components(ov.class_mask("paved"), 150)[:3]:
        g.paving_sites.append(site(
            extent_ring(min_r, min_c, max_r, max_c), "permeable_paving",
            "largest contiguous impervious block (ESA WorldCover class 'paved')",
            {"pavedCells": size,
             "pavedAreaHa": round(size * cell_w * cell_h / 10_000.0, 1)},
        ))

    g.notes.append(f"terrain={ov.terrain_source}, landcover={ov.landcover_source}")
    return g


def _components(mask, min_size: int) -> list[tuple[int, int, int, int, int]]:
    """8-connected components -> (min_r, min_c, max_r, max_c, size), largest first."""
    import numpy as np

    seen = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, int, int]] = []
    rows, cols = mask.shape
    for r0 in range(rows):
        for c0 in range(cols):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            stack = [(r0, c0)]
            seen[r0, c0] = True
            cells = []
            while stack:
                r, c = stack.pop()
                cells.append((r, c))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and not seen[rr, cc]:
                            seen[rr, cc] = True
                            stack.append((rr, cc))
            if len(cells) >= min_size:
                arr = np.array(cells)
                out.append((
                    int(arr[:, 0].min()), int(arr[:, 1].min()),
                    int(arr[:, 0].max()), int(arr[:, 1].max()),
                    len(cells),
                ))
    out.sort(key=lambda t: -t[4])
    return out


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


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
    "permeable_paving": {"color": "#8957e5", "opacity": 0.45},
    "drainage": {"color": "#e3b341", "opacity": 0.95},
    "road": {"color": "#6e7681", "opacity": 0.8},
}

# Which grounding pool each kind draws from.
POOL_FOR_KIND: dict[str, str] = {
    "water_reservoir": "reservoir_sites",
    "green_space": "green_sites",
    "permeable_paving": "paving_sites",
    "drainage": "reservoir_sites",
    "road": "paving_sites",
}


def parse_intent(instruction: str) -> list[tuple[str, int]]:
    """Extract (kind, count) intents from an instruction.

    Deliberately simple and inspectable: the count defaults to 1, and a phrase
    with no recognised verb yields nothing rather than a guess. This is the
    non-AI baseline -- if the LLM cannot beat this, it is not earning its place.
    """
    text = instruction.lower()
    found: list[tuple[str, int]] = []
    for alias, kind in EN_ALIASES.items():
        if alias not in text:
            continue
        # "three reservoirs" / "3 reservoirs"
        window = text[max(0, text.index(alias) - 24): text.index(alias)]
        digits = re.findall(r"\b(\d{1,2})\b", window)
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "a ": 1, "an ": 1, "several": 3, "some": 2}
        count = int(digits[-1]) if digits else next(
            (v for w, v in words.items() if w in window), 1
        )
        if all(kind != k for k, _ in found):
            found.append((kind, max(1, min(count, 3))))
    return found


class HeuristicProvider:
    """Deterministic intent parser. **Not AI** -- the baseline and the fallback.

    Exists for two reasons. A demo that needs an API key is a demo that dies on
    submission day, and the LLM output is only worth anything if there is a
    non-AI thing to compare it against.
    """

    name = "heuristic"

    def propose(self, instruction: str, grounding: Grounding) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind, count in parse_intent(instruction):
            pool = POOL_FOR_KIND.get(kind)
            sites = getattr(grounding, pool, []) if pool else []
            for site in sites[:count]:
                out.append(_feature(
                    kind, site, instruction, self.name,
                    f"largest available {kind} site by {site['evidence']}",
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


def build_prompt(instruction: str, grounding: Grounding) -> tuple[str, list[dict]]:
    """Render the candidate sites as a compact, citable menu."""
    catalogue = []
    for kind, pool in POOL_FOR_KIND.items():
        for i, site in enumerate(getattr(grounding, pool, [])):
            catalogue.append({
                "site_id": f"{kind}#{i}",
                "intervention_type": kind,
                "area_ha": site["areaHa"],
                "evidence": site["evidence"],
                "metrics": site["metrics"],
            })
    system = (
        "You are a flood-risk planner for a Hong Kong new development area. "
        "You select intervention sites from a fixed candidate list derived from "
        "the area's own terrain, land cover and flood model. You never invent "
        "coordinates. Prefer fewer, better-justified sites over many. Every "
        "site you choose must be defensible from the evidence given. If no site "
        "is appropriate, return an empty selection list rather than forcing one."
    )
    user = json.dumps({
        "instruction": instruction,
        "candidate_sites": catalogue,
        "grounding_notes": grounding.notes,
    }, ensure_ascii=False, indent=2)
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
        payload = {
            "model": self.model,
            "max_tokens": 1500,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [CHOOSE_TOOL],
            "tool_choice": {"type": "tool", "name": CHOOSE_TOOL["name"]},
        }
        raw = self._transport(ANTHROPIC_URL, self.api_key, payload)
        selections = _parse_tool_use(raw)
        return self._materialise(selections, instruction, grounding)

    def _materialise(self, selections: list[dict], instruction: str,
                     grounding: Grounding) -> list[dict[str, Any]]:
        """Map chosen site ids back to real geometry, dropping anything invalid."""
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
        return out


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


def _parse_tool_use(raw: dict) -> list[dict]:
    """Pull the tool-use input out of a Messages API response."""
    for block in raw.get("content", []) or []:
        if block.get("type") == "tool_use" and block.get("name") == CHOOSE_TOOL["name"]:
            return list(block.get("input", {}).get("selections", []) or [])
    return []


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def get_provider(prefer: str = "auto") -> Provider:
    """Pick a provider.

    ``auto`` uses the LLM when a key is present and falls back to the heuristic
    otherwise, so the demo never dies for want of a credential. The fallback is
    reported, never silent: the plan records which provider produced it.
    """
    llm = LLMProvider()
    if prefer in ("auto", "llm", "claude"):
        if llm.available:
            return llm
        if prefer == "llm":
            raise PlanGenerationError(
                "the LLM provider was requested but ANTHROPIC_API_KEY is not set"
            )
    if prefer in ("auto", "heuristic", "baseline"):
        return HeuristicProvider()
    raise PlanGenerationError(f"unknown provider {prefer!r}")


def generate_plan(
    scene_id: str,
    instruction: str,
    prefer: str = "auto",
    max_features: int = 6,
) -> dict[str, Any]:
    """Turn a natural-language instruction into a draft plan on a scene.

    This is the AI module's whole public surface. The output goes through the
    *same* validation, storage and render path as a hand-drawn feature -- it is
    GeoJSON, nothing more, which is what §3.3.2 requires.

    Returns a summary including the provider actually used, so the UI and the
    pitch can say which produced the plan.
    """
    if not instruction or not instruction.strip():
        raise PlanGenerationError("instruction is empty")

    from app.schemas.plan import PlanCreate
    from app.services import scene_service

    provider = get_provider(prefer)
    grounding = load_grounding()
    if not grounding.any_available():
        raise PlanGenerationError(
            "no grounded candidate sites are available. Run fetch_dem.py, "
            "fetch_landcover.py and make_wetgrid.py so proposals can be tied to "
            "the area's own data. Refusing to invent geometry."
        )

    features = provider.propose(instruction, grounding)[:max_features]
    if not features:
        return {
            "planId": None,
            "features": 0,
            "provider": getattr(provider, "name", "unknown"),
            "groundingNotes": grounding.notes,
            "message": (
                "The instruction named no recognised intervention type, so no "
                "sites were selected. Nothing was invented."
            ),
        }

    # Every feature is validated by the same schema as a manual one, which is
    # where ADR-030's draft rule is enforced.
    payload = PlanCreate.model_validate({
        "planType": "comprehensive",
        "label": f"AI: {instruction[:60]}",
        "source": "ai",
        "nlInstruction": instruction,
        "params": {
            "provider": getattr(provider, "name", "unknown"),
            "groundingNotes": grounding.notes,
            "candidateCounts": {
                "reservoir": len(grounding.reservoir_sites),
                "green": len(grounding.green_sites),
                "paving": len(grounding.paving_sites),
            },
            "disclaimer": (
                "Machine-proposed, screening level, status=draft. Every site is "
                "tied to a raster in params.basis; none is a design."
            ),
        },
        "interventions": features,
    })
    created = scene_service.create_plan(scene_id, payload)
    return {
        "planId": created.plan_id,
        "features": created.intervention_count,
        "provider": getattr(provider, "name", "unknown"),
        "types": sorted({f["intervention_type"] for f in features}),
        "groundingNotes": grounding.notes,
    }
