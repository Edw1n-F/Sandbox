> **This is a point-in-time snapshot taken at the end of 6 Oct 2026.** It is kept
> unedited because the reasoning matters, but parts of it have since been
> overtaken by events. For current state see [`README.md`](../README.md);
> for the reasoning behind the current design see
> [`ARCHITECTURE.md`](ARCHITECTURE.md).
>
>


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

### 2.2 Placeholder → Real (updated Oct 9 2026)

| Item | Previous status | Current status |
|---|---|---|
| `terrainUrl` | `null` — flat `EllipsoidTerrainProvider`, synthetic DEM inside wetgrid | **Real** — `dem_terrain_rgb.png` from HK Lands Dept 6-NW-12D `TERRAIN(TB)` glTF (148,644 vertices, EPSG:2326 → EPSG:4326 via pyproj). Coverage ~750 m × 600 m; outside that extent filled 0 m (flat). Run `scripts/ingest_terrain_gltf.py` to regenerate. |
| `cityModelUrl` | Procedural massing from ESA WorldCover `paved` class | **Real** — 390 building envelopes extracted from 6-NW-12D `BUILDING/` glTFs. Footprints are AABB rectangles; heights are glTF mesh Z-axis max. `provisional=false`, `footprint_source="LANDS_DEPT_6NW12D_GLTF"`. Run `scripts/ingest_buildings_gltf.py` to regenerate. |

### 2.2a Placeholder → Real (updated Oct 10 2026)

| Item | Previous status | Current status |
|---|---|---|
| `cityModel3DUrl` (3D Tiles) | Built but broken — buildings floating 8–18 m above terrain due to two bugs | **Real and grounded** — 390 textured building meshes from 6-NW-12D `BUILDING/` glTFs, positioned correctly on the terrain surface. See bugs fixed below. |
| Terrain surface height | `demProvider.ts` using `tileXYToNativeRectangle` (returned Web Mercator **metres** treated as radians) → `toDegrees` produced ~7×10⁸ ° → every tile sampled outside DEM range → **0 m flat everywhere** | Fixed: `tileXYToRectangle` returns radians directly; terrain now renders real Lands Dept elevation across the covered area. |

**Three rendering bugs fixed on Oct 10:**

1. **Axis confusion (buildings rotated)** — `tileset.json` lacked `"gltfUpAxis": "Z"`. Cesium applied its default Y→Z-up correction, rotating building vertices and scrambling North/Up channels. Fix: added `"gltfUpAxis": "Z"` to the `asset` block in both `build_3dtiles.py` template and the regenerated `tileset.json`.

2. **Buildings floating above terrain (elevation mismatch)** — Buildings were placed at their HK1980 absolute elevation (8–18 m mPD), but the DEM only covers 7.5 % of the study area; uncovered cells are 0 m. Fix: `build_3dtiles.py` now bilinearly samples `dem_hsk.npy` at each building's footprint centre and shifts the entire building vertically so its ground floor matches the rendered terrain surface.

3. **Terrain rendered as 0 m flat (unit confusion in demProvider)** — `WebMercatorTilingScheme.tileXYToNativeRectangle` returns EPSG:3857 coordinates in **metres** (Hong Kong X ≈ 12,694,000 m), not radians. Passing those values to `CesiumMath.toDegrees` produced longitudes of ~7 × 10⁸ °, far outside the DEM bbox; `sampleAt` returned 0 for every tile. Fix: one-line change in `frontend/src/view/demProvider.ts` line 183 — `tileXYToNativeRectangle` → `tileXYToRectangle`, which returns radians directly.

### 2.2 Still placeholder

| Item | What it really is | Why acceptable today |
|---|---|---|
| `imageryUrl` = OSM tile template | Real online tiles | Spec §4.3 sanctions "在线 Cesium 影像底图占位跑通链路" |
| `wetGridUrl` / `heatmapUrl` | Real PNGs over real terrain (lands-dept-6nw12d) + ESA WorldCover land cover | Terrain is now real (see above). Land cover source is ESA WorldCover, not a site survey. Both say so in sidecar metadata. |
| `envParams` on the scene | Static, location-derived defaults (ADR-024) | Per-run inputs belong on a `simulation_run` row, which does not exist yet. The sliders do not persist to the API — see §4. |
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
- Real (as of Oct 9 2026): the **elevation surface** — `dem_hsk.npy` is now
  from HK Lands Dept 6-NW-12D via `ingest_terrain_gltf.py`. Cells outside the
  glTF tile extent are 0 m (flat), so the wet-grid result still reads as
  screening for those areas. The sidecar now reports `lands-dept-6nw12d`.
- Verdict: a **screening view of where water would collect** over the real DEM.
  Not a flood prediction. The sidecar says so.

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
7. **The overlays rendered nothing, three separate causes.** Each one alone
   looks like "the toggle just doesn't work", and all three were present at once:
   - **React StrictMode destroyed the viewer out from under its siblings.** The
     create-effect's cleanup calls `viewer.destroy()`; the other effects'
     cleanups then ran against the destroyed instance, and `viewer.imageryLayers`
     dereferences an internal that `destroy()` already released. Fixed with a
     single `isLive()` predicate guarding every touch of the viewer.
   - **`SingleTileImageryProvider` was added before its image had loaded.** The
     provider starts empty; adding such a layer makes Cesium's render loop throw
     a bare `DeveloperError` with an empty stack, which took the whole React tree
     down. Now built with `SingleTileImageryProvider.fromUrl()`, which resolves
     only once the image is ready.
   - **`/static` was not in the Vite dev proxy.** Overlay URLs are same-origin
     relative, correct for production, but the dev server only forwarded `/api`
     — so the PNG request returned `index.html`, the image failed to decode, and
     the layer silently never appeared.
   A fourth, quieter issue: with `requestRenderMode: true`, a layer whose texture
   finishes loading asynchronously never paints until something asks for a
   frame, so `scene.requestRender()` is required after adding an overlay.
8. **Cesium 1.146 API drift.** `CesiumTerrainProvider` no longer accepts a `url`;
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
| ~~No browser-render verification~~ | ✅ | **Done** — headless Chromium, 1440x860, zero console errors, with all four layers on. |
| **Overlay failure is invisible in Cesium** | ✅ | A failed imagery layer looks identical to one that was never switched on, so `LayerToggle` now reports load failure in the panel. |

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

---

## 10. 6-NW-12D data inventory (audited Oct 10 2026)

The `backend/6-NW-12D/` directory contains seven asset classes from the HK Lands
Department glTF dataset. Status against the rendering pipeline:

| Class | Files | Bin size | Vertices | Normals | Ingested | Output |
|---|---|---|---|---|---|---|
| `TERRAIN(TB)` | 1 | 5 MB | 148 K | ✅ | ✅ | `dem_terrain_rgb.png` + `terrain_texture_hsk.jpg` |
| `BUILDING` | 390 | 10 MB | ~270 K total | ✅ | ✅ | `rasters/3dtiles/buildings.b3dm` |
| `WATERBODY` | 6 | ~0 MB | ~240 | ✅ | ✅ | `rasters/waterbody_hsk.json` (GeoJSON) |
| `INFRASTRUCTURE` | 7 | 1.2 MB | ~155 K | ✅ | ✅ | `rasters/infrastructure_hsk.json` (GeoJSON) |
| `INFRASTRUCTURE(TB)` | 1 | 3.5 MB | 807 K | ✅ | ❌ | — |
| `VEGETATION(TB)` | 1 | 125 MB | 5.4 M (shared) | ❌ | ❌ | — |
| `GENERIC` | 8 | 1.18 GB | ~110 M / file | ❌ | ❌ | — |

### 10.1 INFRASTRUCTURE(TB) — highest-value pending work

Single glTF, 807 K vertices, 3.5 MB, 8 textures. Contains terrain-scale
infrastructure objects (road bridges, retaining walls, major road surfaces) that
do not exist as discrete INFRASTRUCTURE items. Structure is identical to
BUILDING: one mesh per subdir, NORMAL + POSITION + TEXCOORD_0, column-major
HK1980 node matrix. `build_3dtiles.py` can ingest it with minor path changes.

**Effort estimate:** < 1 hour. Wire a second tileset alongside buildings, add a
`infra_3d_path` field to the scene record, and a toggle in `LayerVisibility`.

### 10.2 VEGETATION(TB) — medium effort, significant visual impact

Single glTF, 145 primitives sharing one 5.4 M vertex buffer, 125 MB bin, 145
textures. Photogrammetry-reconstructed tree canopy — no normals (unlit mesh).

The shared-vertex layout means splitting into per-tree b3dm tiles requires
slicing by index range rather than by node. LOD is mandatory: at 125 MB source
the unsplit b3dm would be ~330 MB and unrenderable in a browser.

**Recommended approach:** extract each primitive's index range → compute its
ENU bbox → assign to a spatial grid → write one b3dm per grid cell (≤ 500 K
verts each) → build a `tileset.json` with a two-level hierarchy. Estimated
effort: 4–6 hours.

**Alternative (lower effort, lower fidelity):** derive tree positions from land
cover or WATERBODY geometry centroids; render as `BillboardCollection` sprites
(face-camera PNG). Gives visual depth at ~2 h effort but is not the real data.

**Why trees look 2D today:** the terrain texture (`terrain_texture_hsk.jpg`) is
an orthophoto draped on the surface. Tree crowns in an orthophoto are inherently
top-down flat. No 3D vegetation is currently rendered — the VEGETATION(TB) data
exists but is not yet ingested.

### 10.3 GENERIC — not recommended for current phase

8 glTF files, total ~1.18 GB bin, each ~100–140 M vertices. Dense photogrammetry
of the entire study area (ground, roads, loose objects). No normals. Rendering
without a multi-level LOD tiling pipeline (e.g. py3dtiles or Cesium ion) is
not feasible. Defer until a purpose-built tiling step can be added.

### 10.4 Summary — next rendering improvements in priority order

1. **Fix building normals** (in-tree, no new data): `build_3dtiles.py` copies
   source normals in HK1980 local space without rotating them to ENU. PBR
   shading is therefore incorrect — walls that face East may light as if facing
   Up. Fix: recompute normals from ENU geometry after the coordinate transform.
   Also add `"doubleSided": true` to avoid holes from winding-order flips.

2. **Enable Cesium lighting + shadows** (frontend only, no data): set
   `viewer.scene.globe.enableLighting = true`, `viewer.shadows = true`,
   `viewer.shadowMap.softShadows = true` in `SandboxViewer.tsx`. Instant depth
   improvement, zero data work.

3. **Ingest INFRASTRUCTURE(TB)** (< 1 h): adds real 3D road bridges and
   retaining walls. Follow the BUILDING pipeline exactly.

4. **Ingest VEGETATION(TB) as 3D Tiles** (4–6 h): real textured tree canopy
   from Lands Dept. Requires per-primitive splitting and a 2-level LOD tileset.
