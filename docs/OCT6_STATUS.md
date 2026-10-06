# OCT6_STATUS — what actually landed on 6 Oct 2026

**This is the evidence base for the 11 Oct rung declaration.** It records what is
*real*, what is a *placeholder*, and what is *assumed*, so the rung statement is
a tick-box rather than a guess.

Repo: `T1` · Spec of record: `docs/技术文档.docx` + `CLAUDE.md`

---

## 1. The three decisions, written down so they stop being re-litigated

| # | Decision | Chosen | Why | Where it lives |
|---|---|---|---|---|
| **D1** | What "done" means today | **Build for Rung 2, guarantee Rung 1** | `技术文档` §2.3.1/§4.1 defers wet grid + heatmap; the team brief includes them. Ordering the work so Rung 1 is shippable at any point removes the argument. | this file |
| **D2** | Store mode | **`STORE_MODE=file`** | No Docker, no Postgres, no Homebrew on this host. §4.3 of our own spec explicitly sanctions a placeholder path. The swap to PostGIS is one module. | `backend/.env`, `app/config.py` |
| **D3** | Bounding box | **Hung Shui Kiu / Ha Tsuen (HSK/HT)** | Strongest public evidence base (approved EIA, OZP S/HSK/3, LegCo study). The Deep Bay / San Tin anchor in the 27-task plan is deliberately **not** used. | `backend/data/scene_hsk.json` |

**D1 detail, and the gap that must be closed:** §2.3.1 says wet grid and heatmap
"留待物理引擎接入后" (deferred until the physics engine is integrated) and §4.1
reserves their columns without specifying a write path. The team plan asks for
both. This repo therefore ships the **fields and the render path** as real, and
the **numbers** as a labelled screening estimate — see §4.

---

## 2. Real vs placeholder vs assumed

### 2.1 Real — implemented, tested, working

| Item | Evidence |
|---|---|
| **Full REST contract** — all 9 endpoints from §3 | `GET /openapi.json` lists every one; `backend/tests/test_contract.py` asserts it |
| **File-mode CRUD + `scene → plan → intervention` cascade** | 11/11 contract tests pass, including a cascade test that deletes a scene and asserts its plan 404s |
| **camelCase wire contract with `sceneId`/`planId` aliases** | `RowModel.from_row` is the single translation point; a test asserts storage `id` never leaks to the wire |
| **bbox normalisation to a closed ring** | Validator repairs §3.2.2's 2-coordinate example. **This found and fixed a real bug** — see §5. |
| **One error envelope everywhere** (422/404, `detail` + `error`) | Test asserts both shapes; §3.5's two contradictory requirements are both satisfied |
| **`/static` mount so overlay URLs resolve over HTTP** | `StaticFiles` on `backend/data` at `/static` |
| **Locked study area with machine-derived provenance** | `backend/data/scene_hsk.json` |
| **No Cesium Ion dependency** | `baseLayer:false` + explicit OpenStreetMap layer. Runs with **zero credentials** |
| **End-to-end chain** — frontend boots, fetches scene + terrain + plans, renders | **Verified by a real headless-Chromium render** (1440x860, zero console errors) plus clean `tsc --noEmit` |
| **Wet grid + heatmap overlays** — generated, served, togglable | `backend/scripts/make_wetgrid.py`, `make_heatmap.py`; PNGs served at `/static/rasters/`; both scripts write a `.json` sidecar recording every assumption |
| **Wet-grid engine (screening)** — CN runoff → storage → level-pool fill | Bisection on the water-surface elevation; **monotonic in rainfall** (0/200/450/800 mm → 0/8.7/17.0/27.2 % wet area) |
| **18 automated tests** | 11 contract + 7 constants, both green |

### 2.2 Placeholder — deliberately, with the substitution named

| Item | What it really is | Why acceptable today |
|---|---|---|
| `terrainUrl` = **`null`** | `EllipsoidTerrainProvider` (a flat surface) in the viewer; a **synthetic** surface inside the wet-grid script | No DEM exists on this host. The viewer stays flat and says so; the script uses a labelled synthetic surface so the wet grid has a basin to fill. **Neither is a DEM.** |
| `imageryUrl` = OSM tile template | Real online tiles | Spec §4.3 sanctions "在线 Cesium 影像底图占位跑通链路" |
| `wetGridUrl` / `heatmapUrl` | **Real PNGs now generated** — but over a synthetic surface, and the heatmap over a synthetic land class | See §4. The *render path and the computation* are real; the *inputs* are placeholders, and both say so on screen and in the sidecar metadata |
| `envParams` on the scene | Static, location-derived defaults (ADR-024) | Per-run inputs belong on a `simulation_run` row, which does not exist yet. **The sliders do not persist to the API** — see §4. |
| `interventions` = `[]` | Valid empty `FeatureCollection` | §2.3.1 defers interventions. The frontend renders an empty collection without erroring |

### 2.3 Assumed — state these out loud before a judge does

| Assumption | Value used | Source | Confidence | If wrong |
|---|---|---|---|---|
| **Study-area extent** | 113.982–114.005 °E, 22.425–22.455 °N | Derived from OSM place anchors (HSK Station works `way/1374749699`, Ha Tsuen `node/7684821757`), retrieved 6 Oct | **Provisional** | Every raster is misaligned. **Replace with the OZP S/HSK/3 sheet extent.** |
| **Soil hydrologic group** | D (conservative) | HSK/HT is documented as silt/clay | Medium | CN values shift; relative comparison between scenarios survives |
| **Curve Number table** | class × HSG D, hand-authored | SCS TR-55; **awaiting Lia's independent check** | **Unverified** | Runoff volume scales directly with CN |
| **Grid resolution** | 5 m (≈ 667 × 740 cells) | ADR-009 | High | Coarser is faster; finer changes the "precision" of the number |
| **Vertical datum** | mPD throughout | ADR-008 | High | 0.146 m mCD↔mPD offset is half a LiDAR cell — small, but it can flip cells wet/dry |
| **No bare-earth correction** | Lands Dept 5 m DTM is not bare-earth | Lands Dept states it | Certain | **Not used today.** Would bias a deficit if adopted naively |

---

## 3. Contract inconsistencies we fixed (and how)

`技术文档` §3 contradicts §4 in four places. Each is now resolved in code and
recorded here rather than left to be rediscovered:

| # | Issue | Resolution |
|---|---|---|
| 1 | `bbox` example has 2 coordinates — not a valid ring | Normalised to a closed ring; validator repairs short input |
| 2 | `defaultCamera` is `{lon,lat,height,heading,pitch}` in §4.2.1 but `{height,heading,pitch}` in §3.2.1 | Model is the **union**; lon/lat default to the scene centre, so the frontend always receives a complete camera |
| 3 | DB column `id` vs API field `sceneId`/`planId` | **Alias, not a rename.** Storage keeps `id` per the DDL; `RowModel.from_row` translates on the way out |
| 4 | `plan_type` CHECK allows 5 values; nothing creates `economic`/`environmental` | Left legal and unreachable; noted in `models/plan.py` |
| 5 | `migrations/` referenced by both docs, does not exist | **Still missing.** Not needed in file mode. Flagged, not faked |
| 6 | §3.5 mandates both FastAPI's `detail` array and `{"error":{...}}` | **Both are emitted.** The frontend has one error branch |

---

## 4. What the overlays are, and are not

Both overlays exist, compute something, and render. Neither is a result you can
defend as engineering output, for one shared reason: **both inputs are
synthetic.**

**Wet grid** — `backend/scripts/make_wetgrid.py`

- Real: the SCS-CN runoff computation, the storage subtraction, the ADR-015
  double-counting partition, and a level-pool fill solved by bisection on the
  water-surface elevation. That last part matters: it produces a flat water
  surface, so the picture is a pond rather than a uniform smear.
- Placeholder: the **elevation surface**. `terrainUrl` is `null` because no DEM
  is available, so the script uses an explicit synthetic topography — a tilt
  toward the south-east plus a low ridge, with water and wetland cells sunk.
- Verdict: a **screening view of where water would collect over plausible
  topography.** Not a flood prediction. It carries no surveyed elevation, and
  the sidecar says `SCREENING_VIEW_SYNTHETIC_TERRAIN`.

**Heatmap** — `backend/scripts/make_heatmap.py`

- Real: the classification → index → ramp pipeline. This one needs no terrain,
  which is why it is the overlay that can honestly ship today.
- Placeholder: the **land-class grid**, which is a structured placeholder, not
  measured land cover. Drop a real `landclass_hsk.npy` into `backend/data/` and
  it is used automatically.
- Verdict: a **relative heat-exposure index on a 0–1 scale.** Not degrees
  Celsius, and not a modelled air-temperature field. `--heatwave` is a *display*
  stretch, not a scenario, so a screenshot under the preset cannot be mistaken
  for different conditions.

**The sliders still do not recompute anything.** They write `envParams` into the
Zustand store only. Re-running the scripts is a manual step. Presenting this as
"move the slider, the model responds" would be a false claim, so the panel says
what actually happens.

## 5. Bugs found and fixed during the build

Recorded because the second one is the class of failure that makes a map silently
render nothing:

1. **`SceneRead` rejected its own storage row.** Response models inherited
   `extra="forbid"` and read `sceneId`, while storage wrote `id` + extra columns.
   Fixed with `RowModel.from_row` — the translation is now one explicit class
   instead of alias magic spread across the service.
2. **`close_ring` closed nothing.** It compared `coords[0]` against `coords[-1]`
   at the *ring-list* level, so for a single-ring polygon it compared the ring
   against itself and silently returned the ring unclosed. An unclosed GeoJSON
   ring does not render. Both copies (schema + service) are fixed, and
   `test_bbox_ring_is_closed_on_ingress` now guards it.
3. **`DATA_DIR=./data` resolved against the working directory**, so launching
   uvicorn from the repo root pointed the store and the `/static` mount at a
   different directory. Now anchored to `backend/` in `config.py`.
4. **`tsconfig.json` declared `@/*` but `vite.config.ts` never registered it.**
   TypeScript compiled; Vite failed to resolve 5 modules at runtime. The type
   checker was green while the app was broken — a good reminder that `tsc` alone
   does not prove a frontend runs.
5. **The SCS-CN retention constant was the documented ×10 error.** Written as
   `S = 25400/CN − 2540`, which returns *negative* retention for any CN above
   ~91. That makes the denominator of `Q = (P − 0.2S)² / (P + 0.8S)` negative,
   and the first run reported **5.4 × 10¹⁸ m³** of runoff — while rendering a
   perfectly plausible-looking image. The correct form is `− 254`
   (`25.4 × (1000/CN − 10)`). This is precisely the "fails silently" constant
   the source documents warn about, and it is now pinned by
   `tests/test_constants.py` against the TR-55 worked example.
6. **`STATIC_BASE_URL` was empty while the mount is at `/static`.** The API
   returned `/rasters/wetgrid_hsk.png` for a file served at
   `/static/rasters/wetgrid_hsk.png` — a 404 on the last hop of the chain, and
   exactly the class of bug that makes an overlay silently never appear. Fixed
   in `backend/.env`; **the backend must be restarted for `.env` to take
   effect.**
7. **Cesium 1.146 API drift.** `CesiumTerrainProvider` no longer accepts a `url`;
   `ImageryLayer` has no `imageryIndex`; `RectangleGraphics` members are
   `Property` objects, not booleans. All handled. Terrain wiring is reported as
   unsupported rather than cast around — see §6.

---

## 6. Known gaps against the 27-task plan

| Gap | Severity | Note |
|---|---|---|
| **No DEM** | 🔴 | Blocks a real wet grid. Needs explicit permission to download ~290 MB, and the Lands Dept 5 m DTM is **not bare-earth**. |
| **No PostGIS / MinIO** | 🔴 | Cannot be provisioned on this host. `STORE_MODE=file` covers the contract. |
| **No GDAL** | 🟡 | `rasterio` wheels bundle GDAL and would unblock COG conversion without Homebrew — untried. |
| **No `migrations/`** | 🟡 | Referenced by both docs. Unnecessary in file mode. |
| **No gazetted NDA polygon** | 🟡 | Overpass timed out; Nominatim returns points. §2.3 records the exact anchors used. |
| **No CN verification** | 🟡 | Waiting on Lia. |
| **No physics tests** | 🟡 | Monotonicity and the constants are covered; there is no test against a real flood record. |
| **`interventionLayer.ts` written but not wired** | 🟢 | Renders discrete features; no features exist yet. Deliberate, not forgotten. |
| **Overlay PNGs are committed** | 🟡 | ~16 KB each, so harmless, but they should arguably be build artefacts. |
| ~~No browser-render verification~~ | ✅ | **Done** — headless Chromium, 1440x860, zero console errors. |

---

## 7. How to run it

```bash
# backend
cd T1/backend
pip install -r requirements.txt
python scripts/seed_scene.py          # prints the sceneId
python -m uvicorn app.main:app --reload        # :8000
python scripts/make_wetgrid.py --rainfall 450  # -> data/rasters/wetgrid_hsk.png
python scripts/make_heatmap.py                 # -> data/rasters/heatmap_hsk.png
python tests/test_contract.py                  # 11/11
python tests/test_constants.py                 #  7/7

# frontend
cd T1/frontend
npm install
npm run dev                                    # :5173
```

Then set `VITE_SCENE_ID` in `frontend/.env.local` to the id `seed_scene.py`
printed, and **restart the backend** so `STATIC_BASE_URL=/static` is picked up.
The overlay scripts attach their output to the seeded scene automatically, so
`GET /api/scenes/{id}/terrain` starts returning `wetGridUrl` / `heatmapUrl` and
the two overlay switches in the panel become live.

Re-run the scripts after changing the rainfall slider — the sliders do not
trigger a recomputation themselves.

Verify the contract:

```bash
curl -s localhost:8000/health
curl -s localhost:8000/api/scenes/$SCENE_ID
curl -s localhost:8000/api/scenes/$SCENE_ID/terrain
curl -s localhost:8000/api/scenes/$SCENE_ID/plans
curl -s localhost:8000/api/plans/$PLAN_ID/interventions
open http://localhost:8000/docs
```

---

## 8. Environment reality (verified 6 Oct, not assumed)

| Tool | Status | Consequence |
|---|---|---|
| Node 22 / npm 10 | ✅ | frontend fine |
| Python 3.13 managed venv — `fastapi 0.115.0`, `pydantic 2.9.2`, `numpy 2.4.6`, `pillow 12.3.0` | ✅ | everything above runs |
| `backend/.venv` (Python 3.10) | ⚠️ exists, **missing numpy/pillow** | ADR-035: two interpreters is a submission-day trap. **Standardise before 18 Oct.** |
| Cesium **1.146.0** (`package.json` says `^1.121.0`) | ⚠️ | `vite-plugin-cesium@1.2.23` predates it. Currently works; **pin to 1.121.0 if it breaks.** |
| Docker / Postgres / GDAL / Homebrew | ❌ | Why D2 is file mode |
| npm registry | `registry.npmmirror.com` | Fine unless a package lags |

---

## 9. Rung recommendation for 11 Oct

**Declare Rung 2, with the DEM gap stated as the reason it stops there.**

What is genuinely true and testable:

- The §2.3.1 chain closes end-to-end against the spec's contract — verified in a
  real browser, not just by `curl`.
- A locked, provenance-recorded study area, a seeded scene, a real cascade, and
  a single error envelope. 18 automated tests.
- Two overlays that **compute** rather than merely display: CN runoff → storage
  → level-pool fill for the wet grid, and class → index → ramp for the heatmap.
  Both monotonic in their inputs; both shipping a sidecar that records every
  constant, with a test pinning the two that the source documents say fail
  silently.

What is **not** true, and must not be claimed: real terrain, measured land
cover, a verified Curve Number table, a temperature in degrees, or a flood
prediction.

**The single blocker to the next rung is a DEM.** Everything downstream of it —
bare-earth handling, a real wet grid, the Hato validation the 27-task plan
depends on — is waiting on one decision and one download:

1. Permission to fetch `Whole_HK_DTM_5m.zip` (~290 MB), **and**
2. an explicit bare-earth decision, because that product is a surface model,
   not ground.

Wiring `DEM_SOURCE` is already a config switch, and `terrainLoader.ts` already
detects a quantized-mesh endpoint. Neither is the bottleneck. The DEM is.

The other two things worth doing before 18 Oct, in order: standardise the Python
interpreter (§8 — there are still two), and replace the OSM-derived extent with
the OZP S/HSK/3 sheet.
