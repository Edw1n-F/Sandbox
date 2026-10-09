# PRD — Heat anchor (Stage 4b) and prototype-demo readiness

**Owner:** 许清楚 (PM) · **Date:** 2026-10-09 · **Type:** incremental PRD + research note, not greenfield
**Source of truth:** `STATUS.md` (Stage 4b, gaps 9 & 11); `docs/drafts/PRD_stages_3_6.md` §4 (anchoring
prerequisite); `docs/drafts/DESIGN_stages_3_6.md` §1 Q6, §6 (labels); the hackathon brief
`GENAI Hackathon.docx` / `_extracted_docx.txt`; the team's own `Hack4SDG_Oct6_TODO.md`,
`Hack4SDG_Oct6_Edwin_Brief.md`, `Edwin_Action_Plan_2026-10-05.md`; `docs/RUNG_AND_BUSINESS.md`.
**Scope:** the anchoring increment (Stage 4b) and a demo-readiness gap list. **No code, no
architecture** — that is the architect's and engineer's lane. This file is the PM's research,
decision and acceptance criteria.

---

## 0. TL;DR

1. **The decision is REAL data — air temperature.** The HKO Lau Fau Shan station record *and* the
   Open-Meteo ERA5 archive are both reachable, keyless, from this host. Every **skin**-temperature
   source (Landsat 8/9 LST, MODIS, ECOSTRESS) is credential-gated and **unreachable**. So the anchor
   is a cited **air**-temperature band (`quantity: "HKO_air"`), and skin is explicitly *not*
   claimed.
2. **The anchor stays a band, not a temperature.** The heat field remains a relative 0–1 index;
   the anchor states which air-temperature band the scale is read against. It is never converted to
   °C, and `heatIndexSource` stays hand-authored.
3. **Top demo blockers:** (a) the new backend capabilities (heat-field, corridors, routed drainage,
   `compare`) have **no frontend surface** — built but not showable; (b) the heat overlay renders a
   **December** solar scene while a heat demo and a July anchor need a summer day; (c) the AI's
   most-used ops `place`/`reset` are **placeable but not scoreable**, so the headline
   "edit land use → deficit in seconds" loop is half-missing.

---

## 1. Deliverable 1 — Data research: what this host can *actually* reach

Method: real `curl` fetches from this host on **2026-10-09**. Every row below is a response I saw,
not a source I know exists. A source I could not reach is reported as such.

| # | Candidate | Exact URL tested | HTTP | Reachable? | Verdict |
|---|---|---|---|---|---|
| 1 | **Open-Meteo ERA5 archive** (2 m air temp) | `archive-api.open-meteo.com/v1/archive?latitude=22.44&longitude=113.994&start_date=2015-07-01&end_date=2024-07-31&daily=temperature_2m_max,temperature_2m_mean,temperature_2m_min&timezone=Asia%2FHong_Kong` | **200** | **Yes, keyless** | **USABLE — air temp** |
| 2 | **HKO open data — Lau Fau Shan station** (daily mean/max/min air temp) | `data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMTEMP\|CLMMAXT\|CLMMINT&lang=en&rformat=json&station=LFS` | **200** | **Yes, keyless** | **USABLE — air temp** |
| 3 | **HKO 1991–2020 climate normals** (HKO HQ, air temp) | `www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm` | **200** | **Yes, keyless** | **USABLE — air temp (cross-check)** |
| 4 | Open-Meteo elevation (already used by the project) | `api.open-meteo.com/v1/elevation?latitude=22.44&longitude=114.01` | 200 | Yes, keyless | Sanity only |
| 5 | Open-Meteo climate API (CMIP downscale) | `climate-api.open-meteo.com/v1/climate?...&models=MRI_AGCM3_2_S` | 200 | Yes, keyless | Available; not needed |
| 6 | **USGS Landsat 8/9 C2 L2 Surface Temperature** | `landsatlook.usgs.gov/data/collection02/landsat-c2l2-st/tile/122/044/2020/` | 200 | **No** — body is the *EROS Registration System login page* | **UNREACHABLE (auth)** |
| 7 | USGS M2M API | `m2m.cr.usgs.gov/api/api/json/stable/login` | 404 | No | **UNREACHABLE (auth)** |
| 8 | **NASA LAADS MOD11 (MODIS LST)** | `ladsweb.modaps.eosdis.nasa.gov/archive/allData/61/MOD11A1/2019/182/MOD11A1.A2019182.h29v06.061...hdf` | 200 | **No** — returns `text/html` (Earthdata login), not the `.hdf` | **UNREACHABLE (auth)** |
| 9 | USGS `e4ftl01` MODIS mirror | `e4ftl01.cr.usgs.gov/MOLT/MOD11A1.061/` | 404 | No | **UNREACHABLE** |
| 10 | NASA Earthdata portal (ECOSTRESS route) | `earthdata.nasa.gov` | 200 | Portal loads, data does not | **UNREACHABLE without Earthdata account** |

**The decisive physical fact.** Everything reachable is an **air** temperature. The only **skin**
temperature products (Landsat/MODIS/ECOSTRESS LST) are behind USGS/Earthdata credentials that this
host does not have. LST skin and air temperature are different quantities — LST can run 10–20 °C
above air on a hot paved surface — and the design forbids conflating them. **The anchor therefore
names `HKO_air`, and the response must never read as a skin measurement.**

### 1.1 Values actually extracted (2026-10-09)

**HKO Lau Fau Shan (LFS) station — nearest long-record HKO station to HSK/HT; the station the
project's own `Edwin_Action_Plan` names for temperature.** July 2015–2024, from the daily record:

| Quantity | Value |
|---|---|
| Mean daily **minimum** (July) | **26.4 °C** |
| Mean temperature (July) | 28.96 °C |
| Mean daily **maximum** (July) | **32.7 °C** |
| Hottest daily maximum in the window | 37.0 °C |
| Coolest daily minimum in the window | 23.3 °C |

**HKO 1991–2020 normals (HKO HQ) — cross-check.** July: mean daily max **31.6 °C**, mean 28.9 °C,
mean daily min 26.9 °C. Close to LFS, which is why LFS is usable.

**Open-Meteo ERA5 archive at the study-area centroid (22.44 N, 113.994 E) — cross-check.** July
2015–2024: 10-yr mean-of-daily-max **30.99 °C**; hottest daily max 36.0 °C.

**Reproducible extraction commands** (exactly what I ran):

```bash
# HKO LFS daily mean / max / min (three calls), then filter to July 2015-2024:
curl -sS "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMTEMP&lang=en&rformat=json&station=LFS" -o lfs_mean.json
curl -sS "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&lang=en&rformat=json&station=LFS" -o lfs_max.json
curl -sS "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMINT&lang=en&rformat=json&station=LFS" -o lfs_min.json

# ERA5 archive at the site centroid, July 2015-2024:
curl -sS "https://archive-api.open-meteo.com/v1/archive?latitude=22.44&longitude=113.994&start_date=2015-07-01&end_date=2024-07-31&daily=temperature_2m_max,temperature_2m_mean,temperature_2m_min&timezone=Asia%2FHong_Kong" -o era5.json

# HKO 1991-2020 normals (July row of Table 4):
curl -sSL "https://www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm" -o normals.htm
```

The HKO JSON shape is `{type, fields:[Year,Month,Day,Value,Completeness], data:[[...]]}` with
missing values as `"***"`. The ERA5 shape is `{daily:{time,temperature_2m_max,...}}`.

---

## 2. Deliverable 2 — The decision, and an incremental PRD

### 2.1 The decision: **REAL data — air temperature, from the HKO Lau Fau Shan station**

**One-line reason:** both the HKO LFS station record and the Open-Meteo ERA5 archive are reachable
keyless from this host, so a cited **air**-temperature band is obtainable now; the only
**skin**-temperature sources are credential-gated and unreachable, so skin is not available and must
not be claimed.

**The anchor** (committed, cited, reproducible):

| Field | Value |
|---|---|
| `quantity` | `"HKO_air"` |
| `anchorSource` (enum) | `"hko_station"` — the Lau Fau Shan **daily station record**; the 1991–2020 normals are only a cross-check, not the source (see §2.5 note) |
| `rangeC` | `[26.4, 32.7]` — LFS July mean daily **minimum** → mean daily **maximum** |
| `mapsTo` | `"range"` |
| `source` | HKO Lau Fau Shan station daily temperature record, `data.weather.gov.hk` opendata API (`CLMTEMP`/`CLMMAXT`/`CLMMINT`, `station=LFS`) |
| `retrieved` | `2026-10-09` |
| `method` | Mean of daily min/max over July 2015–2024; cross-checked against the HKO 1991–2020 normals (HKO HQ, July: 26.9 / 31.6) and the Open-Meteo ERA5 archive at the site centroid (July mean-of-daily-max 30.99 °C) |
| `mapsTo` semantics | index **0.0 ↔ 26.4 °C** (a typical July night); index **1.0 ↔ 32.7 °C** (a typical July afternoon). A **display band**, not a site measurement |
| Target file | `backend/data/reference/heat_anchor.json` (committed; never fetched at runtime) |
| Fetcher | `backend/scripts/fetch_heat_anchor.py` (the `scripts/fetch_dem.py` precedent: fetch once, verify, commit the artefact) |

**Why this is defensible and not an overclaim.** It follows the two project precedents exactly:
`data/reference/hsk_ava_reference.json` (a committed, cited JSON) and `scripts/fetch_dem.py` (a
reproducible fetcher with control-point assertions). It uses the project's own chosen station. It
names the physical quantity so skin and air can never be conflated. And it changes **no** arithmetic:
the field stays a 0–1 relative index; only the *band it is read against* becomes cited.

**The honest caveat that must appear in the response.** This is a **stated band for a Hong Kong
summer, not a measurement of this site.** The station is ~20 km away; the band is a July climatology,
not the site's air temperature on any given day. The warning must say so.

**Fallback if the team judges the mapping too loose:** keep `anchorSource: "none"` and the status
`RELATIVE_INDEX_NOT_A_TEMPERATURE`. That is always available and is the status quo. The decision above
is a *gain in provenance*, not a claim of measurement.

### 2.2 Goal & non-goals

**Goal** — replace the hand-authored, unanchored heat scale with a scale that states, machine-readably,
the **cited air-temperature band** it corresponds to, via a committed calibration file, without ever
turning the field into a temperature and without claiming skin temperature.

**Non-goals** — no conversion of the field to °C; no skin temperature (Landsat/MODIS LST) — unreachable;
no site-specific air-temperature measurement; no comfort index (UTCI/PET/SET); no change to `HEAT_INDEX`
(the land-class table stays hand-authored); no runtime network fetch; no fourth scenario axis.

### 2.3 Requirement pool

Prioritisation: **P0** = the anchoring itself plus the hard guards. **P1** = the surfaces that show it.
**P2** = the credential-gated skin path.

#### P0 — the anchor and its guards

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P0-A1** | Commit `data/reference/heat_anchor.json` with the schema in §2.1 (`quantity: "HKO_air"`, `anchorSource: "hko_station"`, `rangeC: [26.4, 32.7]`, `mapsTo: "range"`, source, retrieved, method). | `load_heat_calibration()` returns the dict; `heat_field_report(...)["anchoring"]` shows `anchorSource == "hko_station"`, `anchorRangeC == [26.4, 32.7]`, `quantity == "HKO_air"`, `calibrationPath` set, and status `ANCHORED_TO_PUBLISHED_REFERENCE`. Committing the file flips four existing pins — the `anchorSource` mapping in `_anchoring_block` and the four assertions in **P0-A5** must move in the same commit. | "anchored to a published reference, not a measurement of this site" | `backend/data/reference/heat_anchor.json` |
| **P0-A2** | Reproducible fetcher `scripts/fetch_heat_anchor.py`: pulls the HKO LFS record (+ ERA5 cross-check), asserts the July band, writes the committed JSON with source + retrieved date. **Never runs at request time.** | Run the script from a clean checkout → it reproduces the committed file byte-identically (given the same window); with the network down, the *test* still passes because it reads the committed file only. | "reproducible from a cited public source" | `backend/scripts/fetch_heat_anchor.py` |
| **P0-A3** | **The field is never converted to a temperature.** No new key carries a °C-converted raster, and `HEAT_INDEX` stays hand-authored. | Test asserts the heat response contains no `temperatureC`/`lstC`/`tempC` key, that `heatIndexSource` is still `"Hand-authored screening index; awaiting verification"`, and that `status` is a label from the closed set — never a number and never a verdict. | "Heat is not a temperature" + "Screening-level; relative, not absolute" | `app/services/design_metrics.py` |
| **P0-A4** | **Determinism — the anchor is committed, never fetched at runtime.** | Grep the runtime import path (`app/`) for `requests`/`urllib`/`httpx`; `load_heat_calibration()` reads only `settings.data_dir / HEAT_CALIBRATION_PATH`. A test with network stubbed off returns the same anchor. | "deterministic; no network at request time" | `app/services/design_metrics.py` |
| **P0-A5** | **Update the pinned assertions that flip when the anchor file lands** — they currently pin `"none"` (or the old enum) and would fail, not silently pass. | In one commit with P0-A1: (1) `_anchoring_block`'s `quantity→source` map gains `"HKO_air" → "hko_station"`; (2) `tests/test_design_metrics.py:420` tuple extends to include `"hko_station"`; (3) `tests/test_contract.py:392` `anchorSource == "none"` becomes the anchored value when the file exists (or is guarded on file presence); (4) `tests/test_contract.py:594` hits **`/heat-field`** and flips `355 → 196` (15 July) when the heat-field default moves (P2-A2). `tests/test_contract.py:453` hits **`/design-metrics`** and **stays at 355** — a no-op in this increment (moving the design-metrics shade axis would shift scenario numbers; it is a stated follow-up, not part of this change). | "a flipping test is updated, not deleted or weakened" | `app/services/design_metrics.py`, `backend/tests/test_design_metrics.py`, `backend/tests/test_contract.py` |

#### P1 — the surfaces that show the anchor

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P1-A1** | `make_heatmap.py` sidecar carries the anchoring fields (already wired) and reflects the committed anchor after a re-run. | Re-run `python scripts/make_heatmap.py`; `data/rasters/heatmap_hsk.json` shows `anchorSource: "hko_station"`, `anchorRangeC: [26.4, 32.7]`, `quantity: "HKO_air"`, and the warning naming the air band. | "stated band, not a measurement of this site" | `backend/scripts/make_heatmap.py` |
| **P1-A2** | **State the decision in `T1/README.md`** (the user's explicit requirement): real data, air temperature, cited source, and the band. | README contains the one-line decision, the source URL, the band, and the date; a reader learns the scale is a *relative index anchored to a cited air-temperature band*. | "relative index, anchored to a cited band — not a temperature" | `T1/README.md` |
| **P1-A3** | `GET /api/scenes/{id}/heat-field` echoes the anchored block (already served) with the new status. | Contract test: the heat-field route returns 200 and `anchoring.anchorSource == "hko_station"` once the file exists; `status == "ANCHORED_TO_PUBLISHED_REFERENCE"`. | "screening-level; relative, not absolute" | `app/routers/scenes.py` |

#### P2 — the skin path (blocked)

| ID | Requirement | Acceptance criterion (runnable) | Honesty label | Target file |
|---|---|---|---|---|
| **P2-A1** | **Landsat 8/9 LST (skin)** — if USGS credentials ever become available, add a second calibration with `quantity: "LST_skin"` and the scene/date, kept separate from the air band. | Test skips with the reason "no USGS credentials" while unreachable; flips to the LST path only when a committed LST calibration exists. | "skin temperature; a different quantity from air" | `reference/` + `design_metrics.py` |
| **P2-A2** | Reconcile the anchor's **season** with the rendered solar scene (§3, blocker B2), scoped to the **heat surface only**. | The **`/heat-field`** default moves `355 → 196` (15 July), so a July anchor and the displayed shadow geometry agree; `make_heatmap.py` renders the same summer day. The `/design-metrics` and scenario shade axes **keep 355** in this increment — moving them would shift scenario numbers, so they are a stated follow-up, not part of this change. | "stated relation between anchor season and rendered scene" | `make_heatmap.py` / `heat-field` route |

### 2.4 Provenance table

| Input | Status today | What the anchor does with it |
|---|---|---|
| `HEAT_INDEX` (land-class index) | **Hand-authored** — paved 1.00, bare 0.85, green 0.40, wetland 0.20, water 0.05 | **Unchanged.** Stays labelled hand-authored; the anchor does not touch it |
| Anchor band | **None today** (`anchorSource: "none"`) | **Becomes cited** — HKO LFS air band [26.4, 32.7] °C, July 2015–2024 |
| Anchor quantity | — | **Air** (`HKO_air`). Skin is unreachable and is *not* claimed |
| SVF / shadow / blend | Real geometry, screening-grade | Unchanged; still 0–1 relative components |
| Source URL | — | `data.weather.gov.hk/weatherAPI/opendata/opendata.php` (`station=LFS`), retrieved 2026-10-09 |
| Cross-check | — | HKO 1991–2020 normals (HKO HQ) and Open-Meteo ERA5 archive (site centroid) |
| Runtime network | — | **None.** The file is committed; `fetch_heat_anchor.py` runs offline-of-runtime |

### 2.5 Note — the `anchorSource` enum is `"hko_station"`, not `"hko_normals"`

The anchor's **source** is the Lau Fau Shan **daily station record** (`CLMTEMP`/`CLMMAXT`/`CLMMINT`,
`station=LFS`). The HKO **1991–2020 normals** are a *cross-check* of that band, not the source, so the
enum value is `"hko_station"`. `DESIGN_stages_3_6.md` §3.2 inherited the label `"hko_normals"` in its
`quantity → anchorSource` map, and §3.2/§6 still carry the old enum and the "HKO normals (air)"
warning text; the corrected enum is `"hko_station"` and the engineer should apply it to that doc and
to `_anchoring_block` in the same commit (P0-A5, item 1), so code, tests and docs agree. The
`quantity` field stays `"HKO_air"` — it names the physical quantity, which is unaffected by the
source-label fix.

*Raised by the architect; mirrored here so the PRD and the design agree.*

---

## 3. Deliverable 3 — Prototype demo readiness: what else is required

Grounded in the brief (`_extracted_docx.txt`: the 27-task timeline, the judging rubric lines 44–104,
the "interactive prototype: edit land use → deficit in seconds" task 19) and the project's own
sources (`STATUS.md` Working / Stages / Known gaps; `RUNG_AND_BUSINESS.md`; `README.md` Limitations).
Effort signal: **S** ≈ hours, **M** ≈ a day, **L** ≈ multi-day.

### 3.1 Prioritised table

| # | Requirement | Why the demo needs it | What exists today | Effort | Blocker? |
|---|---|---|---|---|---|
| **B1** | **Frontend surface for the new backend capabilities** — heat-field, `ventilation/corridors`, `drainage/routed`, `scenario/compare`. | The 5-min pitch must *show* the strongest work. Today the backend serves SVF, corridors, routed mass-balance and the weighted comparison, but the Cesium/React UI calls only `getScene/getTerrain/listPlans/listInterventions/ai-plan/ledger/sea-level/publishedScenarios/buildingFit`. These four routes are **built but not showable**. | Backend endpoints exist and are tested (`scenes.py:764,867,945,988`); frontend has no caller. | **L** | **YES** |
| **B2** | **Render the heat field at a summer solar day.** | The heat overlay sidecar renders `solarDayOfYear: 355` (**Dec 21**). A heat/heatwave demo — and a July anchor — need a summer day, or the shadow geometry undercuts the story and the anchor season disagrees with the picture. | `make_heatmap.py` runs a fixed `solarDayOfYear`; `--heatwave` stretches the display scale only. Design moves the **`/heat-field`** default `355 → 196` (15 July) and leaves `/design-metrics` and the scenario shade axis at 355 (P2-A2). | **S** | **YES** (integrity) |
| **B3** | **`place` / `reset` are not scoreable.** | The brief's headline loop is "edit land use → deficit in seconds". `place` (new massing on unbuilt land) and `reset` are the AI's most-used ops and both return under `unsupportedOps`. The demo's central interaction is half-missing. | `scenario.py` lists them as unsupported with a reason (`STATUS.md` gap 7). | **L** | **YES** |
| **B4** | **Scenario comparison is demonstrable in the UI.** | The brief's core pitch is *cumulative* comparison across projects. `compare()` refuses without weights (correctly) — but the demo must let a planner state weights and see the ranked list with the three axes beside it. Needs a small UI on top of `POST .../scenario/compare`. | Endpoint exists; no UI, no weight control. | **M** | **YES** |
| **B5** | **Wet-grid "pour" animation / overlay clarity.** | The brief (§3e) asks for an animated water "pour"; the wet grid is the headline visual. | Wet-grid overlay renders (level-pool); no pour animation. | **M** | No (nice-to-have) |
| **B6** | **Three published plans demonstrably toggled.** | Task 9 / rung C: three real CEDD programmes encoded. | Encoded (`RUNG_AND_BUSINESS.md`); needs to be *toggled* in the UI in one click. | **S** | No |
| **B7** | **Sea-level scenario visible.** | Task 21 / rung C: "the same storm, a worse outcome" — the brief calls it the most memorable part. | `GET /sea-level` exists and is consumed by `ImpactPanel`; result is "inert at this site" (honest). | **S** | No |
| **B8** | **Verify the Curve Number table (HSG for HSK/HT).** | The single largest lever on every number; a judge will ask. | Assumed HSG D; ±20 % band published (`RUNG_AND_BUSINESS.md`). | **M** | No (honesty, not demo) |
| **B9** | **Backup demo recording (task 26) + pitch deck (task 23).** | Hard deadlines: feature freeze 14 Oct, submit 18 Oct, pitch 24 Oct. | Not built. | **M** | No (process) |
| **B10** | **Reconcile the two study areas in the pitch.** | The brief's own task 3 says San Tin / Tai Sang Wai / Mai Po; the repo locked HSK/HT; `RUNG_AND_BUSINESS.md` frames the business around Deep Bay. A judge who reads the brief will notice. | README says HSK/HT; RUNG says Deep Bay. | **S** | No (framing) |
| **B11** | **Frontend renderer unit coverage.** | `STATUS.md` gap 4: overlay layers and the online-asset loader are untested; a green `npm test` does not mean the map renders. | 46 unit tests over other areas; two browser suites need live servers. | **M** | No |

### 3.2 Blockers vs nice-to-have

**Blockers (the demo cannot be shown convincingly without them):**

1. **B1 — no frontend surface for the four new backend routes.** The backend's best evidence (a
   mass-balance that *raises* on a leak, an SVF that reads 0.9913 vs 0.0, corridors, a weighted
   comparison that refuses to grade) is invisible in the product. This is the single largest gap and
   it is a **frontend** gap, not a backend one.
2. **B2 — December solar scene under a July anchor.** A heat demo rendered in mid-winter is a
   self-inflicted inconsistency a judge can catch, and it directly contradicts P1-A2/P2-A2 above.
3. **B3 — `place`/`reset` unscoreable.** The brief's central interaction is "edit land use → deficit in
   seconds"; two of the most-used edits cannot move the number.
4. **B4 — comparison not demonstrable.** The pitch's differentiator is cumulative comparison; without
   a UI to state weights and show the ranked list, the differentiator is a `curl` command.

**Nice-to-have:** B5 (pour animation), B6 (one-click plan toggle), B7 (sea-level visible — already
consumed), B8 (CN verification — honesty), B9 (deck/recording — process), B10 (area framing),
B11 (renderer tests).

### 3.3 Brief items **not built** (say so, don't hide it)

Per `RUNG_AND_BUSINESS.md` rung C and `_extracted_docx.txt`:

- **Typhoon surge + jacking effect** (task 12) — needs an Hato tide series and an outfall network we
  do not have.
- **Perception encoder** (task 16) — used ESA WorldCover instead; declared as a rung B/C boundary.
- **Generative emulator** (task 17) — the ledger runs in 0.9 s; there is nothing to make faster.
- **Kappa validation vs recorded flood zones** (task 15) — no 2017 dataset; the brief accepts
  "validate with the ladder and report the disagreement".
- **InVEST / UFRM** (task 8) — implemented SCS-CN from TR-55 Table 2-2 directly; documented deviation.
- **CEDD LiDAR bare-earth DTM** (task 13) — request-gated; used Open-Meteo SRTM-derived elevation.

### 3.4 Built but **not showable** (the honest list)

- The **heat-field** endpoint (SVF + blend + anchoring) — no frontend caller.
- The **ventilation corridors** endpoint — no frontend caller.
- The **routed drainage** endpoint (fill-and-spill + mass balance + HAND) — no frontend caller.
- The **scenario comparison** endpoint (`compare()`) — no frontend caller, no weight UI.
- The **per-cell FAD** field — served only inside corridor responses.
- The **routed depth artefact** `routed_depth_hsk.npy` — sits beside the headline deficit, not in the
  demo (`STATUS.md` gap 8).

### 3.5 What is already covered (do not pad)

The chain closes: real HSK/HT terrain + ESA land cover + a 2,639-envelope editable city model render
in Cesium with no Ion token; the **buffer deficit is served as a headline** (3,170,180 m³, pinned by
test); **AI NL → grounded 3D edits** are live (local Ollama, no key); **three published CEDD
programmes** are encoded; **sea-level scenarios** run against official HKO projections; the wet-grid
and heatmap overlays render; **393 backend + 104 frontend tests** are green. That is rung C, and it is
substantially more than the demo strictly needs.

---

## 4. Evidence appendix — raw fetch log (2026-10-09)

```
1. archive-api.open-meteo.com/v1/archive  (July 2015-2024, centroid)
   HTTP 200  bytes=93151  grid=(22.460455, 113.96892) elev=7 m
   July mean-of-daily-max = 30.99 C ; hottest daily max = 36.0 C

2. data.weather.gov.hk opendata CLMTEMP/CLMMAXT/CLMMINT station=LFS
   HTTP 200  bytes≈432k each ; rows=14945 each
   LFS July 2015-2024: min 26.4 / mean 28.96 / max 32.7 ; extremes 23.3 .. 37.0

3. www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm
   HTTP 200  bytes=50291  Table 4 July: max 31.6 / mean 28.9 / min 26.9

4. landsatlook.usgs.gov/.../landsat-c2l2-st/tile/122/044/2020/
   HTTP 200 but body = "Login - EROS Registration System"  -> UNREACHABLE

5. ladsweb.modaps.eosdis.nasa.gov/.../MOD11A1/...hdf
   HTTP 200 but content-type=text/html (login)              -> UNREACHABLE

6. api.open-meteo.com/v1/elevation                          HTTP 200 (host already used)
```

---

*This is a screening-level increment. The heat field stays a relative index; the anchor states the
air-temperature band it is read against and never becomes a temperature; no output carries a verdict.*
