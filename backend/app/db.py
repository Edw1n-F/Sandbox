"""File-backed store primitives -- the ``app.db`` seam.

The spec's §1.2 puts "database session and connection" here.  Under
``STORE_MODE=file`` that role is filled by a small, atomic JSON document store
instead of a SQLAlchemy session, so the rest of the codebase keeps talking to
one module and PostGIS remains a drop-in replacement (ADR-020).

What this deliberately does *not* do: no indexing, no querying, no
transactions across documents.  At prototype scale the scene set is a handful
of rows; the cascade in ``scene_service`` is the only invariant that needs to
hold, and it is enforced there.

Write path is atomic (temp file + ``os.replace``) so a crashed write can never
leave a half-written scene on disk.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

# One process-local lock.  uvicorn --reload runs a single worker, and a
# read-modify-write across two requests is the only interleaving that matters.
_LOCK = threading.RLock()


def utcnow() -> str:
    """ISO-8601 UTC, second precision, always suffixed with Z.

    Serialised as a string rather than kept as a datetime so the store stays
    trivially inspectable with ``cat`` -- which is the whole point of file mode.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def new_id() -> str:
    """UUID4 string, matching the DDL's ``gen_random_uuid()``."""
    import uuid

    return str(uuid.uuid4())


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_doc(path: Path) -> dict[str, Any] | None:
    """Read one document, or ``None`` if it does not exist.

    A corrupt file raises rather than returning ``None``: silently treating
    unreadable data as "not found" would turn a bug into a phantom 404.
    """
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_doc(path: Path, payload: dict[str, Any]) -> None:
    with _LOCK:
        _atomic_write(path, payload)


def delete_doc(path: Path) -> bool:
    """Remove one document.  Returns whether anything was there."""
    with _LOCK:
        if not path.exists():
            return False
        path.unlink()
        return True


def scene_path(scene_id: str) -> Path:
    return settings.scenes_dir / f"{scene_id}.json"


def plan_path(plan_id: str) -> Path:
    return settings.plans_dir / f"{plan_id}.json"


def intervention_path(intervention_id: str) -> Path:
    return settings.interventions_dir / f"{intervention_id}.json"


def list_docs(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def assert_store_mode() -> None:
    """Fail loudly rather than silently degrading if the mode is unknown."""
    mode = settings.store_mode.lower()
    if mode not in {"file", "postgis"}:
        raise RuntimeError(
            f"STORE_MODE={settings.store_mode!r} is not implemented. "
            "Use 'file' (prototype) or 'postgis' (target, needs Docker/PostGIS)."
        )
