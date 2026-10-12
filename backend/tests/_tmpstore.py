"""Redirect the file store at a throwaway copy, for the suites that write.

The suites that *create* records (plans, interventions, run
overlays) used to write into the real store in ``backend/data``, so running the test
suite accumulated scene, plan and intervention documents in the working tree.
18 plans had built up before the 12 Oct 2026 cleanup, and ``.gitignore`` only
stopped them being *committed* -- it did nothing about the accumulation.

The side effect was worse than untidiness: ``test_ledger``'s "no live seeded
intervention is over-large" check reads the store, so a stray over-large plan
left behind by an earlier run could turn a real check red. A suite whose result
depends on what previous runs left on disk is not a suite.

## Why a copy rather than an empty directory

Scenes, plans and rasters all live under one ``data_dir`` (see
``app.config.Settings``), and the suites need to *read* the seeded scene, the
canonical rasters and the committed city model. Pointing ``data_dir`` at an empty
temp dir would break every read. So the whole tree is copied (15 MB, once per
suite) and only the copy is written to: reads are identical, writes are
discarded, and the real store is untouched.

``shutil.copytree`` preserves mtimes, which matters -- the run service caches
the conditioned grid on ``wetgrid_hsk.npy``'s mtime, and a copy with a fresh
mtime would silently invalidate a cache the tests rely on being warm.

## Usage

    from tests._tmpstore import isolate_store
    isolate_store()
    from app.services import run_service   # import AFTER, so paths resolve late

Called at the top of a creating suite, before anything reads ``settings``.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_ISOLATED: Path | None = None


def isolate_store() -> Path:
    """Point ``settings.data_dir`` at a temp copy of the store. Idempotent.

    Returns the temp directory. It is registered for removal at interpreter
    exit; a failure to remove it is ignored, because leaving a temp directory
    behind is strictly better than failing a suite during teardown.
    """
    global _ISOLATED
    if _ISOLATED is not None:
        return _ISOLATED

    from app.config import settings

    source = settings.data_dir
    target = Path(tempfile.mkdtemp(prefix="hsk-test-store-"))
    shutil.copytree(source, target, dirs_exist_ok=True)
    settings.data_dir = target
    _ISOLATED = target
    atexit.register(shutil.rmtree, target, ignore_errors=True)
    return target


def store_is_isolated() -> bool:
    """True when the store has been redirected. For a suite's own assertions."""
    return _ISOLATED is not None
