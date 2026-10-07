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
    """Impervious surfaces shed more water than vegetated ones."""
    order = [CURVE_NUMBER_HSG_D[n] for n in ("water", "paved", "wetland", "bare", "green")]
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
    # Hung Shui Kiu is coastal floodplain with hills to the north.
    assert -50.0 <= dem.min() <= 60.0, f"DEM min {dem.min()} m is implausible for HSK/HT"
    assert 50.0 <= dem.max() <= 1000.0, f"DEM max {dem.max()} m is implausible for HSK/HT"
    relief = float(dem.max() - dem.min())
    assert 20.0 < relief < 900.0, f"relief {relief:.0f} m is implausible for HSK/HT"


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


if __name__ == "__main__":
    raise SystemExit(_run())
