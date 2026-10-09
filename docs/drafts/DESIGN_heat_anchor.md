# Design — the heat anchor (Stage 4b), the summer solar day, and the README truth pass

**Owner:** 高见远 (Architect) · **Date:** 2026-10-09 · **Type:** implementation design for one
increment, not greenfield.
**Source of truth:** `docs/drafts/PRD_heat_anchor_and_demo.md` (the PM's decision and acceptance
criteria), `docs/drafts/DESIGN_stages_3_6.md` §1 Q6 / §2.1 / §3.2 / §6, `STATUS.md` Stage 4b and
gaps 8–9 and 11, and the committed code itself.
**Scope of this document:** the design for exactly three things — (1) the anchor, (2) the summer
solar day, (3) the `README.md` truth pass — plus, as the task requires, an ordered backend-scoped
task list for the demo must-haves. It is **design only**: no code, no source edits. The frontend is
owned by a separate process and is never designed here.

**The decision is settled and independently verified** (`quantity: "HKO_air"`, `rangeC: [26.4, 32.7]`,
HKO Lau Fau Shan station, July 2015–2024). This document does **not** re-litigate it; it implements
it and corrects one label in it (§2).

---

## 0. TL;DR

1. **The anchor is a committed file, not a runtime fetch.** `backend/data/reference/heat_anchor.json`
   (literal in §3), reproduced by `backend/scripts/fetch_heat_anchor.py` (§4), read only by
   `load_heat_calibration()`.
2. **One label in the PRD is wrong and is corrected here.** The PRD writes
   `anchorSource: "hko_normals"`. The anchor is the **Lau Fau Shan daily station record**; the HKO
   "normals" product is only a cross-check. The corrected enum value is **`"hko_station"`** (§2).
3. **The field never becomes a temperature.** `HEAT_STATUS` stays `RELATIVE_INDEX_NOT_A_TEMPERATURE`;
   `HEAT_INDEX` stays hand-authored; the anchor changes only the band the 0–1 scale is *read against*.
4. **The December scene is replaced by 15 July (day 196)** — inside the anchor's July window and near
   the annual solar maximum. It lives as a CLI flag with a summer default in `make_heatmap.py` and as
   the `/heat-field` route default, echoed in `solarInputs` (§6).
5. **Four existing test assertions flip** when the file lands; the design names each and its
   replacement, and adds the honesty assertions that must survive (§8).
6. **`README.md` gets a truth pass**: the "state the choice" statement, a Sources row, and the
   correction of the now-false "no flow routing and no sink conditioning" claim (§7).

---

## 1. Context: what already exists, and what is actually missing

The anchoring *mechanism* already shipped in the previous increment. This increment supplies the
**data** and makes the surfaces honest. Concretely, in `backend/app/services/design_metrics.py`:

- `HEAT_CALIBRATION_PATH = "reference/heat_anchor.json"` (design_metrics.py:138).
- `load_heat_calibration()` (design_metrics.py:987) reads `settings.data_dir / HEAT_CALIBRATION_PATH`
  and returns `None` when absent. `settings.data_dir` resolves to `backend/data`, so the file must be
  at `backend/data/reference/heat_anchor.json`.
- `_anchoring_block()` (design_metrics.py:1009) maps `quantity` → `anchorSource` and builds the
  anchored block; the map today is `{"LST_skin": "landsat_lst", "HKO_air": "hko_normals"}`
  (design_metrics.py:1029).
- `heat_field_report()` (design_metrics.py:1046) assembles the served dict; the top-level `status` is
  `HEAT_STATUS` (the field's status), while `anchoring.status` is `HEAT_ANCHOR_STATUS`.
- `make_heatmap.py` already reads the calibration and emits an `anchoring` block into the sidecar
  (make_heatmap.py:99, :178, :191).
- `GET /api/scenes/{id}/heat-field` (scenes.py:867) already serves the anchoring block.

So the missing pieces are: **the committed file**, **the fetcher**, **one corrected enum value**, **one
corrected default**, and **the README**. Nothing about the arithmetic changes.

The `hsk_ava_reference.json` precedent (`backend/data/reference/hsk_ava_reference.json`) fixes the
committed-reference style: a top-level `artefact`, `generated`, `status`, a nested `source` block with
`retrieved`, and a `whatThisIsNot` list. `fetch_dem.py` fixes the fetcher style: cache, pace, verify
against control points before writing, and record provenance. This design follows both.

---

## 2. The corrected label: `anchorSource = "hko_station"` (not `"hko_normals"`)

The PRD's P0-A1 / P1-A1 / P1-A3 write `anchorSource: "hko_normals"`, inherited from
`DESIGN_stages_3_6.md` §3.2, whose enum was `"landsat_lst" | "hko_normals"`. That enum is
**inaccurate for this artefact**:

- The **anchor** is the HKO **Lau Fau Shan (LFS) daily station record** — `CLMTEMP`/`CLMMAXT`/`CLMMINT`,
  `station=LFS` — the station the project's own `Edwin_Action_Plan_2026-10-05.md` names for
  temperature (line 135).
- The **HKO 1991–2020 "normals"** are a *different product* (a 30-year normal, at HKO Headquarters),
  used **only as a cross-check**. They are not the anchor.

Labelling a station record as "normals" is the exact kind of small mislabel this project keeps
eliminating. **Decision: the value is `"hko_station"`.** It is accurate (the source is a station
record), it stays distinct from `"hko_normals"` (the product) and `"landsat_lst"` (skin), and it
keeps the enum's job — naming the *source* — separate from `quantity`, which names the *physical
quantity*.

**The corrected enum, recorded once so code, tests and docs agree:**

| `quantity` | `anchorSource` | Meaning |
|---|---|---|
| `"LST_skin"` | `"landsat_lst"` | Landsat 8/9 surface (skin) temperature — unreachable today; P2 |
| `"HKO_air"` | `"hko_station"` | HKO **station** air-temperature record — **this increment** |
| *(none)* | `"none"` | No calibration on disk |

The enum lives in exactly one place in code — the map in `_anchoring_block()`
(design_metrics.py:1029). `DESIGN_stages_3_6.md` §3.2's comment (`"landsat_lst" | "hko_normals"`) and
§6's warning text ("HKO normals (air)") are corrected to match. The PM should mirror the same one-word
correction in `PRD_heat_anchor_and_demo.md` (P0-A1, P1-A1, P1-A3) — the PRD is the PM's file, so this
design records the correction and flags it rather than editing the PM's document.

**Why not keep `"hko_normals"`?** It would make the code, the tests and the README repeat a label the
artefact's own `source` block contradicts. The whole point of the anchor is provenance; a source label
that names the wrong product defeats it.

---

## 3. Deliverable 1 — `backend/data/reference/heat_anchor.json` (literal, ready to write)

The loader reads only the top-level `quantity` and `rangeC`. Everything else is provenance and the
honest boundary. The nested `source` block (with `retrieved` inside it) follows the
`hsk_ava_reference.json` precedent; this is a deliberate refinement of the flat field list sketched in
`DESIGN_stages_3_6.md` §2.1, and it changes nothing the loader reads.

```json
{
  "artefact": "heat_anchor",
  "generated": "2026-10-09",
  "status": "PUBLISHED_STATION_RECORD_NOT_A_MEASUREMENT_OF_THIS_SITE",
  "quantity": "HKO_air",
  "rangeC": [26.4, 32.7],
  "mapsTo": "range",
  "mapsToSemantics": "index 0.0 -> 26.4 C (a typical July night: the mean daily minimum); index 1.0 -> 32.7 C (a typical July afternoon: the mean daily maximum). A display band the relative index is read against, not a site measurement.",
  "source": {
    "name": "Hong Kong Observatory open data - Lau Fau Shan (LFS) daily air-temperature record",
    "publisher": "Hong Kong Observatory",
    "station": "LFS",
    "stationName": "Lau Fau Shan",
    "url": "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php",
    "dataTypes": ["CLMTEMP", "CLMMAXT", "CLMMINT"],
    "queryExample": "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMINT&lang=en&rformat=json&station=LFS",
    "licence": "Hong Kong Observatory open data (GovHK Terms of Use); free, no key",
    "retrieved": "2026-10-09",
    "window": "July 2015-2024",
    "stationDistanceFromSiteKm": 20,
    "values": {
      "meanDailyMinC": 26.4,
      "meanC": 28.96,
      "meanDailyMaxC": 32.7,
      "hottestDailyMaxC": 37.0,
      "coolestDailyMinC": 23.3
    },
    "method": "Mean of the daily minimum and daily maximum air temperature over July of 2015-2024, from the LFS daily station record. Missing values use the HKO sentinel '***' and are excluded, never coerced to 0. rangeC = [mean(daily min), mean(daily max)]."
  },
  "crossChecks": [
    {
      "name": "HKO 1991-2020 climate normals (HKO Headquarters)",
      "url": "https://www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm",
      "julyMeanDailyMinC": 26.9,
      "julyMeanC": 28.9,
      "julyMeanDailyMaxC": 31.6,
      "note": "A different product (a 30-year normal at HKO HQ), used only to show the LFS July band is ordinary for Hong Kong. NOT the anchor."
    },
    {
      "name": "Open-Meteo ERA5 archive (reanalysis) at the site centroid",
      "url": "https://archive-api.open-meteo.com/v1/archive",
      "latitude": 22.44,
      "longitude": 113.994,
      "julyMeanOfDailyMaxC": 30.99,
      "julyHottestDailyMaxC": 36.0,
      "note": "An independent reanalysis cross-check, within 1.8 C of the LFS band maximum. NOT the anchor."
    }
  ],
  "whatThisIsNot": [
    "This is a stated air-temperature band for a Hong Kong summer, NOT a measurement of the Hung Shui Kiu / Ha Tsuen site. The LFS station is ~20 km away and this is a 10-year July climatology, not the site's air temperature on any day.",
    "This is AIR temperature, not skin (surface) temperature. Landsat/MODIS/ECOSTRESS LST runs 10-20 C above air on a hot paved surface and was unreachable (credential-gated) from this host. The two quantities are never conflated.",
    "The heat field stays a relative 0-1 index. This band is what the index is READ AGAINST; the field is never converted to degrees Celsius.",
    "The HEAT_INDEX land-class table remains hand-authored and unverified; the anchor does not change it.",
    "No comfort index (UTCI/PET/SET) is claimed."
  ]
}
```

**Schema contract (what the code may rely on).** `quantity` is one of `{"LST_skin", "HKO_air"}`;
`rangeC` is a two-element numeric list with `rangeC[0] < rangeC[1]`; `mapsTo == "range"`. Anything else
is treated as an unreadable file (see §5.1).

---

## 4. Deliverable 2 — `backend/scripts/fetch_heat_anchor.py`

A one-shot fetcher, in the `fetch_dem.py` mould: fetch once, verify against control points, write the
committed artefact. **It is never imported by `app/` and never runs at request time.**

### 4.1 CLI

```
python scripts/fetch_heat_anchor.py                 # fetch, verify, write the committed JSON
python scripts/fetch_heat_anchor.py --check         # recompute and assert the committed file still agrees; no write
python scripts/fetch_heat_anchor.py --station LFS --start-year 2015 --end-year 2024 --month 7
python scripts/fetch_heat_anchor.py --retrieved 2026-10-09   # provenance date (default: today)
python scripts/fetch_heat_anchor.py --out PATH       # default: data/reference/heat_anchor.json
```

- `--check` is the **idempotency / drift gate**: it recomputes the band and asserts `quantity`,
  `rangeC`, `mapsTo` and `source.values` equal the committed file's, then exits non-zero on any
  mismatch. It is the CI-able form of "reproduces the committed file given the same window".
- `--retrieved` is the **only** non-deterministic field (the fetch date). The *numbers* are
  deterministic because the window is fixed; `--check` compares numbers and ignores the dates, so a
  re-run in 2027 still passes if HKO has not revised the record.

### 4.2 Endpoints and request shape

Three calls, one per dataset, to the same endpoint with a different `dataType`:

```
https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMTEMP&lang=en&rformat=json&station=LFS
https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&lang=en&rformat=json&station=LFS
https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMINT&lang=en&rformat=json&station=LFS
```

`CLMTEMP` = daily mean, `CLMMAXT` = daily maximum, `CLMMINT` = daily minimum. The response shape is
`{"type": ..., "fields": ["Year","Month","Day","Value","Completeness"], "data": [[...], ...]}`.
`dataTypes` is written into `source.dataTypes` from the same constant used to build the URLs, so the
provenance cannot drift from the request.

**Pacing and caching** (the `fetch_dem.py` pattern, trimmed): one JSON cache file per URL under
`backend/data/heat_cache/`, keyed on a hash of the URL; a `User-Agent` header; a 2 s pause between the
three calls; honour `Retry-After` on HTTP 429/5xx, else exponential backoff. With only three calls this
is politeness and resumability, not throughput.

### 4.3 Filtering to July 2015–2024, and the `"***"` sentinel

The parser is a pure function `parse_window(payload, month, start_year, end_year) -> (values, stats)`,
separated from the network call so it is unit-testable offline (see §8).

For each row `[year, month, day, value, completeness]`:

1. Keep the row only if `int(year) in [start_year, end_year]` and `int(month) == month` (7).
2. **Sentinel handling.** If `value` is the HKO missing-value sentinel `"***"` — or an empty string,
   or anything that does not parse as a float — **drop the row and count it**. Never coerce it to
   `0.0` (which biases the mean down and makes the band look cooler than it is) and never to `NaN`
   (which poisons the mean). The live response contains exactly this case: `["2024","1","14","***",""]`
   was observed by the PM. The dropped count is printed and recorded so a sudden jump in missing days
   is visible rather than silent.
3. Parse `value` to float, append to the dataset's list.

The mean daily minimum is `mean(CLMMINT values)`; the mean daily maximum is `mean(CLMMAXT values)`;
the mean temperature is `mean(CLMTEMP values)`. `rangeC = [round(mean_min, 1), round(mean_max, 1)]`.
Rounding to 0.1 °C matches the published figures (26.4 / 32.7) and the band is a display band, so a
tenth of a degree is the honest resolution.

### 4.4 Control-point assertions (a wrong parse must crash, not ship)

Mirroring `fetch_dem.py`'s "verified against known truth before any use". Every assertion prints its
observed value on success and raises with the observed value on failure.

| # | Assertion | Failure mode it catches |
|---|---|---|
| C1 | each dataset returns `>= 10000` rows | a truncated or error payload |
| C2 | each dataset has `280 <= valid_july_days <= 310` (10 Julys × 31 days) | a broken date filter, or a parser that silently drops most rows |
| C3 | `25.5 <= mean_daily_min <= 27.5` | a wrong column parsed (value column vs year/month) |
| C4 | `31.5 <= mean_daily_max <= 33.5` | a wrong column parsed |
| C5 | `28.0 <= mean_temperature <= 30.0` | a wrong column parsed |
| C6 | `mean_daily_min < mean_temperature < mean_daily_max` | a min/max column mix-up (the internal-consistency check) |
| C7 | `rangeC[0] < rangeC[1]` | a reversed band |

The bands are wide enough to survive a minor HKO revision and tight enough that a column mix-up lands
outside them. C6 is the cheap one that catches the commonest error.

**Optional cross-check (`--cross-check`, on by default, never fatal on network failure).** Fetch the
Open-Meteo ERA5 archive at the site centroid (22.44 N, 113.994 E) for July `start_year`–`end_year`,
compute the mean of the daily maxima, and assert `abs(era5_max − mean_daily_max) <= 3.0` (the observed
gap is 1.7 °C). If the archive is unreachable, print a note and continue: **the committed artefact must
not depend on a second network source.** This is the independent check the PM ran; it is a cross-check,
never the anchor.

### 4.5 Provenance writing

The script writes the whole JSON literal of §3, built from a `core` dict (the deterministic part) and a
`provenance` dict (`generated`, `source.retrieved`, `source.url`, `source.dataTypes`, the dropped-value
count). It writes atomically (write to a temp file, then rename) so an interrupted run cannot leave a
half-file that `load_heat_calibration()` would then read. It prints a summary:

```
[anchor] LFS  July 2015-2024  days: min 309 max 310 mean 310 (dropped 1 '***')
[anchor] control mean_daily_min  26.4 C  OK (25.5..27.5)
[anchor] control mean_daily_max  32.7 C  OK (31.5..33.5)
[anchor] cross-check ERA5 mean-of-daily-max 30.99 C  |diff| 1.71 <= 3.0  OK
[anchor] wrote data/reference/heat_anchor.json  rangeC=[26.4, 32.7]  quantity=HKO_air
[anchor] RELATIVE INDEX band only - not a measurement of this site
```

---

## 5. Deliverable 3 — the wiring changes

### 5.1 `backend/app/services/design_metrics.py`

**a) Correct the enum map (design_metrics.py:1029).**

```python
source = {"LST_skin": "landsat_lst", "HKO_air": "hko_station"}.get(quantity, "unknown")
```

**b) Harden `load_heat_calibration()` (design_metrics.py:987) against a malformed file.** Today it
returns whatever JSON is on disk. A file with an unknown `quantity` or a missing/malformed `rangeC`
would produce a block that says `ANCHORED_TO_PUBLISHED_REFERENCE` while carrying no usable band — a
half-anchored lie. The loader validates the schema contract of §3 (`quantity` in the known set,
`rangeC` a two-element numeric list with `rangeC[0] < rangeC[1]`, `mapsTo == "range"`) and returns
`None` when it does not hold, so the response falls back to the honest `anchorSource: "none"` path.
This changes no existing behaviour for a valid file and makes the unanchored branch reachable again for
a bad one. (This is the same instinct as `fetch_dem.py`'s control points: refuse rather than serve a
plausible-looking wrong thing.)

**c) Rewrite the data-present warning in `_anchoring_block()` (design_metrics.py:1039).** It must name
the quantity, the source, the band, and the site caveat, and must not index `rng` unguarded:

```python
band = (f"{rng[0]}-{rng[1]} C" if isinstance(rng, (list, tuple)) and len(rng) == 2 else "unknown")
"warning": (
    "Anchored to a cited air-temperature band: HKO Lau Fau Shan station, "
    f"July 2015-2024 ({quantity}: {band}). This is a stated band for a Hong Kong "
    "summer, not a measurement of this site -- the station is ~20 km away. The "
    "field remains a relative 0-1 index, never degrees Celsius. "
    "Screening-level; relative, not absolute."
)
```

This is the only place the warning text changes. The **no-data** warning constant
`HEAT_ANCHOR_WARNING` (design_metrics.py:146) is unchanged.

**d) `HEAT_STATUS` does not change.** The field is still `RELATIVE_INDEX_NOT_A_TEMPERATURE`; only
`anchoring.status` becomes `ANCHORED_TO_PUBLISHED_REFERENCE`. `heat_field_report()`'s top-level
`status` (design_metrics.py:1087) stays `HEAT_STATUS`.

**e) Update two docstrings to stay honest.** `load_heat_calibration()` (design_metrics.py:987) currently
says the quantity "is `"LST_skin"` or `"HKO_air"`, never both"; keep, but add that the source label is
derived from the quantity and that a station record is labelled `hko_station`, not `hko_normals`.
`heat_field_report()` (design_metrics.py:1054) says "Never converts to a temperature while
`anchorSource == 'none'`" — the qualifier is now wrong: it **never** converts to a temperature, on
every path. Correct it, and state *why* (a band is a display reference, not a conversion).

No other function in the module changes. `blended_heat_field`, `sky_view_factor*`, `shade_mask`,
`design_report`, `frontal_area_*`, `cell_fad` are untouched.

### 5.2 `backend/scripts/make_heatmap.py`

**a) The solar day** — see §6.

**b) Remove the sidecar duplication.** Today the sidecar carries the anchoring block **twice**: nested
under `"anchoring"` (make_heatmap.py:191) and again at top level as `"anchorSource"`,
`"anchorRangeC"`, `"calibrationPath"`, `"quantity"`, `"heatIndexSource"` (make_heatmap.py:192–196).

**Decision: remove the five top-level duplicates; keep only the nested `"anchoring"` block.**
Rationale: two spellings of one value can drift, which is the documented failure mode this project
keeps eliminating; the nested block is the canonical one because it is the same shape the API serves
and it also carries `status` and `warning`. No test or frontend reads the top-level keys (verified:
nothing under `backend/tests`, `backend/app` or `frontend/src` references them), so removal is safe.
The top-level `"status"` and `"warning"` (make_heatmap.py:183–188) are the *field's* status and
warning, not the anchor's, and **stay**.

**c) Bump the hardcoded `"generated": "2026-10-06"`** (make_heatmap.py:182) to the render date, so the
sidecar's provenance is not stale. (The DEM fetcher hardcodes its date too; the heatmap is
regenerated more often, so a live date is the honest choice.)

### 5.3 `backend/app/routers/scenes.py`

- `get_heat_field()` (scenes.py:867–942): change the `day_of_year` default from `355` to `196` (§6),
  and update the docstring line `GET .../heat-field?hours=9,14&day_of_year=355` (scenes.py:870) to
  `...&day_of_year=196`. The response already echoes `solarInputs.dayOfYear` and `hoursLocal`
  (scenes.py:941), so the rendered day is machine-readable with no new key. Update the docstring's
  "with no real Landsat LST or HKO calibration on disk the anchoring block says so
  (`anchorSource: "none"`)" (scenes.py:877–878) — the file is now on disk, so the anchored state is
  the normal one.
- `post_scenario_compare()` (scenes.py:814) and `get_design_metrics()` (scenes.py:528) and
  `post_scenario_evaluate()` (scenes.py:713) keep their `355` default: those are the **shade axis** and
  the **design-metrics** surface, not the heat surface, and changing them moves pinned scenario numbers
  and a pinned contract default. They are flagged as a follow-up consistency item in §9, not changed
  here. `design_metrics.design_report()`'s default (design_metrics.py:567) and
  `scenario.evaluate()`'s default (scenario.py:317) likewise stay.

### 5.4 What deliberately does **not** change

- No new response key, no new endpoint, no signature change.
- `HEAT_INDEX` (in `_overlay_common`) — untouched.
- `HEAT_BLEND_WEIGHTS` — untouched.
- The field is never written to disk in °C; `heatmap_hsk.png` stays the 0–1 ramp.

---

## 6. Deliverable 4 — the summer solar day

**The problem, verified.** `backend/data/rasters/heatmap_hsk.json` carries
`blend.solarDayOfYear = 355` — 21 December, the winter solstice — and `blend.solarHour = 14.0`. The
anchor is a **July** band. A heat / heatwave demo rendered under a mid-winter sun contradicts its own
anchor and undercuts the story a judge sees.

**The chosen value: day-of-year 196 = 15 July.** Justification:

- It sits **inside the anchor's window** (July 2015–2024). Rendering at the solstice (172 = 21 June)
  would be the highest sun but would put the picture a month outside the band's month; 196 keeps the
  shadow geometry and the anchor in the same month.
- It is **near the annual solar maximum**: at 22.44 N, 114.01 E, 14:00, day 196 gives a solar altitude
  of ≈ 67.8° — short afternoon shadows, the geometry a July afternoon actually has, matching the
  band's 32.7 °C "typical July afternoon" reading.
- It is **deterministic**: a fixed integer, no wall-clock, no RNG.

**Where it lives: a named constant plus a CLI flag with a summer default, and the route default.**

- New constant in `design_metrics.py`:
  `HEAT_SUMMER_DAY_OF_YEAR: int = 196  # 15 July; matches the July anchor band`.
- `make_heatmap.py` `--day-of-year` default `355 → HEAT_SUMMER_DAY_OF_YEAR`, help text changed to
  `"solar day for the shadow term (default 196, 15 July -- matches the July anchor)"`.
- `GET /heat-field` `day_of_year` default `355 → HEAT_SUMMER_DAY_OF_YEAR`.
- The chosen day is **stated in the response** already (`solarInputs.dayOfYear`) and in the sidecar
  (`blend.solarDayOfYear`), so nothing is hidden.

It is a CLI flag (not a hardcoded literal) so a maintainer can still render another season, but the
default is deterministic and summer. The committed `heatmap_hsk.json` / `heatmap_hsk.png` are
**regenerated in the same commit** as the default change.

---

## 7. Deliverable 5 — the `README.md` truth pass

Exact locations, as mapped. Line numbers are from the current file.

### 7.1 `### Sources` table (README.md:753–760) — add a row

After the `Elevation` / `Land cover` / `Base imagery` rows, add:

```
| Heat anchor | [HKO open data — Lau Fau Shan station](https://data.weather.gov.hk/weatherAPI/opendata/opendata.php) (`CLMTEMP`/`CLMMAXT`/`CLMMINT`) | HKO open data (GovHK Terms of Use) | daily station record, ~20 km from the site | **Cited band** — *air* temperature, July 2015–2024; a stated band, not a site measurement |
```

### 7.2 `### The heat overlay` (README.md:796–803) — state the final choice

Replace the three-line body with a statement that covers: real data (not synthesized), air (not skin),
the cited source and URL, the band `[26.4, 32.7] °C`, the retrieved date, and the caveat that it is a
stated band for a Hong Kong summer, **not** a measurement of this site (the station is ~20 km away):

```
### The heat overlay

**The final choice: real air temperature, cited, as a band the relative index is read against — not a
temperature field.**

The field itself is unchanged: land class (hand-authored) + sky view + cast shadow → a **relative
heat-exposure index on a 0–1 scale** → RGBA ramp. It is **not degrees Celsius** and **not** a modelled
air-temperature field. What is new is that the scale now states the band it is read against, from
committed, cited data (`backend/data/reference/heat_anchor.json`):

- **Real, not synthesized.** Source: **HKO open data — Lau Fau Shan (LFS) station daily air
  temperature**, `https://data.weather.gov.hk/weatherAPI/opendata/opendata.php`
  (`dataType=CLMTEMP|CLMMAXT|CLMMINT`, `station=LFS`), retrieved **2026-10-09**.
- **Air temperature, not skin.** Every skin-temperature source (Landsat 8/9 C2 L2 ST, MODIS MOD11,
  ECOSTRESS) is credential-gated and was unreachable from this host. Skin runs 10–20 °C above air on a
  hot paved surface and is **not** claimed.
- **The band: [26.4, 32.7] °C.** Index 0.0 ↔ 26.4 °C (a typical July night, the mean daily minimum);
  index 1.0 ↔ 32.7 °C (a typical July afternoon, the mean daily maximum). Mean of the daily min/max
  over **July 2015–2024**, missing values excluded.
- **Honest caveat.** This is a **stated band for a Hong Kong summer, not a measurement of this site** —
  the LFS station is ~20 km away, and it is a 10-year July climatology, not the site's air temperature
  on any day.
- Cross-checked against the HKO 1991–2020 normals (HKO HQ, July 26.9 / 31.6) and the Open-Meteo ERA5
  archive at the site centroid (July mean-of-daily-max 30.99 °C). Neither is the anchor.

The anchor is a **committed file, never fetched at request time** (deterministic); it is reproduced by
`backend/scripts/fetch_heat_anchor.py`. The overlay is rendered at a **summer solar day (day 196,
15 July)** to match the July band. `--heatwave` is still a *display* stretch, not a scenario.
```

### 7.3 `### The wet grid` (README.md:792–794) — correct the now-false claim

The sentence "There is **no flow routing and no sink conditioning** — water is placed by level, not
routed downhill." is **false**: the D8 fill-and-spill cascade was built and committed this session
(`routed_depth_hsk.npy`, `GET /api/scenes/{id}/drainage/routed`, mass balance to 1e-15 relative). Keep
the honest distinction that the **headline deficit still comes from the level-pool path**
(`STATUS.md` gap 8). Replace with:

```
The wet grid is a **level-pool** allocation: water is placed by water-surface level, not routed
downhill, and the **headline deficit** (`bufferDeficitM3`, `wetgrid_hsk.npy`) comes from this path. A
**separate** D8 fill-and-spill routing cascade now exists (`routed_depth_hsk.npy`,
`GET /api/scenes/{id}/drainage/routed`, mass-balanced to 1e-15 relative) as an **additive screening
surface** — it does **not** replace or correct the headline number, and it is not a flood prediction.
The two are different pictures of the same water and are deliberately not merged.
```

### 7.4 `## Limitations: what this is not` (README.md:970)

- **"Not a flood model" paragraph (README.md:975–979)** — the phrases "no flow accumulation, no sink
  conditioning, no drainage network" are now false. Replace the parenthetical with an honest statement:
  a D8 fill-and-spill cascade and a HAND cross-check now exist as an **additive screening surface**,
  but they are screening-level, they do not replace the level-pool headline, and they are not a flood
  prediction. Keep "no tide boundary" (still true).
- **Heat line (README.md:988–989)** — update to name the anchor and the caveat:
  "**The heat overlay is a relative index, not a temperature.** It is anchored to a cited air-
  temperature band (HKO Lau Fau Shan, July [26.4, 32.7] °C), but that is a stated band for a Hong Kong
  summer, not a measurement of this site. `--heatwave` is a display transform, not a scenario."

### 7.5 `### Next steps, in priority order` (README.md:1001)

- **Item 4** ("Add flow routing and sink conditioning to turn the wet grid from a screening view into
  something defensible", README.md:1008–1009) is now partly done. Replace with:
  "**Reconcile the two drainage readings** — the routed fill-and-spill cascade exists and balances, but
  the headline deficit still comes from the level-pool path. Decide which is the headline, or show
  both."
- **Add a new item** for the anchor's known limitation: "**Replace the ~20 km-away anchor** with a
  site-specific source (a nearby station, or a Landsat/MODIS LST scene once credentials exist), and
  keep the air-vs-skin distinction." This is the honest next step the anchor creates.

### 7.6 `## Credits` (README.md:1016)

Add: `- [Hong Kong Observatory open data](https://data.weather.gov.hk/) — the heat anchor, GovHK Terms of Use`.

---

## 8. Deliverable 6 — the test plan

### 8.1 Existing assertions that flip when the file lands (update in the same commit)

| File:line | Today | After |
|---|---|---|
| `tests/test_contract.py:392` | `assert body["anchoring"]["anchorSource"] == "none"` | **Replace** with the anchored shape: `anchorSource == "hko_station"`, `anchorRangeC == [26.4, 32.7]`, `quantity == "HKO_air"`, `calibrationPath == "reference/heat_anchor.json"`, `anchoring.status == "ANCHORED_TO_PUBLISHED_REFERENCE"`. Keep `heatIndexSource == "Hand-authored screening index; awaiting verification"` and top-level `body["status"] == "RELATIVE_INDEX_NOT_A_TEMPERATURE"` (both still true). |
| `tests/test_contract.py:594` | `assert default["solarInputs"]["dayOfYear"] == 355` | `== 196` (the `/heat-field` default is now summer). |
| `tests/test_design_metrics.py:420–424` | else-branch: `anchorSource in ("landsat_lst", "hko_normals") and anchorRangeC is not None` | **Replace** with `anchorSource == "hko_station" and quantity == "HKO_air" and anchorRangeC == [26.4, 32.7] and calibrationPath == "reference/heat_anchor.json" and anchoring.status == "ANCHORED_TO_PUBLISHED_REFERENCE"`, plus `heatIndexSource` unchanged. |

**The PM's claim about `test_design_metrics.py:~420` — verified, with one correction.** The claim
("an existing else-branch already asserts the anchored shape and flips on file presence") **holds**:
there is an `if calibration is None: … else: …` branch at lines 409–424 that takes the else path once
the file exists. But it is **weaker and more specific than the PRD states**: it asserts only
`anchorSource in ("landsat_lst", "hko_normals") and anchorRangeC is not None` — it does **not** check
`quantity`, `status`, or `calibrationPath`, and its enum tuple **excludes `"hko_station"`**, so it
would fail the moment the corrected enum lands. Both must be fixed (§2, §8.1).

### 8.2 New assertions

1. **The field is never a temperature (P0-A3).** Assert the `/heat-field` response and the sidecar
   contain no key matching `(?i)(temp|temperature|lst).*c$` / `celsius` / `degreesC`, and no key whose
   value is a temperature. Pin `HEAT_INDEX` (paved 1.00, bare 0.85, green 0.40, wetland 0.20,
   water 0.05) and `heatIndexSource`. Assert `0.0 <= meanHeatIndex <= 1.0` and `maxHeatIndex <= 1.0`.
2. **Determinism / no runtime network (P0-A4).** Grep `backend/app/` for `requests`, `urllib`, `httpx`,
   `socket` and assert none is imported; assert `load_heat_calibration()` reads only
   `settings.data_dir / HEAT_CALIBRATION_PATH`. With the file present, assert it returns the anchor.
3. **The parser, offline (P0-A2).** Import `parse_window` and run it on a small captured fixture that
   **includes** the sentinel row `["2024","1","14","***",""]`; assert the sentinel is dropped, the
   valid-day count excludes it, and the mean equals the mean over the numeric rows only (i.e. the
   sentinel did **not** enter as 0). This is the sentinel regression the PRD asks for, tested without a
   network call.
4. **Malformed calibration → unanchored.** Write a temp JSON with an unknown `quantity` (or a missing
   `rangeC`, or `rangeC[0] >= rangeC[1]`) and assert `load_heat_calibration()` returns `None` and the
   anchoring block reports `anchorSource == "none"` (the honest fallback, not a half-anchored block).
5. **Sidecar duplication removed.** Assert `heatmap_hsk.json` has `anchoring` and does **not** have the
   top-level `anchorSource` / `anchorRangeC` / `calibrationPath` / `quantity` / `heatIndexSource` keys.
6. **Solar day.** Assert the sidecar `blend.solarDayOfYear == 196` after a re-run, and the `/heat-field`
   default `solarInputs.dayOfYear == 196` while an explicit `day_of_year=355` still returns `355`.

### 8.3 Honesty assertions that must survive (do not weaken)

- No °C-converted key anywhere; the field stays a 0–1 index.
- `HEAT_INDEX` still hand-authored and labelled as such; not duplicated in `design_metrics`.
- No runtime network on the heat path.
- `status` is always a label from the closed set, never a number and never a verdict; the top-level
  heat `status` stays `RELATIVE_INDEX_NOT_A_TEMPERATURE`.
- The field warning still contains "Heat is not a temperature" and "Screening-level; relative, not
  absolute" (`test_design_metrics.py:425–427`).
- The anchored warning names the quantity, the source, and the "not a measurement of this site" caveat.

---

## 9. Deliverable 7 — risks, each with the check that catches it

| # | Risk (a plausible wrong thing) | The specific check |
|---|---|---|
| R1 | **The source is mislabelled** — a station record called "normals". | `anchorSource == "hko_station"`; the corrected enum recorded once in `_anchoring_block`; a grep test that `"hko_normals"` no longer appears as a value in `design_metrics.py`; the docs corrected (§2). |
| R2 | **A hidden °C conversion** creeps into the field or the response. | §8.2.1: no `tempC`-style key; `HEAT_INDEX` pinned; `0 <= index <= 1`. |
| R3 | **Runtime network creep** (a request-time fetch). | §8.2.2: grep `app/` for network imports; the loader reads only the committed path. |
| R4 | **The `"***"` sentinel coerced to 0**, biasing the band cooler. | §8.2.3: the parser fixture with the sentinel yields the numeric-only mean; the valid-day count is asserted in-band (C2). |
| R5 | **A wrong column parsed** (year/month read as the value). | C3–C5 band control points; C6 `min < mean < max`. |
| R6 | **The committed file drifts from the fetcher** (edited by hand). | `fetch_heat_anchor.py --check` recomputes and compares `quantity`/`rangeC`/`values`; a test runs `parse_window` on a fixture. |
| R7 | **The solar day changes but the sidecar is not regenerated.** | §8.2.6: the committed sidecar reads `196`; the `.png`/`.json` are regenerated in the same commit. |
| R8 | **The now-present file silently breaks the contract tests** (someone deletes the assertion). | §8.1: `test_contract.py:392` is *replaced* with the anchored shape, not deleted; the anchored branch is now the tested path. |
| R9 | **The band is read as a site measurement.** | The warning names the ~20 km distance and "not a measurement of this site"; the README states it; the JSON's `whatThisIsNot` says it. |
| R10 | **Air/skin conflation.** | `quantity == "HKO_air"`; the README and warning say "air"; no `LST` claim anywhere; the P2 LST path stays separate and unclaimed. |
| R11 | **The loader serves a half-anchored block** from a malformed file. | §5.1b + §8.2.4: an invalid file returns `None` → the honest `"none"` path. |
| R12 | **The `/design-metrics` shade default (355) and the heat default (196) disagree**, and a judge notices. | **Known and accepted** for this increment (out of scope: it moves pinned scenario numbers and `test_contract.py:453`). Flagged as a follow-up in §5.3 and in the demo task list (D6). |

---

## 10. Ordered backend-scoped task list for the demo must-haves

From `PRD_heat_anchor_and_demo.md` §3.1. Ordered by build priority. **This team can build the
backend/data/docs items; the frontend items are owned by the other process** and are listed only so the
seam is clear — they are not designed here.

**This team (backend / data / docs), in build order:**

| Order | Item | What it is | Effort | Why this order |
|---|---|---|---|---|
| **D1** | **B2 — the summer solar day.** | Day 196 (15 July) as the heat default; sidecar + endpoint regenerated. | **S** | **This increment.** It is the smallest, and it removes a self-inflicted inconsistency a judge can catch in one screenshot. |
| **D2** | **B3 — make `place` / `reset` scoreable.** | The scenario evaluator currently lists both under `unsupportedOps` (`STATUS.md` gap 7). The brief's headline loop is "edit land use → deficit in seconds". Route the two ops through the city-model generator rather than reimplementing them (the drift `STATUS.md` warns about). | **L** | The single largest **backend** demo gap, and the central interaction of the pitch. |
| **D3** | **B8 — verify the Curve Number table (HSG for HSK/HT).** | The largest lever on every drainage number; currently HSG D *assumed*. Publish the verified table or keep the ±20 % band. | **M** | Honesty, and the first thing a judge will probe on the numbers. |
| **D4** | **B7 (backend half) — confirm the sea-level response is coherent for the UI.** | The endpoint exists and is honest ("inert at this site"); make sure the served shape is what the UI needs. | **S** | Cheap, and it is the brief's "most memorable part". |
| **D5** | **B10 — reconcile the two study areas in the pitch framing.** | Docs only: brief says San Tin / Tai Sang Wai / Mai Po; repo locked HSK/HT. | **S** | Framing, not code; a judge who reads the brief will notice. |
| **D6** | **The `/design-metrics` and scenario shade default (355 → 196), if the demo shows that surface.** | Aligns the shade axis with the heat surface (R12). Moves `test_contract.py:453` and possibly scenario numbers. | **S–M** | Do only if the demo shows `/design-metrics`; otherwise it is churn. |
| **D7** | **B9 — backup demo recording + pitch deck.** | Process; hard deadlines (freeze 14 Oct, submit 18 Oct, pitch 24 Oct). | **M** | Process, not engineering, but on the critical path. |

**Other process (frontend) — explicitly not designed here:**

| Item | What it is | Effort |
|---|---|---|
| **B1** | Frontend surface for the four new backend routes: `heat-field`, `ventilation/corridors`, `drainage/routed`, `scenario/compare`. The backend's best evidence is currently invisible in the product. | **L** |
| **B4** | Scenario comparison demonstrable in the UI — a weight control and the ranked list with the three axes beside it. | **M** |
| **B6** | Three published CEDD plans toggled in one click. | **S** |
| **B5** | Wet-grid "pour" animation / overlay clarity. | **M** |
| **B7 (frontend half)** | Make the sea-level result visible. | **S** |
| **B11** | Frontend renderer unit coverage (the overlay layers and the online-asset loader). | **M** |

**Deliberately not built** (per `RUNG_AND_BUSINESS.md` rung C): typhoon surge + jacking, the perception
encoder (ESA WorldCover used instead), the generative emulator, Kappa validation vs recorded flood
zones, InVEST/UFRM (SCS-CN from TR-55 used directly), and the CEDD LiDAR bare-earth DTM (request-gated).
Say so; do not hide it.

---

## 11. Definition of done for this increment

- `backend/data/reference/heat_anchor.json` committed, matching §3.
- `backend/scripts/fetch_heat_anchor.py` committed, runnable, idempotent, with `--check`.
- `anchorSource == "hko_station"`; the corrected enum recorded in code, tests and `DESIGN_stages_3_6.md`.
- `HEAT_STATUS` unchanged; `HEAT_INDEX` still hand-authored; no °C key anywhere; no runtime network.
- The heat overlay rendered at day 196; the sidecar carries `blend.solarDayOfYear == 196`; the
  sidecar's top-level anchoring duplicates removed.
- `README.md` states the choice (§7.2), adds the Sources row (§7.1), and corrects the wet-grid claim
  (§7.3) and the Limitations/Next-steps text (§7.4–7.5).
- All 393 backend tests green, with the four flips of §8.1 updated and the new assertions of §8.2 added.

---

*This is a screening-level increment. The heat field stays a relative 0–1 index; the anchor states the
air-temperature band it is read against and never becomes a temperature; no output carries a verdict.*
