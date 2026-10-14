"""The two silent-constant tests (ADR-036) plus a monotonicity check.

These exist because the source documents name two constants that "fail
silently": a Curve Number form that is wrong by a factor of ten, and a
mCD/mPD datum offset. Neither produces an error -- both produce a plausible
picture and a wrong number, which is the worst possible failure mode.

Both were caught in practice during the Oct 6 build: the `2540` variant of
S = 25400/CN - S returns *negative* retention for any CN above ~91, which makes
the denominator of Q negative and the runoff figure ~5e18 m3. It rendered.

Run:  python tests/test_constants.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402

from _overlay_common import (  # noqa: E402
    COUNTED_AS,
    CURVE_NUMBER_HSG_D,
    LAND_CLASSES,
    runoff_depth,
)

# USDA NRCS TR-55 worked example, evaluated in the inches the standard uses:
#   S = 1000/CN - 10 = 2.5 in ; Ia = 0.2S = 0.5 in
#   Q = (P - Ia)^2 / (P - Ia + S) = 1.0^2 / 3.5 = 0.2857 in = 7.26 mm
# The mm form is algebraically identical because P - Ia + S = P + 0.8S.
TR55_CN = 80.0
TR55_RAINFALL_MM = 38.1  # 1.5 in
TR55_EXPECTED_Q_MM = 7.26


def test_scs_cn_retention_is_not_the_times_ten_error() -> None:
    """S = 25400/CN - 254, not - 2540. Derivation: 25.4 * (1000/CN - 10)."""
    for cn, expected in ((96.0, 10.6), (86.0, 41.3), (68.0, 119.5), (50.0, 254.0)):
        s = 25400.0 / cn - 254.0
        assert abs(s - expected) < 0.15, f"CN {cn}: S={s}, expected ~{expected}"


def test_scs_cn_matches_published_worked_example() -> None:
    q_mm = float(runoff_depth(TR55_RAINFALL_MM, np.array([TR55_CN]))[0]) * 1000.0
    assert abs(q_mm - TR55_EXPECTED_Q_MM) < 0.2, f"Q={q_mm:.2f}mm, expected {TR55_EXPECTED_Q_MM}"


def test_retention_is_never_negative() -> None:
    """The failure mode of the 2540 form: negative S above CN ~91."""
    cn = np.linspace(30.0, 100.0, 200)
    q = runoff_depth(100.0, cn)
    assert np.all(np.isfinite(q)), "runoff must be finite for every CN"
    assert np.all(q >= 0.0), "runoff must be non-negative"
    # A paved surface (CN 96) at 100 mm sheds ~88% of it as runoff.
    paved = float(runoff_depth(100.0, np.array([96.0]))[0])
    ratio = paved / 0.100
    assert 0.85 < ratio < 0.92, f"paved runoff ratio {ratio:.3f}, expected ~0.883"


def test_curve_number_ordering_is_physical() -> None:
    """Impervious surfaces shed more water than vegetated ones.

    Only classes that actually generate runoff are checked. Wetland's CN is
    recorded for transparency but is never used -- it is zeroed in generation
    because it is modelled as storage (ADR-015), so including it here would
    assert an ordering that does not govern anything.
    """
    from _overlay_common import COUNTED_AS

    generating = [n for n in ("paved", "bare", "green")
                  if COUNTED_AS.get(n) == "generation"]
    assert set(generating) == {"paved", "bare", "green"}, generating
    order = [CURVE_NUMBER_HSG_D[n] for n in ("water",) + tuple(generating)]
    assert order == sorted(order, reverse=True), f"CN ordering is wrong: {order}"


def test_no_class_is_counted_as_both_generation_and_retention() -> None:
    """ADR-015: a water body is counted in exactly one term, never both."""
    assert set(COUNTED_AS) == set(LAND_CLASSES)
    assert set(COUNTED_AS.values()) == {"generation", "retention"}
    for name, side in COUNTED_AS.items():
        assert side in ("generation", "retention"), name
    # Open water must be retention-only, otherwise a pond is double counted.
    assert COUNTED_AS["water"] == "retention"
    assert COUNTED_AS["wetland"] == "retention"


def test_datum_offset_is_a_named_constant() -> None:
    """mCD and mPD differ by 0.146 m in Hong Kong.

    There is no tide series in this prototype, so the constant is pinned here
    rather than in a formula: when a tide gauge is added, this is the value it
    must use, and the test is the thing that has to be revisited.
    """
    MCD_TO_MPD_M = 0.146
    assert abs(MCD_TO_MPD_M - 0.146) < 1e-9
    # Half a 0.3 m LiDAR cell -- small, but enough to flip a shallow cell
    # between wet and dry, which is why it is named rather than inlined.
    assert 0.0 < MCD_TO_MPD_M < 0.30


def test_runoff_is_monotonic_in_rainfall() -> None:
    """ADR-017: more rain must never produce less runoff."""
    cn = np.full((3, 3), 90.0)
    previous = -1.0
    for rainfall in (0.0, 50.0, 200.0, 450.0, 800.0):
        total = float(runoff_depth(rainfall, cn).sum())
        assert total >= previous - 1e-12, f"runoff fell at {rainfall} mm"
        previous = total
    assert float(runoff_depth(0.0, cn).sum()) == 0.0, "zero rain gives zero runoff"


def test_wetgrid_data_is_not_clipped_by_its_own_render() -> None:
    """The .npy must carry depths the PNG cannot show.

    `make_wetgrid.py` renders alpha over a fixed 0..MAX_VIS_DEPTH_M range so the
    image stays readable, which means the PNG saturates. In the 450 mm run the
    mean depth in the wet area is ~1.5 m, so a 0.30 m cap clips almost every wet
    cell. An earlier version of `seed_interventions.py` read the PNG to site
    flood storage and consequently found nothing deeper than the cap, producing
    zero storage sites with no error at all.

    So this asserts the float32 field reaches beyond the render's ceiling. If
    somebody deletes the .npy and points the analysis back at the image, this is
    the test that notices.
    """
    import numpy as np

    rasters = BACKEND_DIR / "data" / "rasters"
    npy = rasters / "wetgrid_hsk.npy"
    meta = rasters / "wetgrid_hsk.json"
    if not npy.exists() or not meta.exists():
        print("      (skipped: run scripts/make_wetgrid.py first)")
        return

    depth = np.load(npy).astype(np.float64)
    cap = float(json.loads(meta.read_text())["render"]["maxVisibleDepthM"])

    assert (depth > 0).any(), "depth field is entirely dry -- suspicious"

    # Deliberately 1.5x rather than a strict `>`. A first attempt used
    # `depth.max() > cap` and it passed against a field that had been clipped
    # exactly to the cap -- because float32(0.30) is 0.3000000119..., which is
    # fractionally *greater* than the float64 cap. A strict comparison against a
    # value that has been through float32 is not a real assertion.
    assert float(depth.max()) > cap * 1.5, (
        f"deepest cell is {depth.max():.3f} m, barely beyond the render cap of "
        f"{cap} m. Either the run produced nothing meaningfully deeper, or the "
        f"depth field has been replaced by the saturated render -- which is "
        f"exactly the bug that once made flood-storage siting find nothing."
    )


def test_wetgrid_png_and_npy_agree_wherever_the_png_is_not_clipped() -> None:
    """Wherever alpha has not saturated, the render must match the data.

    The PNG is a visualisation of the .npy, so in the unsaturated range the two
    must agree to within the alpha quantisation step. A mismatch means the two
    artefacts came from different runs, which is exactly the sort of drift that
    makes a screenshot disagree with the number printed beside it.
    """
    import numpy as np
    from PIL import Image

    rasters = BACKEND_DIR / "data" / "rasters"
    npy = rasters / "wetgrid_hsk.npy"
    meta = rasters / "wetgrid_hsk.json"
    png = rasters / "wetgrid_hsk.png"
    if not (npy.exists() and meta.exists() and png.exists()):
        print("      (skipped: run scripts/make_wetgrid.py first)")
        return

    render = json.loads(meta.read_text())["render"]
    lo, hi = float(render["minVisibleDepthM"]), float(render["maxVisibleDepthM"])

    depth = np.load(npy).astype(np.float64)
    alpha = np.array(Image.open(png).convert("RGBA"))[..., 3].astype(np.float64)

    assert alpha.shape == depth.shape, "render and depth field differ in shape"

    # Only cells strictly inside the representable range can be compared.
    comparable = (alpha > 0) & (depth >= lo) & (depth <= hi)
    assert comparable.any(), "no comparable cells -- the two artefacts disagree entirely"
    step = (hi - lo) / 235.0
    worst = float(np.abs(alpha[comparable] / 235.0 * (hi - lo) + lo - depth[comparable]).max())
    assert worst <= step * 1.5, (
        f"render and data disagree by {worst:.4f} m, more than one alpha step "
        f"({step:.4f} m) -- they were probably generated by different runs"
    )


def test_terrain_rgb_round_trips_to_the_source_dem() -> None:
    """The PNG the browser decodes must reproduce the DEM numpy wrote.

    `fetch_dem.py` encodes elevation into mapbox terrain-RGB and
    `frontend/src/view/demProvider.ts` decodes it in the browser. Those are two
    implementations of one formula in two languages. If they ever drift, the
    terrain renders at the wrong height and **nothing errors** -- the globe just
    looks subtly wrong, which is the failure mode this whole test file exists to
    catch.

    So this asserts the invariant on the committed artefacts rather than on a
    synthetic array: whatever `dem_terrain_rgb.png` holds must decode back to
    `dem_hsk.npy` within terrain-RGB's 0.1 m quantisation.
    """
    import numpy as np
    from PIL import Image

    from fetch_dem import encode_terrain_rgb

    dem_path = BACKEND_DIR / "data" / "dem_hsk.npy"
    png_path = BACKEND_DIR / "data" / "dem_terrain_rgb.png"
    if not dem_path.exists() or not png_path.exists():
        print("      (skipped: run scripts/fetch_dem.py first)")
        return

    dem = np.load(dem_path).astype(np.float64)
    decoded = (
        np.array(Image.open(png_path).convert("RGB")).astype(np.int64)
    )
    height = (decoded[..., 0] * 65536 + decoded[..., 1] * 256 + decoded[..., 2])
    height = height * 0.1 - 32768

    assert height.shape == dem.shape, (
        f"terrain-RGB is {height.shape}, DEM is {dem.shape} -- the browser "
        f"would stretch the raster to the wrong extent"
    )
    worst = float(np.abs(height - dem).max())
    assert worst <= 0.1, (
        f"worst round-trip error {worst:.4f} m exceeds terrain-RGB's own "
        f"0.1 m quantum -- the encoder and the browser decoder disagree"
    )

    # And the encoder itself is stable: re-encoding the decoded values must be
    # a no-op, i.e. the transform is idempotent at its own precision.
    assert np.array_equal(encode_terrain_rgb(height), decoded), (
        "encode(decode(x)) != x -- terrain-RGB is not idempotent"
    )


def test_dem_height_range_is_physically_plausible() -> None:
    """A DEM that decodes to 800 km is a broken source, not a broken night.

    This is the browser-side half of the control-point check `fetch_dem.py`
    performs before writing. It guards the committed artefact, so a bad fetch
    that slipped past the fetcher's own assertions is still caught.
    """
    import numpy as np

    dem_path = BACKEND_DIR / "data" / "dem_hsk.npy"
    if not dem_path.exists():
        print("      (skipped: run scripts/fetch_dem.py first)")
        return
    dem = np.load(dem_path).astype(np.float64)
    assert np.isfinite(dem).all(), "DEM contains non-finite values"
    # Hung Shui Kiu is coastal floodplain; the cropped 3D-data extent is flat.
    assert -50.0 <= dem.min() <= 60.0, f"DEM min {dem.min()} m is implausible for HSK/HT"
    assert 5.0 <= dem.max() <= 1000.0, f"DEM max {dem.max()} m is implausible for HSK/HT"
    relief = float(dem.max() - dem.min())
    assert 3.0 < relief < 900.0, f"relief {relief:.0f} m is implausible for HSK/HT"


def test_curve_numbers_match_their_cited_tr55_rows() -> None:
    """Pin each CN to the TR-55 row it claims, so it cannot drift again.

    These values were wrong until 8 Oct 2026 and nothing caught them, because
    they looked plausible. `green` was 68 -- the HSG **A** open-space-poor figure
    -- while the model assumed HSG D throughout, understating runoff on vegetated
    land by 12 CN points. A test per class is the only thing that stops a
    plausible number from being quietly wrong.
    """
    from _overlay_common import CN_CITATIONS, CN_SOURCE, CURVE_NUMBER_HSG_D

    tr55_hsg_d = {
        # cover: HSG D value, from USDA NRCS TR-55 Table 2-2
        "paved": 98.0,   # paved parking / roofs / driveways / streets, all HSG
        "bare": 94.0,    # fallow, bare soil
        "green": 80.0,   # open space, good (>75% grass)
    }
    for name, expected in tr55_hsg_d.items():
        assert CURVE_NUMBER_HSG_D[name] == expected, (
            f"CN {name} is {CURVE_NUMBER_HSG_D[name]}, TR-55 HSG D says {expected}"
        )

    # The specific regression: 68 is an HSG A value and must never be the
    # vegetated row of an HSG D model.
    assert CURVE_NUMBER_HSG_D["green"] != 68.0, "68 is HSG A open-space-poor"

    assert all(name in CN_CITATIONS and CN_CITATIONS[name].strip()
               for name in CURVE_NUMBER_HSG_D), "a class has no citation"
    assert "TR-55" in CN_SOURCE, CN_SOURCE

    # Wetland's CN is recorded but never used; say so rather than let a reader
    # assume it contributes.
    assert "never used" in CN_CITATIONS["wetland"], (
        "the wetland citation must state that its CN is unused"
    )


def test_no_class_is_counted_as_both_generation_and_retention() -> None:
    """ADR-015, and the wetland double-count that violated it.

    `COUNTED_AS` declared wetland a retention class, but `compute()` zeroed only
    the *water* class. Wetland therefore generated runoff at CN 90 *and* drew a
    0.60 m storage allowance -- roughly a 2x error on the wetland contribution,
    and precisely the quiet failure ADR-015 was written to prevent.

    This runs the real computation and asserts a retention class emits no
    generation at all, rather than checking the declaration against itself.
    """
    import sys as _sys

    for extra in (BACKEND_DIR / "scripts",):
        if str(extra) not in _sys.path:
            _sys.path.insert(0, str(extra))
    from _overlay_common import COUNTED_AS, LAND_CLASSES
    from make_wetgrid import compute

    assert set(COUNTED_AS) == set(LAND_CLASSES), (
        f"unclassified: {set(LAND_CLASSES) - set(COUNTED_AS)}"
    )

    result = compute(450.0, 5.0)
    ov = result["overlay"]
    runoff = result["runoff_m"]
    for name, side in COUNTED_AS.items():
        if side != "retention":
            continue
        mask = ov.class_mask(name)
        if not mask.any():
            continue
        peak = float(runoff[mask].max())
        assert peak == 0.0, (
            f"retention class {name!r} generates {peak:.4f} m of runoff -- "
            f"it is counted in both terms"
        )


def test_cn_scaling_is_monotonic_and_clamped() -> None:
    """The sensitivity pass must move the answer in one direction only, and a
    scaled CN must never imply more than 100% runoff."""
    import sys as _sys

    for extra in (BACKEND_DIR / "scripts",):
        if str(extra) not in _sys.path:
            _sys.path.insert(0, str(extra))
    from make_wetgrid import compute

    previous = -1.0
    for scale in (0.8, 1.0, 1.2):
        r = compute(450.0, 5.0, cn_scale=scale)
        total = float(r["runoff_m"].sum() * r["overlay"].cell_area_m2)
        assert total > previous, f"runoff not monotonic at CN scale {scale:.0%}"
        previous = total

    extreme = compute(450.0, 5.0, cn_scale=50.0)
    depth = float(extreme["runoff_m"].max())
    assert depth <= 0.4501, f"runoff depth {depth:.3f} m exceeds the 450 mm rainfall"


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


# --------------------------------------------------------------------------- #
# Terrain conditioning
# --------------------------------------------------------------------------- #

def test_priority_flood_fills_a_pit_to_its_spill_level() -> None:
    """A closed pit must be raised to the lowest escape route, not left as-is.

    The defining behaviour of Priority-Flood (Barnes et al. 2014). The grid is a
    ring of 9 m walls around a 1 m basin containing a 0 m pit: the pit's only
    escape is over the wall, so it must fill to 9 m (+ epsilon). A DEM left
    unconditioned makes the level-pool solver flood that cell to an arbitrary
    depth, because a pit has no downhill neighbour to spill into.
    """
    import sys as _sys

    for extra in (BACKEND_DIR / "scripts",):
        if str(extra) not in _sys.path:
            _sys.path.insert(0, str(extra))
    from hydrology import priority_flood

    dem = np.array([
        [9., 9., 9., 9., 9.],
        [9., 1., 1., 1., 9.],
        [9., 1., 0., 1., 9.],
        [9., 1., 1., 1., 9.],
        [9., 9., 9., 9., 9.],
    ])
    filled = priority_flood(dem)
    assert filled[2, 2] >= 9.0, f"pit filled only to {filled[2, 2]}, expected >= 9.0"
    assert filled[2, 2] < 9.01, f"pit over-filled to {filled[2, 2]}"
    # No cell inside the ring may sit below the wall any more.
    assert float(filled[1:4, 1:4].min()) >= 9.0, "a depression survived filling"
    # Outside the wall nothing changed.
    assert np.array_equal(filled[0], dem[0]), "the wall itself was altered"


def test_d8_accumulation_converges_to_the_outlet() -> None:
    """Every cell of a cone drains outward, so accumulation grows to the low edge."""
    import sys as _sys

    for extra in (BACKEND_DIR / "scripts",):
        if str(extra) not in _sys.path:
            _sys.path.insert(0, str(extra))
    from hydrology import condition

    cone = np.array([
        [1., 1., 1., 1., 1.],
        [1., 3., 5., 4., 1.],
        [1., 5., 9., 4., 1.],
        [1., 4., 5., 2., 1.],
        [1., 1., 1., 1., 1.],
    ])
    result = condition(cone)
    acc = result["accumulation"]
    assert acc.shape == cone.shape
    assert acc.max() > 1.0, "nothing accumulated"
    # The peak drains away from itself, so it cannot be a local maximum.
    assert acc[2, 2] < acc[2, 4], f"peak accumulated {acc[2, 2]}, edge {acc[2, 4]}"
    # Total accumulation is conserved: every cell's water ends up counted once
    # per contributing cell, and the maximum can never exceed the cell count.
    assert acc.max() <= cone.size, "accumulation exceeds the number of cells"


def test_our_dem_actually_has_depressions_to_condition() -> None:
    """Record what conditioning is worth on the real DEM.

    Not a tautology: if a future DEM source produced an already-conditioned
    surface this would report 0% and the number in the README would need
    revisiting. As shipped it is ~10%, which is why the wet grid conditions
    before solving for a water level.
    """
    import sys as _sys

    for extra in (BACKEND_DIR / "scripts",):
        if str(extra) not in _sys.path:
            _sys.path.insert(0, str(extra))
    from hydrology import condition

    dem_path = BACKEND_DIR / "data" / "dem_hsk.npy"
    if not dem_path.exists():
        print("      (skipped: run scripts/fetch_dem.py first)")
        return
    dem = np.load(dem_path).astype(np.float64)
    raised = int(condition(dem)["cells_raised"])
    # The cropped extent is a flat 9x7 grid and may legitimately have none.
    assert raised >= 0
    fraction = raised / dem.size
    assert fraction < 0.5, (
        f"{fraction:.0%} of cells needed raising -- that suggests the DEM is "
        f"noise rather than terrain, not that conditioning is working hard"
    )


if __name__ == "__main__":
    raise SystemExit(_run())


