"""Write the API schema to contracts/openapi.json -- the checked-in contract.

The Pydantic schemas are the source of truth; this file is generated from them,
so it cannot be edited into disagreement. `tests/test_openapi_contract.py`
regenerates it and fails if the committed copy differs, which is what stops the
contract going stale the way hand-written docs do.

Run after any change to a route or schema, from backend/:

    python -m app.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def render() -> str:
    """The schema as the exact text that gets committed.

    Sorted keys and a trailing newline so the output is byte-stable: the same
    app always renders the same file, whatever order routes were registered in.
    """
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the file is committed with LF whatever platform wrote it.
    with CONTRACT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render())
    print(f"wrote {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
