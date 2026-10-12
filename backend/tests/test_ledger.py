"""Tests for the buffer deficit and the scenario ledger.

The headline number is only worth anything if it is the same number the wet-grid
script produced, and only useful if a scenario delta means something. Both are
pinned here.

The refusal tests matter as much as the application tests: a ledger that applied
a site-covering polygon would report a large fictitious reduction in the deficit
while looking entirely healthy. Those absurd geometries are built **here**, in
code, rather than read out of the store -- `data/interventions/` and
`data/plans/` also collect residue from hand-poking the API, and a guard tested
by whatever happens to be lying around is not a guard.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from app.services.sandbox_model import (  # noqa: E402
    DEFAULT_RAINFALL_MM,
    INTERVENTION_EFFECTS,
    LAND_CLASSES,
    MAX_INTERVENTION_COVERAGE,
    _polygon_cells,
    _runoff_and_unstored,
    ledger_for_scenario,
    load_grids,
    routed_ledger,
    sea_level_report,
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


def _shipped_plan_files() -> list[Path]:
    """The plan documents git actually tracks, newest-first order irrelevant.

    `data/plans/` is a runtime directory: it holds the two seeded plans AND
    whatever a hand poke with the API wrote there, which `.gitignore` excludes.
    A check about the repo's *shipped* fixtures has to name that distinction
    explicitly or it ends up asserting something about this machine's store.
    When git cannot answer (no repo, no git binary) the function returns an
    empty list and the caller's `bool(shipped)` guard reports a failure rather
    than a silent pass.
    """
    plans = sorted((BACKEND_DIR / "data" / "plans").glob("*.json"))
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--", "data/plans/*.json"],
            cwd=BACKEND_DIR, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    tracked = {(BACKEND_DIR / p).resolve() for p in listed}
    return [p for p in plans if p.resolve() in tracked]


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

    # --- the over-large-intervention guard must actually fire --- #
    # This was previously asserted by scanning data/interventions/ for badly
    # sited seeds and requiring the set to be non-empty. That coupled the guard
    # to leftover test residue: the assertion passed precisely BECAUSE stale
    # documents were sitting in the store, so cleaning the store up silently
    # turned a real check into a failing one, and leaving the residue in was the
    # only way to keep it green. A guard tested by garbage is not a guard.
    #
    # The absurd geometries are now built here instead, so the check is a
    # property of the code rather than of whatever the store happens to hold.
    site_area = float(grids.rows * grids.cols)
    min_lon, min_lat, max_lon, max_lat = grids.bounds
    whole_area = [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]
    coverage = len(_polygon_cells(grids, whole_area)) / site_area
    check("the site-covering fixture really does cover the site",
          coverage > 0.95, f"{coverage:.1%} of {grids.rows}x{grids.cols} cells")
    for kind in ("flood_storage", "green_space"):
        out = ledger_for_scenario(grids, [_feature(kind, whole_area)])
        skipped = out["interventionsSkipped"]
        # Both of these rewrite the land class, so both are refusable; the point
        # is that a site-covering polygon of either kind is refused outright
        # rather than applied as a district-scale land-cover swap.
        check(f"a site-covering {kind} is refused, not applied",
              bool(skipped) and coverage > MAX_INTERVENTION_COVERAGE,
              f"covers {coverage:.0%} of the site, "
              f"{'skipped' if skipped else 'APPLIED -- guard did not fire'}")

    # A ledger handed nothing must move not at all -- the guard is not a filter
    # that silently drops legitimate work.
    check("the over-large guard does not fire on a normal polygon",
          not ledger_for_scenario(grids, [_feature("green_space", ring)])
          ["interventionsSkipped"],
          f"the {len(_polygon_cells(grids, ring))}-cell ring was applied")

    # --- and no *shipped* seed is itself over-large --- #
    #
    # "Shipped" is not the same as "on disk". `data/plans/` also holds residue:
    # documents a hand poke with the API left behind, which .gitignore keeps out
    # of the repo on purpose. A residue plan makes a residue intervention "live",
    # so a check that globs the directory fails for the *store* rather than for
    # the code -- the exact coupling this file stopped making above. Git is the
    # definition of shipped, so ask it who is actually in the repo.
    store = BACKEND_DIR / "data" / "interventions"
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in store.glob("*.json")]
    shipped = _shipped_plan_files()
    live_ids = set()
    for plan_path in shipped:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for item in plan.get("interventions") or []:
            live_ids.add(item.get("id"))
    absurd_live = []
    for row in rows:
        if row.get("id") not in live_ids:
            continue
        coords = (row.get("geometry") or {}).get("coordinates") or []
        ring_i = coords[0] if coords and isinstance(coords[0], list) else []
        cells = _polygon_cells(grids, [p for p in ring_i
                                       if isinstance(p, (list, tuple)) and len(p) >= 2])
        if not cells:
            continue
        frac = len(cells) / site_area
        if frac > MAX_INTERVENTION_COVERAGE and \
                row.get("intervention_type") in INTERVENTION_EFFECTS and \
                INTERVENTION_EFFECTS[row["intervention_type"]].get("to"):
            absurd_live.append((row["intervention_type"], round(frac, 3)))
    check("no shipped seed is itself over-large",
          bool(shipped) and not absurd_live,
          f"{len(rows)} documents on disk, {len(shipped)} shipment(s) in git, "
          f"{len(live_ids)} live id(s), {len(absurd_live)} over-large")

    # --- the base ledger still matches the script after all of this --- #
    led = storage_ledger(grids, grids.land_class)
    check("the unmodified ledger is unchanged by any of it",
          abs(led["unstoredM3"] - sidecar["unstoredVolumeM3"]) < 1.0,
          f"{led['unstoredM3']:,.1f}")

    # --- Stage 6: the routed cascade, beside the level pool --- #
    #
    # The cascade consumes the per-cell unstored volume storage_ledger already
    # sums, so its runoffInM3 must be the published headline rather than a
    # re-derived one. Two identities are asserted, and the second is the one that
    # catches an ADR-015 double count hiding inside a balanced cascade.
    routed = routed_ledger(grids, grids.land_class)
    check("the routed ledger is available",
          routed.get("status") == "SCREENING_ROUTED_NOT_A_FLOOD_PREDICTION",
          str(routed.get("status")))
    mb = routed["massBalance"]
    check("the routed cascade balances to relative tolerance",
          mb["balanced"] is True and mb["relativeResidual"] < mb["tol"],
          f"residual {mb['residualM3']:+.2e} m3, relative {mb['relativeResidual']:.2e}")
    check("runoff_in equals stored plus discharged exactly",
          abs(mb["runoffInM3"] - (mb["storedM3"] + mb["dischargedM3"])) < 1e-6,
          f"{mb['runoffInM3']:.4f} vs {mb['storedM3'] + mb['dischargedM3']:.4f}")
    check("routed runoffInM3 is the published unstored volume, not a re-derivation",
          abs(routed["runoffInM3"] - sidecar["unstoredVolumeM3"]) < 1.0,
          f"{routed['runoffInM3']:,.1f} vs {sidecar['unstoredVolumeM3']:,.1f}")

    gen_m, unstored_m = _runoff_and_unstored(grids.land_class, DEFAULT_RAINFALL_MM)
    area = grids.cell_area_m2
    generation = float(gen_m.sum() * area)
    local_retention = float((gen_m - unstored_m).sum() * area)
    runoff_in = float(unstored_m.sum() * area)
    check("generation equals local retention plus runoff_in",
          abs(generation - (local_retention + runoff_in))
          <= 1e-9 * max(abs(generation), 1.0),
          f"{generation:,.3f} = {local_retention:,.3f} + {runoff_in:,.3f}")
    check("and a retention class generates no runoff of its own (ADR-015)",
          bool((gen_m[grids.land_class == LAND_CLASSES.index("water")] == 0.0).all()),
          "water cells generate nothing, so they are never counted twice")

    wetter = routed_ledger(grids, grids.land_class, DEFAULT_RAINFALL_MM * 1.5)
    check("more rainfall raises the routed runoff",
          wetter["runoffInM3"] > routed["runoffInM3"],
          f"{wetter['runoffInM3']:,.0f} > {routed['runoffInM3']:,.0f}")

    check("the routed depth is reported beside HAND, never merged into it",
          "handVsRouted" in routed
          and "never merged" in routed["handVsRouted"]["note"], "")
    check("the routed output carries the not-a-flood-prediction labels",
          routed["status"] == "SCREENING_ROUTED_NOT_A_FLOOD_PREDICTION"
          and "Not a flood prediction" in routed["warning"]
          and "Not engineering-grade" in routed["warning"], "")

    stripped = replace(grids, receiver=None, raw_elevation_5m=None)
    unavailable = routed_ledger(stripped, stripped.land_class)
    check("an absent receiver reports ROUTING_UNAVAILABLE, not a crash",
          unavailable["status"] == "ROUTING_UNAVAILABLE"
          and "reason" in unavailable and "warning" in unavailable, "")

    # --- regression pins: these are the numbers that must not move --- #
    #
    # wetgrid_hsk.npy is the level-pool depth field the cascade sits BESIDE. If a
    # refactor moved it, every published deficit would move with it silently.
    wet_md5 = hashlib.md5(
        (BACKEND_DIR / "data" / "rasters" / "wetgrid_hsk.npy").read_bytes()).hexdigest()
    check("the wet-grid depth artefact is byte-identical",
          wet_md5 == "d7cc052a45af4a3f6aac59773568b2ee", wet_md5)
    accum_md5 = hashlib.md5(
        (BACKEND_DIR / "data" / "rasters" / "flow_accum_hsk.npy").read_bytes()).hexdigest()
    check("the flow-accumulation artefact is unchanged",
          accum_md5 == "4f9c4689bb0f6ef802c8b4dab836deec", accum_md5)

    # The _runoff_and_unstored() refactor and the elevation_5m semantics change
    # (it now holds the Priority-Flood FILLED surface, where it previously held
    # the raw one) must not have moved the sea-level report's headline.
    sea = sea_level_report(grids)
    check("the sea-level ground range is still the physical range",
          2.0 <= sea["groundElevationM"]["min"] <= 2.5
          and abs(sea["groundElevationM"]["max"] - 141.0) < 1.0,
          str(sea["groundElevationM"]))
    check("and the sea-level headline deficit is unchanged",
          abs(sea["bufferDeficitM3"] - sidecar["unstoredVolumeM3"]) < 1.0,
          f"{sea['bufferDeficitM3']:,.1f}")
    check("no official projection moves the deficit at this site",
          all(p["deltaUnstoredM3"] == 0.0 for p in sea["projections"]), "")

    print(f"\n{PASSED}/{PASSED + FAILED} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
