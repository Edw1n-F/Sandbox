#!/usr/bin/env python
"""Fetch the heat-field anchor band from the HKO Lau Fau Shan station record.

    python scripts/fetch_heat_anchor.py            # fetch, verify, write the anchor
    python scripts/fetch_heat_anchor.py --check    # recompute and assert the file still agrees

The heat field is a *relative* 0-1 index and is never a temperature. But an index
with no stated reference is read against nothing -- the response says
``anchorSource: "none"`` and a viewer has no way to judge whether 0.8 is hot.
This script supplies the missing reference: a **cited air-temperature band** the
index is read against.

**Source: Hong Kong Observatory open data** (``data.weather.gov.hk``), the
**Lau Fau Shan (LFS) daily station record**, data types ``CLMTEMP`` (daily mean),
``CLMMAXT`` (daily maximum) and ``CLMMINT`` (daily minimum). Free, no key, the
station the project's own action plan names for temperature. Chosen over the
alternatives for the reasons the design records:

- **Air, not skin.** Every skin-LST source (Landsat 8/9 C2 L2 ST, MODIS MOD11,
  ECOSTRESS) is credential-gated and was unreachable from this host; a probe of
  ``landsatlook.usgs.gov`` returns the EROS login HTML with HTTP 200. Skin runs
  10-20 C above air on a hot paved surface; the two quantities are never
  conflated, so nothing here claims LST.
- **The HKO 1991-2020 normals are a cross-check, not the anchor.** They are a
  different product (a 30-year normal at HKO Headquarters). Labelling this
  station record ``"hko_normals"`` would name the wrong product, so the anchor's
  source enum is ``"hko_station"``.

**Verified before any use.** The band is asserted against control points (C1-C7
below) so a wrong column, a broken date filter or a reversed band fails loudly
instead of shipping a plausible-looking wrong number. The one failure this file
exists to avoid is a mean biased by the HKO missing-value sentinel ``"***"``,
which is **dropped and counted**, never coerced to 0 (which would cool the band)
and never to NaN (which would poison the mean).

**Never runs at request time.** The output is committed and read by
``load_heat_calibration()``; nothing in ``app/`` imports this module or opens a
socket. ``--check`` is the drift gate: it recomputes the band and refuses to pass
if the committed file no longer agrees, so a hand-edit cannot survive.

Output: ``backend/data/reference/heat_anchor.json`` (atomic write).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

API = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
USER_AGENT = "Hack4SDG-T1/1.0 (heat anchor fetch; contact: team)"

# The three datasets, in the order they are fetched. ``dataTypes`` is written
# into the artefact from this same constant, so the provenance cannot drift from
# the requests that produced it.
DATASETS: tuple[str, ...] = ("CLMTEMP", "CLMMAXT", "CLMMINT")
MEAN, MAX, MIN = "CLMTEMP", "CLMMAXT", "CLMMINT"

# The HKO missing-value sentinel. Observed live in the record (the PM saw
# ``["2024","1","14","***",""]``); it must never be read as a number.
SENTINEL = "***"

# Control points (design C1-C7). Wide enough to survive a minor HKO revision,
# tight enough that a wrong column lands outside them.
MIN_TOTAL_ROWS = 10_000
VALID_DAYS_LOW, VALID_DAYS_HIGH = 280, 310   # 10 Julys x 31 days
MEAN_MIN_BAND = (25.5, 27.5)
MEAN_MAX_BAND = (31.5, 33.5)
MEAN_TEMP_BAND = (28.0, 30.0)
CROSS_CHECK_TOLERANCE_C = 3.0

CACHE_DIR = settings.data_dir / "heat_cache"

# The site centroid, used only by the non-fatal ERA5 cross-check.
SITE_LAT, SITE_LON = 22.44, 113.994


def build_url(data_type: str, station: str) -> str:
    """The one endpoint, one ``dataType`` per call."""
    return f"{API}?{urllib.parse.urlencode({'dataType': data_type, 'lang': 'en', 'rformat': 'json', 'station': station})}"


def _fetch_json(url: str) -> dict:
    """Fetch one URL as JSON, cached on disk so re-runs are free and resumable.

    The ``fetch_dem.py`` pattern, trimmed to three calls: a ``User-Agent``
    header, honour ``Retry-After`` on HTTP 429/5xx, else exponential backoff
    with jitter. The cache is keyed on the URL itself, so a different station or
    window misses the cache rather than serving the wrong record.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    last_error: Exception | None = None
    for attempt in range(8):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload: {str(payload)[:200]}")
            cache_file.write_text(json.dumps(payload))
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if (retry_after or "").isdigit() else 0.0
            if wait <= 0:
                wait = min(2.0**attempt, 30.0) + random.uniform(0, 2.0)
            print(f"[anchor]   HTTP {exc.code}, backing off {wait:.1f}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            wait = min(2.0**attempt, 30.0) + random.uniform(0, 2.0)
            print(f"[anchor]   {exc}, retrying in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"HKO open data failed after retries: {last_error}")


def parse_window(payload: dict, month: int, start_year: int, end_year: int
                 ) -> tuple[list[float], dict]:
    """Rows in the year/month window -> the numeric values, plus counts.

    Pure and offline so it can be unit-tested without a network call. Each row
    is ``[year, month, day, value, completeness]``.

    1. Keep a row only if ``start_year <= year <= end_year`` and ``month`` matches.
    2. If ``value`` is the sentinel ``"***"`` -- or empty, or anything that does
       not parse as a float -- **drop the row and count it**. Never ``0.0`` (the
       mean would read cooler than the record) and never ``NaN`` (the mean would
       be poisoned). The dropped count is returned so a sudden jump in missing
       days is visible rather than silent.
    3. Otherwise parse to float and keep it.

    Returns ``(values, stats)`` with ``stats = {"rows", "valid", "dropped"}``.
    """
    values: list[float] = []
    rows = 0
    dropped = 0
    for row in payload.get("data") or []:
        try:
            year = int(row[0])
            mon = int(row[1])
        except (ValueError, TypeError, IndexError):
            continue
        if not (start_year <= year <= end_year and mon == month):
            continue
        rows += 1
        raw = row[3] if len(row) > 3 else None
        if raw is None or raw == SENTINEL:
            dropped += 1
            continue
        try:
            values.append(float(raw))
        except (ValueError, TypeError):
            dropped += 1
    return values, {"rows": rows, "valid": len(values), "dropped": dropped}


def _mean(values: list[float]) -> float:
    if not values:
        raise RuntimeError("no values in the window -- refusing to write a band")
    return statistics.fmean(values)


def compute_core(station: str, month: int, start_year: int, end_year: int
                 ) -> dict:
    """Fetch the three datasets and compute the band, checking C1-C7 as it goes.

    Every control prints its observed value on success and raises with the
    observed value on failure -- the ``fetch_dem.py`` "verified against known
    truth before any use" rule. A wrong parse must crash, not ship.
    """
    means: dict[str, float] = {}
    extremes: dict[str, float] = {}
    stats_by_type: dict[str, dict] = {}
    for data_type in DATASETS:
        payload = _fetch_json(build_url(data_type, station))
        rows = payload.get("data") or []
        if len(rows) < MIN_TOTAL_ROWS:
            raise RuntimeError(
                f"{data_type} returned {len(rows)} rows, expected >= "
                f"{MIN_TOTAL_ROWS} -- truncated or error payload; refusing to write."
            )
        values, stats = parse_window(payload, month, start_year, end_year)
        if not (VALID_DAYS_LOW <= stats["valid"] <= VALID_DAYS_HIGH):
            raise RuntimeError(
                f"{data_type} has {stats['valid']} valid days in the window, "
                f"expected {VALID_DAYS_LOW}..{VALID_DAYS_HIGH} -- broken date "
                "filter or a parser that silently drops rows; refusing to write."
            )
        means[data_type] = _mean(values)
        extremes[data_type] = (max(values) if data_type == MAX else min(values))
        stats_by_type[data_type] = stats
        print(f"[anchor] {data_type}  n={stats['valid']}  "
              f"(window rows {stats['rows']}, dropped {stats['dropped']} sentinel)  "
              f"mean {means[data_type]:.4f} C")
        time.sleep(2.0)  # a free public API; three calls, paced

    mean_min, mean_max, mean_temp = means[MIN], means[MAX], means[MEAN]
    for label, value, (low, high) in (
        ("mean_daily_min", mean_min, MEAN_MIN_BAND),
        ("mean_daily_max", mean_max, MEAN_MAX_BAND),
        ("mean_temperature", mean_temp, MEAN_TEMP_BAND),
    ):
        if not low <= value <= high:
            raise RuntimeError(
                f"control {label} = {value:.2f} C is outside {low}..{high} -- a "
                "wrong column was parsed; refusing to write."
            )
        print(f"[anchor] control {label:<16} {value:6.2f} C  OK ({low}..{high})")
    if not mean_min < mean_temp < mean_max:
        raise RuntimeError(
            f"min < mean < max violated ({mean_min:.2f}, {mean_temp:.2f}, "
            f"{mean_max:.2f}) -- a min/max column mix-up; refusing to write."
        )
    range_c = [round(mean_min, 1), round(mean_max, 1)]
    if not range_c[0] < range_c[1]:
        raise RuntimeError(f"reversed band {range_c}; refusing to write.")

    return {
        "rangeC": range_c,
        "quantity": "HKO_air",
        "mapsTo": "range",
        "values": {
            "meanDailyMinC": round(mean_min, 1),
            "meanC": round(mean_temp, 2),
            "meanDailyMaxC": round(mean_max, 1),
            "hottestDailyMaxC": round(extremes[MAX], 1),
            "coolestDailyMinC": round(extremes[MIN], 1),
        },
        "stats": stats_by_type,
        "mean_daily_max": mean_max,
    }


def cross_check_era5(month: int, start_year: int, end_year: int,
                     mean_daily_max: float) -> None:
    """Non-fatal independent check against the Open-Meteo ERA5 archive.

    Never fatal on a network failure: the committed artefact must not depend on
    a second network source. When it runs, it asserts the reanalysis July
    mean-of-daily-maxima is within ``CROSS_CHECK_TOLERANCE_C`` of the station
    band maximum -- the gap the PM observed was 1.7 C.
    """
    last_day = 31
    url = (
        "https://archive-api.open-meteo.com/v1/archive?"
        + urllib.parse.urlencode({
            "latitude": SITE_LAT,
            "longitude": SITE_LON,
            "start_date": f"{start_year}-{month:02d}-01",
            "end_date": f"{end_year}-{month:02d}-{last_day}",
            "daily": "temperature_2m_max",
            "timezone": "Asia/Hong_Kong",
        })
    )
    try:
        payload = _fetch_json(url)
        times = payload["daily"]["time"]
        highs = payload["daily"]["temperature_2m_max"]
        july = [v for t, v in zip(times, highs)
                if t[5:7] == f"{month:02d}" and v is not None]
        era5_mean = statistics.fmean(july)
    except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError,
            ValueError) as exc:  # noqa: BLE001 -- a cross-check must not block
        print(f"[anchor] cross-check ERA5 unavailable ({exc}); continuing")
        return
    diff = abs(era5_mean - mean_daily_max)
    verdict = "OK" if diff <= CROSS_CHECK_TOLERANCE_C else "OUT OF BAND"
    print(f"[anchor] cross-check ERA5 mean-of-daily-max {era5_mean:.2f} C  "
          f"|diff| {diff:.2f} <= {CROSS_CHECK_TOLERANCE_C}  {verdict}")
    if diff > CROSS_CHECK_TOLERANCE_C:
        print("[anchor] WARNING: ERA5 disagrees with the station band by more "
              "than the tolerance -- check the station record before trusting it.")


def build_document(core: dict, station: str, month: int, start_year: int,
                   end_year: int, retrieved: str) -> dict:
    """Assemble the committed JSON: the deterministic core + provenance.

    The cross-checks are recorded as static provenance from the retrieval run;
    they are never the anchor and the committed artefact must not depend on a
    second network source.
    """
    month_name = date(2000, month, 1).strftime("%B")
    return {
        "artefact": "heat_anchor",
        "generated": retrieved,
        "status": "PUBLISHED_STATION_RECORD_NOT_A_MEASUREMENT_OF_THIS_SITE",
        "quantity": core["quantity"],
        "rangeC": core["rangeC"],
        "mapsTo": core["mapsTo"],
        "mapsToSemantics": (
            f"index 0.0 -> {core['rangeC'][0]} C (a typical {month_name} night: "
            f"the mean daily minimum); index 1.0 -> {core['rangeC'][1]} C (a "
            f"typical {month_name} afternoon: the mean daily maximum). A display "
            "band the relative index is read against, not a site measurement."
        ),
        "source": {
            "name": "Hong Kong Observatory open data - Lau Fau Shan (LFS) daily "
                    "air-temperature record",
            "publisher": "Hong Kong Observatory",
            "station": station,
            "stationName": "Lau Fau Shan",
            "url": API,
            "dataTypes": list(DATASETS),
            "queryExample": build_url(MIN, station),
            "licence": "Hong Kong Observatory open data (GovHK Terms of Use); "
                       "free, no key",
            "retrieved": retrieved,
            "window": f"{month_name} {start_year}-{end_year}",
            "stationDistanceFromSiteKm": 20,
            "values": core["values"],
            "method": (
                f"Mean of the daily minimum and daily maximum air temperature "
                f"over {month_name} of {start_year}-{end_year}, from the LFS "
                "daily station record. Missing values use the HKO sentinel "
                "'***' and are excluded, never coerced to 0. "
                "rangeC = [mean(daily min), mean(daily max)]."
            ),
        },
        "crossChecks": [
            {
                "name": "HKO 1991-2020 climate normals (HKO Headquarters)",
                "url": "https://www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm",
                "julyMeanDailyMinC": 26.9,
                "julyMeanC": 28.9,
                "julyMeanDailyMaxC": 31.6,
                "note": "A different product (a 30-year normal at HKO HQ), used "
                        "only to show the LFS July band is ordinary for Hong "
                        "Kong. NOT the anchor.",
            },
            {
                "name": "Open-Meteo ERA5 archive (reanalysis) at the site centroid",
                "url": "https://archive-api.open-meteo.com/v1/archive",
                "latitude": SITE_LAT,
                "longitude": SITE_LON,
                "julyMeanOfDailyMaxC": 30.99,
                "julyHottestDailyMaxC": 36.0,
                "note": "An independent reanalysis cross-check, within 1.8 C of "
                        "the LFS band maximum. NOT the anchor.",
            },
        ],
        "whatThisIsNot": [
            "This is a stated air-temperature band for a Hong Kong summer, NOT a "
            "measurement of the Hung Shui Kiu / Ha Tsuen site. The LFS station is "
            "~20 km away and this is a 10-year July climatology, not the site's "
            "air temperature on any day.",
            "This is AIR temperature, not skin (surface) temperature. "
            "Landsat/MODIS/ECOSTRESS LST runs 10-20 C above air on a hot paved "
            "surface and was unreachable (credential-gated) from this host. The "
            "two quantities are never conflated.",
            "The heat field stays a relative 0-1 index. This band is what the "
            "index is READ AGAINST; the field is never converted to degrees "
            "Celsius.",
            "The HEAT_INDEX land-class table remains hand-authored and unverified; "
            "the anchor does not change it.",
            "No comfort index (UTCI/PET/SET) is claimed.",
        ],
    }


def _atomic_write(path: Path, document: dict) -> None:
    """Write to a temp file, then rename, so an interrupted run cannot leave a
    half-file that ``load_heat_calibration()`` would then read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def run_check(path: Path, core: dict) -> int:
    """Recompute and assert the committed file still agrees (the drift gate)."""
    if not path.exists():
        print(f"[anchor] --check: {path} does not exist")
        return 1
    committed = json.loads(path.read_text())
    mismatches: list[str] = []
    for key in ("quantity", "rangeC", "mapsTo"):
        if committed.get(key) != core[key]:
            mismatches.append(f"{key}: file={committed.get(key)!r} "
                              f"computed={core[key]!r}")
    file_values = (committed.get("source") or {}).get("values") or {}
    for key, value in core["values"].items():
        if file_values.get(key) != value:
            mismatches.append(f"source.values.{key}: file={file_values.get(key)!r} "
                              f"computed={value!r}")
    if mismatches:
        print("[anchor] --check FAILED: the committed file has drifted from the "
              "fetcher:")
        for line in mismatches:
            print(f"[anchor]   {line}")
        return 1
    print(f"[anchor] --check OK: {path} reproduces "
          f"rangeC={core['rangeC']} quantity={core['quantity']} "
          f"values={core['values']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="LFS", help="HKO station code")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--month", type=int, default=7)
    parser.add_argument("--retrieved", default=date.today().isoformat(),
                        help="provenance date (default: today)")
    parser.add_argument("--out", default=None,
                        help="output JSON (default data/reference/heat_anchor.json)")
    parser.add_argument("--check", action="store_true",
                        help="recompute and assert the committed file still agrees")
    parser.add_argument("--no-cross-check", dest="cross_check", action="store_false",
                        help="skip the non-fatal ERA5 cross-check")
    args = parser.parse_args()

    out = Path(args.out) if args.out else settings.data_dir / "reference" / "heat_anchor.json"
    core = compute_core(args.station, args.month, args.start_year, args.end_year)

    if args.check:
        return run_check(out, core)

    if args.cross_check:
        cross_check_era5(args.month, args.start_year, args.end_year,
                         core["mean_daily_max"])

    document = build_document(core, args.station, args.month, args.start_year,
                              args.end_year, args.retrieved)
    _atomic_write(out, document)
    print(f"[anchor] wrote {out}  rangeC={core['rangeC']} "
          f"quantity={core['quantity']}")
    print("[anchor] RELATIVE INDEX band only - not a measurement of this site")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
