"""Tests for the published HSK/HT scenarios (brief task 9).

These guard two specific failures. First, that an invented scenario gets presented
as a published plan -- so every figure is checked against the CEDD source and the
assumptions are required to be declared. Second, that a capped result reads as a
complete one: for the Second Phase the district does not have enough non-paved
land, and the shortfall must be impossible to miss.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.sandbox_model import (  # noqa: E402
    apply_published_scenario,
    load_grids,
    load_published_scenarios,
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
    published = load_published_scenarios()
    check("the rasters load", grids is not None)
    check("the published scenario file loads", published is not None)
    if grids is None or published is None:
        print("\n0/2 checks passed")
        return 1

    src = published["source"]
    check("the source is CEDD's own project website",
          src["publisher"] == "CEDD West Development Office"
          and "hskhtnda.hk" in src["url"], src["url"])
    check("it records when it was retrieved", bool(src.get("retrieved")),
          str(src.get("retrieved")))
    check("three scenarios are encoded", len(published["scenarios"]) == 3,
          f"{len(published['scenarios'])}")
    check("the status claims no more than it has",
          published["status"] == "PUBLISHED_WORKS_AREAS_PROXY_FOR_LAND_USE",
          published["status"])
    check("what is NOT published is declared",
          len(published["notPublished"]) >= 4,
          f"{len(published['notPublished'])} items")

    areas = {s["id"]: s["worksAreaHa"] for s in published["scenarios"]}
    check("First Phase is about 19.6 ha",
          abs(areas["first_phase"] - 19.6) < 0.01, f"{areas['first_phase']}")
    check("Second Phase Stage 2 is about 200 ha",
          abs(areas["second_phase_stage2"] - 200.0) < 0.01,
          f"{areas['second_phase_stage2']}")
    check("all six Second Phase contracts total about 253.5 ha",
          abs(areas["second_phase_all_contracts"] - 253.5) < 0.01,
          f"{areas['second_phase_all_contracts']}")
    contracts = published["scenarios"][2]["components"]
    check("and the total is the sum of its six published components",
          abs(sum(c["areaHa"] for c in contracts) - 253.5) < 0.01,
          f"{len(contracts)} components")

    for sc in published["scenarios"]:
        check(f"{sc['id']} declares its land-use assumption",
              bool(sc.get("assumptionNote")) and bool(sc.get("assumedConversion")),
              str(sc.get("assumedConversion")))

    results = [apply_published_scenario(grids, sc) for sc in published["scenarios"]]
    check("all three run", all("error" not in r for r in results), "")

    for r in results:
        check(f"{r['scenarioId']} cites where its area came from",
              bool(r.get("source")), str(r.get("source"))[:42])
        check(f"{r['scenarioId']} forming land worsens the deficit",
              r["deltaUnstoredM3"] > 0 and r["worsens"],
              f"{r['deltaUnstoredM3']:+,.0f} m3")

    first, stage2 = results[0], results[1]
    check("a bigger programme is a bigger effect",
          stage2["deltaUnstoredM3"] > first["deltaUnstoredM3"],
          f"{first['deltaUnstoredM3']:+,.0f} -> {stage2['deltaUnstoredM3']:+,.0f}")
    check("and the effect is monotonic across all three",
          results[0]["deltaUnstoredM3"] <= results[1]["deltaUnstoredM3"]
          <= results[2]["deltaUnstoredM3"], "")

    check("the Second Phase cannot fit the land available, and says so",
          stage2["areaShortfallHa"] > 0,
          f"{stage2['areaShortfallHa']} ha short of {stage2['worksAreaHa']} ha")
    check("the shortfall is named in the note",
          "areaShortfallHa" in stage2["note"], "")
    check("bare ground runs out first, so later phases come from green",
          results[1]["cellsConverted"]["bare"] == first["cellsConverted"]["bare"],
          f"bare {first['cellsConverted']['bare']:,} cells in both")
    check("no scenario converts more than the area allows",
          all(r["cellsConvertedTotal"] <= r["cellsWanted"] for r in results), "")

    zero = apply_published_scenario(grids, {"id": "x", "worksAreaHa": 0})
    check("a scenario with no published area is refused",
          "error" in zero, str(zero.get("error")))

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
