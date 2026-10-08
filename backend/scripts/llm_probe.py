#!/usr/bin/env python
"""Prove the LLM path end to end, and never print a secret.

    python scripts/llm_probe.py
    python scripts/llm_probe.py --instruction "make the district 25% taller"

This is the artefact that demonstrates the round trip ran. Three checks, in
increasing cost:

1. **Configuration** -- which providers are configured. Never prints a key, only
   whether one is present and its length.
2. **Model id** -- for Anthropic, `GET /v1/models` lists valid ids. Query it
   rather than trusting a hard-coded string, because a stale id is a 404 on the
   day it matters and that is an avoidable failure.
3. **One real call** -- a real `propose()` against a small grounding, printing
   the provider, the latency, and the features that came out.

Exit code is 0 when a live provider answered, 1 when nothing is configured.
That distinction matters: "not configured" and "configured but broken" are
different problems and the script keeps them apart.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.ai_plan import (  # noqa: E402
    ANTHROPIC_URL,
    ANTHROPIC_VERSION,
    LLMProvider,
    OpenAICompatProvider,
    PlanGenerationError,
    load_grounding,
)

MODELS_URL = "https://api.anthropic.com/v1/models"


def _redact(value: str) -> str:
    """Never print a secret. Length is enough to spot an empty string."""
    return f"<set, {len(value)} chars>" if value else "<empty>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruction", default="add a water reservoir")
    parser.add_argument("--skip-call", action="store_true",
                        help="configuration and model-id checks only")
    args = parser.parse_args()

    print("llm probe")
    print(f"  ANTHROPIC_API_KEY : {_redact(settings.anthropic_api_key)}")
    print(f"  ANTHROPIC_MODEL   : {settings.anthropic_model or '<empty>'}")
    print(f"  LLM_BASE_URL      : {settings.llm_base_url or '<empty>'}")
    print(f"  LLM_API_KEY       : {_redact(settings.llm_api_key)}")
    print(f"  LLM_MODEL         : {settings.llm_model or '<empty>'}")

    anthropic = LLMProvider()
    compat = OpenAICompatProvider()
    print(f"  anthropic ready   : {anthropic.available}")
    print(f"  openai-compat ready: {compat.available}")

    if not (anthropic.available or compat.available):
        print("\nNO LIVE PROVIDER CONFIGURED — the demo would run the "
              "heuristic, which is not AI.")
        print("  Option A: export ANTHROPIC_API_KEY=... (and set ANTHROPIC_MODEL)")
        print("  Option B: install Ollama, then:")
        print("      ollama pull <a tool-capable model>")
        print("      export LLM_BASE_URL=http://localhost:11434/v1")
        print("      export LLM_MODEL=<model>")
        return 1

    # --- model id ---------------------------------------------------------- #
    if anthropic.available:
        print("\n  checking model id against /v1/models ...")
        request = urllib.request.Request(
            MODELS_URL,
            headers={"x-api-key": settings.anthropic_api_key,
                     "anthropic-version": ANTHROPIC_VERSION},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read())
            ids = [m.get("id", "") for m in data.get("data", [])]
            print(f"    HTTP 200, {len(ids)} model(s) available")
            configured = settings.anthropic_model
            if configured in ids:
                print(f"    configured model {configured!r} is valid")
            else:
                near = [i for i in ids if configured and configured.split("-")[0] in i]
                print(f"    WARNING: {configured!r} was not in the list.")
                print(f"    closest: {near[:4] or ids[:4]}")
        except urllib.error.HTTPError as exc:
            print(f"    HTTP {exc.code} — key rejected or id unknown")
        except Exception as exc:  # noqa: BLE001
            print(f"    could not reach {MODELS_URL}: {str(exc)[:70]}")

    if args.skip_call:
        return 0

    # --- one real call ----------------------------------------------------- #
    print(f"\n  live call: {args.instruction!r}")
    grounding = load_grounding()
    if not grounding.any_available():
        print("    no grounded sites — run fetch_dem/fetch_landcover/make_wetgrid")
        return 1
    print(f"    grounding: {len(grounding.reservoir_sites)} reservoir, "
          f"{len(grounding.green_sites)} green, {len(grounding.paving_sites)} paving")

    provider = anthropic if anthropic.available else compat
    started = time.perf_counter()
    try:
        features = provider.propose(args.instruction, grounding)
    except PlanGenerationError as exc:
        print(f"    refused: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"    FAILED after {time.perf_counter()-started:.1f}s: "
              f"{type(exc).__name__}: {str(exc)[:120]}")
        return 1
    elapsed = time.perf_counter() - started

    print(f"    provider {provider.name!r} answered in {elapsed:.1f}s")
    print(f"    {len(features)} feature(s)")
    for f in features:
        params = f.get("params", {})
        print(f"      {f['intervention_type']:<18} source={f['source']} "
              f"basis={str(params.get('basis'))[:44]}")
    print(f"    endpoint: {ANTHROPIC_URL if provider is anthropic else compat._endpoint()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
