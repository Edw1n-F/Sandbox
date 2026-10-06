# Architecture

Design decisions and the reasoning behind them. The
[README](../README.md) has the condensed version and the local setup guide; this
document is for whoever changes something and needs to know what would break.

---

## The shape of it

A 3D planning sandbox. You load a study area, put a development plan on it, and
see where water collects and where heat is trapped.

The interesting constraint is not technical. It is that **the team has to be
able to run this on a laptop, with no Docker, no database and no API keys, on
the day.** Almost every decision below follows from that.

```
┌─ open data (fetch_dem.py, fetch_landcover.py) ──────────────┐
│  Open-Meteo/SRTM 1-arcsec elevation   ESA WorldCover 10 m  │
│  verified against control points     CC BY 4.0, COG window │
└──────────────────────────┬─────────────────────────────────┘
                           │ writes
                    backend/data/*.npy, *.png, *.json
                           │ read by
┌──────────────────────────▼─────────────────────────────────┐
│ scripts/  _overlay_common  build_grid  SCS-CN  level-pool   │
│           make_wetgrid   make_heatmap                      │
│           each attaches its output to the scene            │
└──────────────────────────┬─────────────────────────────────┘
                           │ file store (STORE_MODE=file)
┌──────────────────────────▼─────────────────────────────────┐
│ app/routers   HTTP only: parse, delegate, serialise        │
│ app/schemas   the wire contract + RowModel translation     │
│ app/services  cascade, bbox repair, URL resolution         │
│ app/db.py     the ONLY module that knows storage is files  │
└──────────────────────────┬─────────────────────────────────┘
                           │ /api, /static
┌──────────────────────────▼─────────────────────────────────┐
│ frontend  api/ (typed fetch)  state/ (Zustand)             │
│           view/ (Cesium)  components/ (MUI panel)          │
│           no filesystem, no DB, no Cesium above this line   │
└────────────────────────────────────────────────────────────┘
```

Each arrow is an HTTP call or a file write. The frontend never touches the
filesystem. The backend never knows what Cesium is.

---

## Decision 1 — Store mode is configuration, not architecture

**Decision.** `STORE_MODE=file` (JSON on disk) today, `postgis` is the target.

**Why.** The host has no Docker, no Postgres, no Homebrew. The spec's target is
PostGIS + MinIO.

**How it's kept honest.** The storage boundary is drawn at exactly **two
modules**: `app/db.py` and `app/services/scene_service.py`. No route, no schema,
no frontend code knows which store is active. The 11 contract tests run against
file mode today and run unchanged against PostGIS when it exists.

**Why it matters.** If the routes knew about storage, "temporarily" would have
become permanent by the third endpoint. The seam is what makes the PostGIS
migration a swap rather than a rewrite.

---

## Decision 2 — Degrees on the wire, metres in the maths

**Decision.** Every coordinate crossing the API is EPSG:4326. Exactly one
function converts degrees to metres: `build_grid()` in
`app/services/geo_import.py`.

**Why.** The spec mandates EPSG:4326, and Cesium and GeoJSON both speak it. But
degrees are not a unit of length, and summing rainfall over "a degree" is
meaningless.

`build_grid()` uses a local equirectangular approximation about the extent
centre. At a 3 km extent the error against HK1980 Grid is far below the 5 m cell
size. Every raster script calls it; nothing else converts units.

**Where it will need to change.** Volume reporting should move to HK1980 Grid
(EPSG:2326). The comment at the choke point says so.

---

## Decision 3 — `RowModel`: one translation point for the wire

**Decision.** Storage uses `id` (the spec's DDL). The API uses `sceneId` /
`planId`. The translation lives in `RowModel.from_row`.

**Why an alias and not a rename.** The DDL is part of the specification.
Renaming the storage column to satisfy the API would have quietly diverged from
it.

**Why one function.** The alternative — `@computed_field`, or `Field(alias=...)`
scattered across models — works right up until something bypasses it, and then
storage IDs leak onto the wire and nobody notices. `extra="forbid"` on the
response models is what turned that from a silent leak into a startup error
during the build.

---

## Decision 4 — The spec contradicts itself in six places

`技术文档` §3 and §4 disagree. Each conflict is resolved in **one named place**
rather than by picking a reading and hoping:

| Conflict | Resolution | Where |
|---|---|---|
| §3.2.2 `bbox` example has 2 coordinates — not a valid ring | Normalised to a closed ring on ingress | `schemas/scene.py`, `services/scene_service.py` |
| §4.2.1 `defaultCamera` has `lon`/`lat`; §3.2.1 does not | Model is the **union**; `lon`/`lat` default to the scene centre | `schemas/scene.py` |
| DB `id` vs API `sceneId`/`planId` | Alias via `RowModel` | `schemas/scene.py` |
| `plan_type` CHECK allows 5 values; nothing creates `economic`/`environmental` | Left legal and unreachable, noted in the model | `models/plan.py` |
| Both docs reference a `migrations/` directory that does not exist | **Still missing.** Unnecessary in file mode. Flagged, not faked | — |
| §3.5 mandates both a FastAPI `detail` array and `{"error": {...}}` | **Both emitted.** Frontend has one error branch | `main.py` |

The pattern: where the spec is ambiguous, the code is permissive on input and
strict on output, and the decision is written down where the next reader looks.

---

## Decision 5 — A data source that cannot prove itself is rejected

**Decision.** `fetch_dem.py` asserts Everest ≈ 8,000–9,500 m and the open ocean
≈ 0 m **before it writes a single file**.

**Why.** The first source tried, AWS `elevation-tiles-prod`, returns HTTP 200
with plausible-looking PNGs whose pixels are not terrarium-encoded. Everest
decoded to ~1,000,000 m. It would have produced a beautiful, silent, wrong
landscape.

**The general rule.** Open data is not trustworthy by virtue of being open. The
test is cheap: two control points, a known answer, abort on mismatch. Detail in
[DATA_SOURCES.md](DATA_SOURCES.md#rejected-aws-elevation-tiles-prod-terrarium).

A second instance of the same principle: the first land-cover read filled 21 % of
the study area with a default class because it read only one of two tiles.
Nodata quietly becoming a real class is the same failure — confident, wrong, and
invisible in a picture.

---

## Decision 6 — Continuous fields are images, not polygons

**Decision.** Wet grid and heat overlays are `SingleTileImageryProvider` rasters.
Extrusion is reserved for discrete features (projects, interventions).

**Why.** One georeferenced 256×256 PNG is ~16 KB. The same field as polygons
would be thousands of features, tens of MB, and a different frame rate.

**The async trap.** `SingleTileImageryProvider` starts with no image. Adding it
to a scene in that state makes Cesium's render loop throw a bare
`DeveloperError` with an empty stack, which takes the React tree down with it.
Hence `SingleTileImageryProvider.fromUrl()`, which resolves only once the image
is ready. See the `createOverlayLayer` docstring.

---

## Decision 7 — Real terrain without a token or a tile server

**Decision.** Elevation is published as a mapbox terrain-RGB raster and served
through `CustomHeightmapTerrainProvider`.

**Why.** Cesium's stock `CesiumTerrainProvider` wants quantized-mesh from a
tiling service. The Lands Dept quantized-mesh tiles need approval we do not
have, and Cesium ion needs a token we deliberately do not use. The study area is
2.4 × 3.3 km — a single 1.6 KB image, not a tile pyramid.

`CustomHeightmapTerrainProvider` is a first-class Cesium API, not a workaround.
Returning `undefined` from its callback is what makes tiles outside the raster
fall back to their parent, so flying away from the study area shows flat ground
instead of a stretched slab.

---

## Decision 8 — Every viewer touch is guarded by `isLive()`

**Decision.** One predicate, used everywhere the Cesium `Viewer` is touched.

**Why.** React StrictMode mounts, unmounts and remounts every effect in
development. The create-effect's cleanup calls `viewer.destroy()`; the *other*
effects' cleanups then run against the destroyed instance, and
`viewer.imageryLayers` dereferences an internal `destroy()` already released.

**Also learned the hard way:** with `requestRenderMode: true`, a layer whose
texture finishes loading asynchronously never paints until something asks for a
frame. Hence `scene.requestRender()` after each overlay is added.

---

## Decision 9 — Honesty is a feature, not a disclaimer

**Decision.**

- Every raster writes a `.json` sidecar listing every constant, assumption and
  source, *including which data source it actually used*.
- The viewer reports a failed overlay in the panel.
- The UI states the prototype limitation on screen.
- The provenance string is **derived at runtime**, never hard-coded.

**Why the last one matters.** The provenance string *was* hard-coded. When real
ESA WorldCover data replaced the synthetic land class, the sidecar kept claiming
`SYNTHETIC PLACEHOLDER` — a stale label contradicting the data beside it. A
label that can go stale without anyone noticing is worse than no label, so the
scripts now report `ov.landcover_source` / `ov.terrain_source` from the object
that produced the data.

**Why any of it.** Three separate bugs presented as "the toggle does nothing" —
an unregistered Vite path alias, a StrictMode-destroyed viewer, and an image
that failed to decode. None was diagnosable from the code. Making failure
visible is what turned a guess into a diagnosis.

---

## Decision 10 — A clean `tsc` is not evidence the frontend works

**Decision.** Frontend changes are verified by driving a real headless-Chromium
render and asserting zero console errors, not by the compiler.

**Why.** Three runtime bugs passed `tsc --noEmit` cleanly:

1. `tsconfig.json` declared `paths: { "@/*": ["src/*"] }` but `vite.config.ts`
   never registered a matching `resolve.alias`. TypeScript resolved the imports;
   Vite did not. Five modules failed at runtime — while the type checker was
   green.
2. The StrictMode-destroyed viewer (Decision 8).
3. `/static` missing from the Vite dev proxy, so the overlay PNG request returned
   `index.html`.

The compiler validates types. It does not validate module resolution, lifecycle
ordering, or that a URL serves the bytes you expect.

---

## Deliberately not used

| Not used | Why |
|---|---|
| Docker | Cannot be provisioned; the demo must run natively |
| PostgreSQL / PostGIS | The documented target. `STORE_MODE=file` covers the same contract (Decision 1) |
| MinIO | Rasters are small enough for the file store, and `/static` serves them |
| `alembic` / `migrations/` | Referenced by both spec documents and **still missing**. Unnecessary in file mode — flagged, not faked |
| GDAL | `rasterio`'s wheels bundle it, and only one script needs it |
| A physics engine | Out of scope for this phase; named in the task plan |
| Cesium Ion | Requires a token; the app runs with zero credentials instead |
| A paid elevation API | Open-Meteo and WorldCover are open, key-less, and verified |

---

## Where the seams are

If you are changing something, these are the places that must move together:

| Change | Touches |
|---|---|
| New endpoint | `routers/` + `schemas/` + a contract test |
| New raster field | `scripts/` + a field in the terrain/overlay response + a UI toggle |
| Swapping to PostGIS | `app/db.py` + `app/services/scene_service.py` **only** |
| A different study area | `data/scene_hsk.json`, then re-run all four scripts |
| A better DEM | Drop in `dem_hsk.npy` + set `terrain_source`. Nothing else |
| Real land cover | Drop in `landclass_hsk.npy`. Nothing else |
| A new front-end layer | `view/` for rendering, `components/LayerToggle.tsx` for the switch |

---

## Testing strategy

Two suites, each guarding a different class of mistake.

**`tests/test_contract.py` (11 tests)** — the API is a contract with an external
consumer, so the contract is asserted: every endpoint, the cascade delete, the
`sceneId` alias, bbox ring closure, and that the error envelope carries both
required shapes.

**`tests/test_constants.py` (7 tests)** — guards the two constants the source
documents say *fail silently*: the SCS-CN form and the mCD/mPD datum offset.
Neither raises an error when wrong; both produce a plausible number. One of them
was caught in practice — `S = 25400/CN − 2540` returns negative retention above
CN ≈ 91, which made `Q`'s denominator negative and produced a first run
reporting **5.4 × 10¹⁸ m³** of runoff while rendering a plausible image. Also
covers the ADR-015 double-counting partition and ADR-017 monotonicity.

**Frontend** — `npx tsc --noEmit`, plus a headless-Chromium render asserting zero
console errors (Decision 10). The render is the test that would have caught all
three runtime bugs.

---

## Related documents

- [`README.md`](../README.md) — setup guide, tech stack, troubleshooting
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — provenance, licences, rejected sources
- [`OCT6_STATUS.md`](OCT6_STATUS.md) — dated evidence log; the real /
  placeholder / assumed split
- [`../CLAUDE.md`](../CLAUDE.md) — repo conventions
