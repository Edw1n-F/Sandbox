"""Tests for the sea-level scenarios (brief task 21).

The headline here is a *negative* result: at HSK/HT none of the official Hong
Kong projections for 2100 changes the storage deficit, because the ponds are
landward. That is worth pinning, because the brief expected this scenario to be
the most memorable part of the demo and a plausible-looking large number would
have been the easiest wrong thing to produce.

So these tests check two things: that the projections are the real published
figures with a citation, and that the mechanism is real even though the answer
is currently zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.sandbox_model import (  # noqa: E402
    SEA_LEVEL_PROJECTIONS,
    load_grids,
    sea_level_report,
    sea_level_scenario,
    storage_ledger,
)

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {label}{f' -- {detail}' if detail else ''}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}{f' -- {detail}' if detail else ''}")


def main() -> int:
    grids = load_grids()
    check("the rasters load", grids is not None)
    if grids is None:
        print("\n0/1 checks passed")
        return 1

    # --- the figures are the published ones --- #
    official = {k: v["metres"] for k, v in SEA_LEVEL_PROJECTIONS.items()
                if k.startswith("ssp")}
    check("the HKO SSP2-4.5 range is 0.37-0.82 m",
          official["ssp245_low"] == 0.37 and official["ssp245_high"] == 0.82,
          f"{official['ssp245_low']}-{official['ssp245_high']}")
    check("the HKO SSP5-8.5 range is 0.57-1.08 m",
          official["ssp585_low"] == 0.57 and official["ssp585_high"] == 1.08,
          f"{official['ssp585_low']}-{official['ssp585_high']}")
    check("every projection cites Hong Kong Observatory",
          all("Observatory" in v["source"] for v in SEA_LEVEL_PROJECTIONS.values()
              if v["source"].startswith("brief") is False),
          "")
    check("the brief's own +0.4 and +0.8 m are kept and attributed",
          SEA_LEVEL_PROJECTIONS["brief_0.4"]["metres"] == 0.40
          and SEA_LEVEL_PROJECTIONS["brief_0.8"]["metres"] == 0.80
          and "brief" in SEA_LEVEL_PROJECTIONS["brief_0.4"]["source"], "")
    check("all six scenarios are present",
          len(SEA_LEVEL_PROJECTIONS) == 6, f"{len(SEA_LEVEL_PROJECTIONS)}")

    # --- the mechanism must be real, even where the answer is zero --- #
    report = sea_level_report(grids)
    check("every projection reports a delta of zero at this site",
          all(p["deltaUnstoredM3"] == 0.0 for p in report["projections"]),
          f"max {max(p['deltaUnstoredM3'] for p in report['projections']):,.0f} m3")
    check("and none submerges a single retention cell",
          all(p["retentionCellsSubmerged"] == 0 for p in report["projections"]),
          f"{min(p['retentionCellsSubmerged'] for p in report['projections'])}")
    check("which is explained rather than left as a mystery",
          "landward" in report["finding"] and "jacking" in report["finding"], "")
    check("the datum caveat is attached",
          "mCD" in report["caveat"] and "0.146" in report["caveat"], "")

    # If the answer is zero only because the mechanism is broken, a high enough
    # sea must still bite. This is the test that distinguishes "correctly zero"
    # from "silently inert".
    absurd = sea_level_scenario(grids, 12.0)
    check("the mechanism bites when the sea is high enough",
          absurd["retentionCellsSubmerged"] > 1000
          and absurd["deltaUnstoredM3"] > 0,
          f"{absurd['retentionCellsSubmerged']:,} cells, "
          f"{absurd['deltaUnstoredM3']:+,.0f} m3")
    check("lost retention raises the deficit one-for-one",
          abs(absurd["deltaUnstoredM3"] - absurd["lostRetentionM3"]) < 1.0,
          f"lost {absurd['lostRetentionM3']:,.0f} vs delta "
          f"{absurd['deltaUnstoredM3']:,.0f}")

    # --- monotonic in sea level: a higher sea is never better --- #
    ladder = [sea_level_scenario(grids, h)["deltaUnstoredM3"]
              for h in (2.0, 4.0, 6.0, 8.0, 10.0)]
    check("the deficit never improves as the sea rises",
          all(a <= b for a, b in zip(ladder, ladder[1:])),
          " < ".join(f"{v:,.0f}" for v in ladder))

    # --- a zero or negative sea level changes nothing at all --- #
    check("a non-positive sea level is a no-op",
          sea_level_scenario(grids, 0.0) == {}, "not a scenario")
    check("and today's deficit is unchanged by asking for zero",
          abs(storage_ledger(grids, grids.land_class)["unstoredM3"]
              - report["bufferDeficitM3"]) < 1.0, "")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
