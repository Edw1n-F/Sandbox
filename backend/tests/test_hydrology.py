"""Tests for the drainage routing: depressions, fill-and-spill, mass balance, HAND.

The cascade is the one place in this project where a wrong number is silent. A
mass balance that leaks by a little looks exactly like one that balances, and a
depression capacity with the sign inverted either never ponds or ponds
everywhere -- both plausible on a map. So these tests pin the identities and the
signs on synthetic DEMs whose answer is known by hand, then repeat the identities
on the real grid.

Two index spaces meet in this module and the tests respect both: ``condition()``
returns a receiver reshaped to the grid, while ``d8_flow_direction()`` and the
artefact on disk are flat (``row * cols + col``). The cascade consumes the flat
form, so the synthetic cases build it with ``d8_flow_direction`` directly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import numpy as np  # noqa: E402

from hydrology import (  # noqa: E402
    HAND_DRAINAGE_MIN_ACCUM_CELLS,
    d8_flow_direction,
    fill_and_spill,
    flow_accumulation,
    hand,
    label_depressions,
    mass_balance,
    priority_flood,
)
from app.services.sandbox_model import load_grids  # noqa: E402

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
    # --- label_depressions on a two-pit DEM --- #
    #
    # The eastern half is higher ground, so the two pits have DIFFERENT rims and
    # therefore different spill elevations. A spill read from the pit bottom
    # instead of the rim would report 5 and 8; the correct escapes are 10 and 20.
    raw = np.full((20, 20), 10.0)
    raw[:, 10:] = 20.0
    raw[5, 5] = 5.0        # pit A, rim 10 -> depth 5
    raw[15, 15] = 8.0      # pit B, rim 20 -> depth 12
    filled = priority_flood(raw)
    dep = label_depressions(filled, raw)
    check("a two-pit DEM gives exactly two depressions",
          dep["count"] == 2, f"{dep['count']}")
    check("depressionCells is the integer total, one per pit",
          dep["depressionCells"] == 2, f"{dep['depressionCells']}")
    spills = sorted(dep["spillElevationM"].values())
    check("each spill is the lowest escape on the rim, not the pit bottom",
          abs(spills[0] - 10.0) < 1e-6 and abs(spills[1] - 20.0) < 1e-6, str(spills))
    depths = sorted(dep["depthM"].values())
    # Priority-Flood adds its documented epsilon (1e-5 m) to every filled cell, so
    # the depth is (rim + epsilon) - bottom: 5.00001 and 12.00001, not 5 and 12.
    check("the depth is max(filled - raw) per component",
          abs(depths[0] - 5.0) < 1e-4 and abs(depths[1] - 12.0) < 1e-4, str(depths))
    check("labels are contiguous 0..count",
          sorted(set(dep["labels"].ravel().tolist())) == [0, 1, 2], "")

    # --- fill_and_spill: a flat basin stores, a slope discharges --- #
    basin_raw = np.full((20, 20), 10.0)
    basin_raw[1:-1, 1:-1] = 0.0
    basin_filled = priority_flood(basin_raw)
    basin_recv = d8_flow_direction(basin_filled)
    # Runoff only on the interior, so the discharging rim carries none: the
    # basin's own water then has nowhere to go but storage.
    basin_runoff = np.zeros((20, 20))
    basin_runoff[1:-1, 1:-1] = 0.1
    basin = fill_and_spill(basin_filled, basin_raw, basin_recv, basin_runoff, 1.0)
    check("a flat basin stores nearly all its water",
          basin["storedM3"] > 0.99 * basin["runoffInM3"],
          f"stored {basin['storedM3']:.3f} of {basin['runoffInM3']:.3f} m3")
    check("and discharges essentially nothing",
          basin["dischargedM3"] < 1e-6, f"{basin['dischargedM3']:.2e} m3")
    check("with the books balanced",
          basin["massBalance"]["balanced"] is True, "")

    slope = np.tile((100.0 - np.arange(20.0))[:, None], (1, 20))
    slope_filled = priority_flood(slope)
    slope_recv = d8_flow_direction(slope_filled)
    slope_runoff = np.full((20, 20), 1.0)
    slope_res = fill_and_spill(slope_filled, slope, slope_recv, slope_runoff, 1.0)
    check("a slope has no depression capacity and stores nothing",
          slope_res["storedM3"] == 0.0, f"{slope_res['storedM3']}")
    check("so nearly all of it discharges",
          slope_res["dischargedM3"] > 0.999 * slope_res["runoffInM3"],
          f"{slope_res['dischargedM3']:.1f} of {slope_res['runoffInM3']:.1f} m3")
    again = fill_and_spill(slope_filled, slope, slope_recv, slope_runoff, 1.0)
    check("the cascade is deterministic across two runs",
          again["massBalance"] == slope_res["massBalance"]
          and np.array_equal(again["depthField"], slope_res["depthField"]), "")

    # --- mass_balance raises on a leak --- #
    try:
        mass_balance(100.0, 10.0, 50.0)
        check("a leaked mass balance raises AssertionError", False, "it returned")
    except AssertionError as exc:
        check("a leaked mass balance raises AssertionError",
              "leaked" in str(exc), str(exc)[:60])
    ok_block = mass_balance(100.0, 10.0, 90.0)
    check("a balanced one returns the residual block",
          ok_block["balanced"] is True and abs(ok_block["residualM3"]) < 1e-9, "")

    # --- HAND --- #
    slope_acc = flow_accumulation(slope_filled, slope_recv)
    slope_hand = hand(slope_filled, slope_recv, slope_acc,
                      drainage_min_accum_cells=20.0)
    hm = slope_hand["handM"]
    check("HAND is zero on every drainage cell",
          bool((hm[slope_acc >= 20.0] == 0.0).all()), "")
    col = hm[:, 10]
    check("HAND falls monotonically down a synthetic slope",
          all(a >= b - 1e-9 for a, b in zip(col, col[1:])),
          " > ".join(f"{v:.0f}" for v in col[::4]))
    check("HAND is never negative", float(hm.min()) >= 0.0, f"{hm.min()}")
    check("the HAND output carries the cross-check label",
          slope_hand["status"] == "SCREENING_HAND_CROSSCHECK_NOT_A_FLOOD_PREDICTION"
          and "Not a flood prediction" in slope_hand["warning"], "")
    check("and echoes the accumulation threshold it used",
          slope_hand["drainageThresholdCells"] == 20.0, "")

    # --- the real grid --- #
    grids = load_grids()
    check("the rasters load", grids is not None)
    if grids is not None and grids.receiver is not None:
        real_dep = label_depressions(grids.elevation_5m, grids.raw_elevation_5m)
        check("the real grid has the reported 37 depressions",
              real_dep["count"] == 37, f"{real_dep['count']}")
        check("spanning the reported 32,500 depression cells",
              real_dep["depressionCells"] == 32500, f"{real_dep['depressionCells']}")

        real_hand = hand(grids.elevation_5m, grids.receiver, grids.accumulation)
        check("HAND marks the reported 4,607 drainage cells",
              real_hand["drainageCells"] == 4607, f"{real_hand['drainageCells']}")
        check("HAND is zero on every drainage cell and non-negative everywhere",
              bool((real_hand["handM"][
                  grids.accumulation >= HAND_DRAINAGE_MIN_ACCUM_CELLS] == 0.0).all())
              and float(real_hand["handM"].min()) >= 0.0, "")

        # Every zero-HAND cell that is not drainage is a grid outlet (receiver
        # -1), seeded to zero so the cross-check overlay is defined everywhere.
        # There are no other zeros: a non-outlet, non-drainage cell always has a
        # reference below it.
        outlets = grids.receiver.reshape(grids.rows, grids.cols) < 0
        drainage = grids.accumulation >= HAND_DRAINAGE_MIN_ACCUM_CELLS
        zero_non_drainage = (real_hand["handM"] == 0.0) & ~drainage
        check("every non-drainage zero-HAND cell is a grid outlet",
              bool((zero_non_drainage & ~outlets).sum() == 0),
              f"{int(zero_non_drainage.sum())} zeros, "
              f"{int((zero_non_drainage & outlets).sum())} of them outlets")
    else:
        check("the routing artefacts exist for the real-grid checks", False,
              "run scripts/make_wetgrid.py first")

    # --- determinism: no wall-clock, no RNG, and no land class in the routing --- #
    #
    # HAND must be an INDEPENDENT cross-check of the routed depth. Deriving its
    # drainage network from the water/wetland land class would restate the
    # land-cover grid and cross-check nothing, so the module never touches land
    # cover at all.
    src = (BACKEND_DIR / "scripts" / "hydrology.py").read_text()
    check("hydrology.py imports neither time nor random",
          not re.search(r"^\s*(import|from)\s+(time|random)\b", src, re.M), "")
    check("HAND's drainage network is flow accumulation, not the land class",
          "accum >=" in src and "land_class" not in src and "LAND_CLASSES" not in src,
          "the module never references land cover")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
