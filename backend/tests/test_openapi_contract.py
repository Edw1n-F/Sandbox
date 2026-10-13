"""The committed API contract matches the running app.

`contracts/openapi.json` is generated from the Pydantic schemas by
`app/export_openapi.py`. If a route or schema changes and the file is not
regenerated, the contract the frontend reads has silently gone stale. This test
regenerates it in memory and compares, so that drift fails here instead of in a
reader's head.

The other half matters as much: a test that only checks "the file exists" would
pass on a stale file. So `test_a_stale_contract_is_detected` builds a deliberately
wrong copy and asserts the comparison catches it.

Run:  python tests/test_openapi_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.export_openapi import CONTRACT_PATH, render  # noqa: E402

FIX = "cd backend && python -m app.export_openapi"


def _committed() -> str:
    # newline="" keeps the file's own line endings, so a CRLF checkout is read
    # as written rather than silently translated into agreement.
    with CONTRACT_PATH.open(encoding="utf-8", newline="") as fh:
        return fh.read()


def _normalised(text: str) -> str:
    """LF-only, so a Windows autocrlf checkout does not read as drift."""
    return text.replace("\r\n", "\n")


def test_the_contract_file_exists() -> None:
    assert CONTRACT_PATH.exists(), f"missing {CONTRACT_PATH}; run: {FIX}"


def test_the_contract_matches_the_running_app() -> None:
    assert _normalised(_committed()) == render(), (
        f"contracts/openapi.json is stale. A route or schema changed without "
        f"regenerating it. Run: {FIX}"
    )


def test_rendering_is_byte_stable() -> None:
    # If two renders of the same app differ, the drift check above would flake.
    assert render() == render()


def test_the_contract_is_valid_json_with_the_documented_routes() -> None:
    paths = json.loads(_committed())["paths"]
    # The routes CLAUDE.md's API contract section documents, plus the two the
    # frontend cannot work without. Exact templates, not prefixes: a prefix
    # match would pass with any one of `/api/scenes/...` present.
    for route in (
        "/health",
        "/api/scenes",
        "/api/scenes/{scene_id}",
        "/api/scenes/{scene_id}/terrain",
        "/api/scenes/{scene_id}/plans",
        "/api/plans/{plan_id}",
        "/api/plans/{plan_id}/interventions",
        "/api/scenes/{scene_id}/run",
        "/api/scenes/{scene_id}/chat/stream",
    ):
        assert route in paths, f"{route} is documented but missing from the contract"


def test_a_stale_contract_is_detected() -> None:
    """The guard must fire on a wrong file, or it is not a guard."""
    stale = json.loads(render())
    stale["paths"].pop(next(iter(stale["paths"])))
    stale_text = json.dumps(stale, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert _normalised(stale_text) != render()


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
