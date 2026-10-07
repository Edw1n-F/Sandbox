#!/usr/bin/env python
"""Generate an intervention plan from a natural-language instruction.

    python scripts/ai_plan.py "add two water reservoirs in the deepest basins"
    python scripts/ai_plan.py "..." --provider llm
    python scripts/ai_plan.py "..." --dry-run      # show what would be made

The CLI form of ``POST /api/scenes/{sceneId}/ai-plan``, so the GenAI path can be
demonstrated without a browser -- useful when the demo machine has no display,
and useful for scripted check that the module still works.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.ai_plan import (  # noqa: E402
    HeuristicProvider,
    LLMProvider,
    PlanGenerationError,
    generate_plan,
    get_provider,
    load_grounding,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instruction", help="what you want built, in plain language")
    parser.add_argument("--scene-id", default=None)
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "llm", "heuristic"])
    parser.add_argument("--max-features", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve the provider and grounding, create nothing")
    args = parser.parse_args()

    print(f"[ai] instruction: {args.instruction}")
    llm = LLMProvider()
    print(f"[ai] llm available: {llm.available}"
          f"{f' (model {llm.model})' if llm.available else ' -- no ANTHROPIC_API_KEY'}")
    provider = get_provider(args.provider)
    print(f"[ai] provider:     {getattr(provider, 'name', '?')}")

    grounding = load_grounding()
    print(f"[ai] grounded sites: reservoir={len(grounding.reservoir_sites)} "
          f"green={len(grounding.green_sites)} paving={len(grounding.paving_sites)}")
    for note in grounding.notes:
        print(f"[ai]   {note}")

    if args.dry_run:
        if isinstance(provider, HeuristicProvider):
            feats = provider.propose(args.instruction, grounding)
        else:
            print("[ai] dry-run cannot call the LLM; showing the request shape only")
            from app.services.ai_plan import build_prompt
            system, user = build_prompt(args.instruction, grounding)
            print(json.dumps({
                "system": system,
                "candidate_site_count": len(json.loads(user)["candidate_sites"]),
                "tools": ["select_intervention_sites"],
            }, indent=2)[:600])
            return 0
        print(f"[ai] would create {len(feats)} feature(s):")
        for f in feats:
            print(f"[ai]   {f['intervention_type']:<18} "
                  f"{f['params']['siteAreaHa']:>7} ha  {f['params']['basis'][:46]}")
        return 0

    scene_id = args.scene_id
    if not scene_id:
        for path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(path)
            if doc and doc.get("study_area"):
                scene_id = doc["id"]
                break
    if not scene_id:
        print("[ai] no seeded scene; run scripts/seed_scene.py first")
        return 1

    try:
        result = generate_plan(scene_id, args.instruction,
                               prefer=args.provider, max_features=args.max_features)
    except PlanGenerationError as exc:
        print(f"[ai] refused: {exc}")
        return 1

    if not result["planId"]:
        print(f"[ai] {result['message']}")
        return 0
    print(f"[ai] created plan {result['planId']} with {result['features']} feature(s)")
    print(f"[ai] types: {', '.join(result['types'])}")
    print("[ai] every feature is status=draft and cites the raster it came from")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
