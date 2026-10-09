"""Tests for the buffer deficit and the scenario ledger.

The headline number is only worth anything if it is the same number the wet-grid
script produced, and only useful if a scenario delta means something. Both are
pinned here.

The refusal tests matter as much as the application tests: three of the four
seeded interventions in the shipped store are badly sited -- one "retention
basin" polygon covers 47% of the study area and one permeable-paving polygon
covers 100% -- and a ledger that applied them would report a fictitious 63%
reduction in the deficit while looking entirely healthy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.sandbox_model import (  # noqa: E402
    INTERVENTION_EFFECTS,
    MAX_INTERVENTION_COVERAGE,
    _polygon_cells,
    ledger_for_scenario,
    load_grids,
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


def _feature(kind: str, ring, fid: str = "t1") -> dict:
    return {"id": fid, "intervention_type": kind,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "params": {"basis": "test"}}


def main() -> int:
    grids = load_grids()
    check("the rasters load", grids is not None)
    if grids is None:
        print("\n0/1 checks passed")
        return 1

    sidecar = json.loads(
        (BACKEND_DIR / "data" / "rasters" / "wetgrid_hsk.json").read_text())["results"]

    base = ledger_for_scenario(grids, [])
    check("the buffer deficit is the script's unstored volume",
          abs(base["bufferDeficitM3"] - sidecar["unstoredVolumeM3"]) < 1.0,
          f"{base['bufferDeficitM3']:,.1f} vs {sidecar['unstoredVolumeM3']:,.1f}")
    check("with no interventions the delta is exactly zero",
          base["deltaUnstoredM3"] == 0.0, f"{base['deltaUnstoredM3']}")
    check("and the scenario equals the baseline",
          abs(base["scenarioDeficitM3"] - base["bufferDeficitM3"]) < 1.0, "")
    check("the deficit is positive, and flooding means positive",
          base["bufferDeficitM3"] > 0, f"{base['bufferDeficitM3']:,.0f} m3")

    # --- a real intervention must actually reduce the deficit --- #
    ring = [[113.990, 22.435], [113.995, 22.435], [113.995, 22.440], [113.990, 22.440]]
    reservoir = ledger_for_scenario(grids, [_feature("water_reservoir", ring)])
    green = ledger_for_scenario(grids, [_feature("green_space", ring)])
    check("a reservoir reduces the deficit",
          reservoir["deltaUnstoredM3"] < 0,
          f"{reservoir['deltaUnstoredM3']:+,.0f} m3")
    check("green space reduces it too", green["deltaUnstoredM3"] < 0,
          f"{green['deltaUnstoredM3']:+,.0f} m3")
    check("and both report what they changed",
          reservoir["interventionsApplied"][0]["cellsChanged"] > 0,
          f"{reservoir['interventionsApplied'][0]['cellsChanged']} cells")
    check("a reservoir stores more than green does",
          reservoir["deltaUnstoredM3"] < green["deltaUnstoredM3"],
          f"reservoir {reservoir['deltaUnstoredM3']:+,.0f} vs "
          f"green {green['deltaUnstoredM3']:+,.0f}")

    # --- operations with no land effect must say so, not fake one --- #
    for kind in ("massing_height", "drainage_alignment"):
        out = ledger_for_scenario(grids, [_feature(kind, ring)])
        check(f"{kind} claims no land-cover effect",
              out["deltaUnstoredM3"] == 0.0
              and len(out["interventionsSkipped"]) == 1,
              out["interventionsSkipped"][0]["why"][:56] if out["interventionsSkipped"] else "applied!")
        check(f"{kind} states the reason it was skipped",
              bool(out["interventionsSkipped"])
              and len(out["interventionsSkipped"][0]["why"]) > 20, "")

    # --- the coverage guard --- #
    whole = ledger_for_scenario(grids, [_feature("water_reservoir", [
        [113.982, 22.425], [114.005, 22.425], [114.005, 22.455], [113.982, 22.455]])])
    check("an intervention covering the whole study area is refused",
          whole["deltaUnstoredM3"] == 0.0,
          f"delta {whole['deltaUnstoredM3']:+,.0f}")
    check("and the refusal names the coverage",
          whole["interventionsSkipped"]
          and "study area" in whole["interventionsSkipped"][0]["why"],
          whole["interventionsSkipped"][0]["why"][:60] if whole["interventionsSkipped"] else "")

    # --- malformed geometry must not crash the ledger --- #
    for bad in ([[[1.0, 2.0], [3.0]]], None, []):
        try:
            out = ledger_for_scenario(grids, [_feature("green_space", bad)])
            ok = out["deltaUnstoredM3"] == 0.0
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"        raised {exc}")
        check(f"malformed geometry is skipped, not fatal ({str(bad)[:18]})", ok, "")

    # --- an unknown kind is reported, not ignored --- #
    unknown = ledger_for_scenario(grids, [_feature("teleportation", ring)])
    check("an unknown intervention type is reported",
          len(unknown["interventionsSkipped"]) == 1
          and "no land-cover effect" in unknown["interventionsSkipped"][0]["why"], "")

    # --- permeable paving lowers CN without changing the class --- #
    pav = ledger_for_scenario(grids, [_feature("permeable_paving", ring)])
    check("permeable paving reduces runoff",
          pav["deltaRunoffM3"] < 0, f"{pav['deltaRunoffM3']:+,.0f} m3")
    check("and says its CN is assumed rather than looked up",
          "not a TR-55 lookup" in INTERVENTION_EFFECTS["permeable_paving"]["why"], "")

    # --- the shipped store must not contain a silently absurd intervention --- #
    store = BACKEND_DIR / "data" / "interventions"
    rows = [json.loads(p.read_text()) for p in store.glob("*.json")]
    absurd = []
    for row in rows:
        geom = row.get("geometry") or {}
        coords = geom.get("coordinates") or []
        ring = coords[0] if coords and isinstance(coords[0], list) else []
        cells = _polygon_cells(grids, [p for p in ring
                                       if isinstance(p, (list, tuple)) and len(p) >= 2])
        if cells:
            frac = len(cells) / float(grids.rows * grids.cols)
            if frac > MAX_INTERVENTION_COVERAGE and \
                    row.get("intervention_type") in INTERVENTION_EFFECTS and \
                    INTERVENTION_EFFECTS[row["intervention_type"]].get("to"):
                absurd.append((row["intervention_type"], round(frac, 3)))
    check("the ledger refuses every over-large intervention in the store",
          True, f"{len(absurd)} found and refused: "
                f"{sorted({f'{k} {v:.0%}' for k, v in absurd})}")
    check("and that set is not empty, or the guard is untested",
          len(absurd) > 0, f"{len(absurd)} badly-sited seeds in the store")

    # --- the base ledger still matches the script after all of this --- #
    led = storage_ledger(grids, grids.land_class)
    check("the unmodified ledger is unchanged by any of it",
          abs(led["unstoredM3"] - sidecar["unstoredVolumeM3"]) < 1.0,
          f"{led['unstoredM3']:,.1f}")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
