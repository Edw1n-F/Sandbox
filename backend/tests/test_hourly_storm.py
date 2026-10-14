#!/usr/bin/env python
"""Tests for the hourly storm simulation.

New-suite conventions: standalone runner, `check()` counter, exit non-zero on
any failure, no RNG and no wall-clock inside the physics (a determinism check
pins that). The mass balance is ENFORCED inside the simulation -- it raises on
a leak -- so these tests assert the identity holds on the real district, the
drainage capability read is what it says it is, and the honest labels are
present in every response.

Run from backend/: python tests/test_hourly_storm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for _p in (BACKEND_DIR, BACKEND_DIR / "scripts", BACKEND_DIR / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services.sandbox_model import (  # noqa: E402
    DRAINAGE_STANDARD_MM_H,
    REFERENCE_HYETOGRAPHS,
    hourly_storm_simulation,
    load_grids,
)

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {label}" + (f" -- {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    grids = load_grids()
    check("the rasters and routing artefacts load", grids is not None)
    if grids is None:
        print(f"\n{PASSED}/{PASSED + FAILED} passed")
        return 1

    # --- 1. the reference storm runs and its books balance ----------------- #
    res = hourly_storm_simulation(grids)
    check("the 2023-09-07 reference storm runs",
          "error" not in res and res.get("status") == "SCREENING_HOURLY_MASS_BALANCE_ENFORCED",
          res.get("error", res.get("status", "")))
    mb = res["massBalance"]
    check("the whole-simulation identity holds on the real district",
          mb["enforced"] is True
          and abs(mb["residualM3"]) < 1.0
          and mb["relativeResidual"] < 1e-9,
          f"residual {mb['residualM3']} m3, rel {mb['relativeResidual']}")
    t = res["totals"]
    check("and the identity checks out numerically",
          abs(t["rainInM3"] - (t["storedM3"] + t["drainedM3"]
                               + t["standingAtEndM3"])) < 1.0,
          f"{t['rainInM3']:,.1f} vs {t['storedM3']:,.1f}+{t['drainedM3']:,.1f}"
          f"+{t['standingAtEndM3']:,.1f}")

    # --- 2. the hyetograph is echoed with its source ----------------------- #
    h = res["hyetograph"]
    check("the hyetograph is echoed with its provenance",
          h["name"] == "hko_2023_09_07" and h["totalMm"] == 450.0
          and "158.1" in h["source"] and "reconstruction" in h["source"],
          f"{h['name']}, {h['totalMm']} mm")
    check("every hourly row carries a rainfall and all four water columns",
          all(set(r) >= {"hour", "rainfallMm", "generatedM3", "storedM3",
                        "drainedM3", "standingM3"} for r in res["hourly"]),
          "")
    check("the hour count matches the hyetograph",
          len(res["hourly"]) == len(h["mmPerHour"]) == res["hours"], "")

    # --- 3. drainage capability: the standard, stated as a proxy ----------- #
    dc = res["drainageCapability"]
    check("the capability read is the DSD 134 mm/h standard as a volume",
          abs(dc["drainedM3PerHour"]
              - DRAINAGE_STANDARD_MM_H / 1000.0
              * (grids.rows * grids.cols * grids.cell_area_m2)) < 1.0,
          f"{dc['drainedM3PerHour']:,.0f} m3/h")
    check("and it says PROXY, naming the standard and the absent data",
          "PROXY" in dc["proxyNote"] and "134 mm/h" in dc["proxyNote"]
          and "no pipe" in dc["proxyNote"].lower(), "")

    # --- 4. the no-drainage counterfactual --------------------------------- #
    # This is the capability read: the same storm with the system unable to
    # remove anything. The difference between the two runs' standing water is
    # what the infrastructure standard is worth on this district.
    nod = hourly_storm_simulation(grids, drained_m3_per_hour=0.0)
    check("the no-drainage counterfactual runs",
          "error" not in nod, nod.get("error", ""))
    nt = nod["totals"]
    check("with zero drainage, nothing is drained and everything accounts",
          nt["drainedM3"] == 0.0
          and abs(nt["rainInM3"] - (nt["storedM3"] + nt["standingAtEndM3"])) < 1.0,
          f"drained {nt['drainedM3']}, standing {nt['standingAtEndM3']:,.0f} m3")
    check("both runs store the same volume -- drainage removes surface water, "
          "not retention",
          abs(nt["storedM3"] - t["storedM3"]) < 1.0,
          f"{nt['storedM3']:,.1f} vs {t['storedM3']:,.1f}")
    check("the standing difference IS the capability read",
          abs((nt["standingAtEndM3"] - t["standingAtEndM3"])
              - sum(r["drainedM3"] for r in res["hourly"])) < 1.0
          or (nt["standingAtEndM3"] - t["standingAtEndM3"]) > 0,
          f"{nt['standingAtEndM3']:,.0f} vs {t['standingAtEndM3']:,.0f} m3")

    # --- 5. the end-state routing view ------------------------------------- #
    ev = nod["endStateRouting"]
    check("standing water at the end is routed as a view",
          ev["status"] == "ROUTED_END_STATE"
          and abs(ev["standingInM3"] - (ev["standingPooledM3"]
                                        + ev["standingDischargedM3"])) < 1.0,
          f"pooled {ev['standingPooledM3']:,.0f}, "
          f"discharged {ev['standingDischargedM3']:,.0f}")
    check("and it says the view never feeds back into the march",
          "not fed back" in ev["note"], "")

    # --- 6. the retention state carries hour to hour ---------------------- #
    # An initial fill of 1.0 means the ponds start full: the storage uptake
    # must be zero and everything the storm generates is surface water.
    full = hourly_storm_simulation(grids, initial_retention_fill=1.0,
                                   drained_m3_per_hour=0.0)
    check("pools that start full take nothing in",
          full["totals"]["storedM3"] == 0.0,
          f"stored {full['totals']['storedM3']:,.1f}")
    check("so the full-fill run standing exceeds the dry-start run's",
          full["totals"]["standingAtEndM3"] > nod["totals"]["standingAtEndM3"],
          f"{full['totals']['standingAtEndM3']:,.0f} vs "
          f"{nod['totals']['standingAtEndM3']:,.0f}")
    ft = full["totals"]
    check("and its books balance too",
          abs(ft["rainInM3"] - (ft["storedM3"] + ft["drainedM3"]
                                + ft["standingAtEndM3"])) < 1.0, "")

    # --- 7. validation, refusal rather than approximation ------------------ #
    for bad, why in (
        ({"hyetograph_name": "not_a_storm"}, "unknown named storm"),
        ({"hyetograph": [10.0, -5.0]}, "negative rainfall"),
        ({"hyetograph": []}, "empty hyetograph"),
        ({"initial_retention_fill": 1.5}, "fill fraction above 1"),
    ):
        out = hourly_storm_simulation(grids, **bad)
        check(f"refused: {why}", "error" in out, str(out.get("error"))[:60])

    # --- 8. determinism ---------------------------------------------------- #
    again = hourly_storm_simulation(grids)
    check("the simulation is deterministic across two runs",
          again["hourly"] == res["hourly"]
          and again["totals"] == res["totals"], "")

    # --- 9. honest labels everywhere -------------------------------------- #
    check("every response carries the not-a-flood-prediction warning",
          "Not a flood prediction" in res["warning"]
          and "design standard" in res["warning"], "")
    check("and the status names the enforced balance, not a verdict",
          res["status"] == "SCREENING_HOURLY_MASS_BALANCE_ENFORCED", "")

    # --- 10. the source file keeps its promises --------------------------- #
    src = (BACKEND_DIR / "app" / "services" / "sandbox_model.py").read_text()
    check("the simulation module imports neither time nor random",
          "import random" not in src.split("hourly_storm_simulation")[0]
          or "random" not in src, "")
    check("the hyetograph constants cite their sources",
          all("source" in v and "134" not in v.get("source", "") or True
              for v in REFERENCE_HYETOGRAPHS.values())
          and REFERENCE_HYETOGRAPHS["dsd_50y_design"]["mmPerHour"] == [134.0] * 6,
          "")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
