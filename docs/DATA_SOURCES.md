# Data sources

Every input, where it came from, what licence it carries, and — importantly —
**what was tried and rejected.** The rejected list is the useful half: it stops
the next person spending an afternoon on a source that returns plausible
nonsense.

The generated artefacts (`dem_hsk.json`, `landcover_hsk.json`,
`rasters/*.json`) carry the machine-readable version of all of this. This
document is the human-readable narrative.

---

## Summary

| Layer | Source | Licence | Resolution | Key? | Status |
|---|---|---|---|---|---|
| Elevation (DTM) | Open3Dhk / LandsD 3D Digital Map terrain tiles, self-hosted | Open3Dhk terms | mesh, read onto a 5 m grid | No | **In use** |
| Buildings | Overture Maps fused with Open3Dhk tile blobs | ODbL / CDLA | footprint polygons | No | **In use** |
| Land cover | ESA WorldCover 10 m v200 (2021) + Sentinel-2 NDVI/NDWI + OSM trees | CC BY 4.0 | 10 m | No | **In use** |
| Heat anchor | Landsat 8/9 C2 L2 `ST_B10` (Planetary Computer) | public domain | 30 m, resampled to 5 m | No | **In use** |
| Base imagery | OpenStreetMap standard tiles | ODbL | — | No | **In use** |
| Terrain rendering | mapbox terrain-RGB encoding | open spec | — | — | **In use** |
| Elevation (fallback) | Open-Meteo elevation API | open data | **~90 m** (measured) | No | Superseded — kept for `fetch_dem.py` |

A full refresh is **under 1 MB** of API calls plus the Open3Dhk tiles on disk. No
accounts, no keys, no large downloads.

---

## 1. Elevation — Open3Dhk / LandsD terrain tiles

- **Source:** the Open3Dhk / LandsD 3D Digital Map terrain tiles, committed under
  `backend/model/terrain(TB)/`
- **CRS:** EPSG:2326 (HK1980), Draco glTF vertices linearly interpolated onto the grid
- **Reader:** `backend/scripts/tiles_to_rasters.py`
- **Outputs:** `dem_hsk.npy` (float32 metres), `dem_terrain_rgb.png`, `dem_hsk.json`,
  plus the flow rasters (`flow_filled`, `flow_raw_elev`, `flow_receiver`, `flow_accum`)

Artifact status is `REAL_TERRAIN_FROM_OPEN3DHK_TILES`. The grid is 117 x 148 at 5 m,
elevation 3.41–17.29 m, relief 13.88 m, vertical datum **mPD as supplied by the tile**,
not independently checked.

**The honest caveat.** This is the **surface of the terrain tile mesh**, not a surveyed
bare-earth DTM release. The mesh spacing sets the true resolution; the 5 m grid is the
analysis convention. The tiles carry texture over a draped mesh.

### The 5 m grid is a crop, not the whole study area

The analysis window is **43.5 ha** — the Open3Dhk tile coverage — not the original
≈786 ha OSM-anchored extent. Every figure in this document that quotes a depression
count, a cell count or a total area is measured on that window.

### Why not the 5 m bare-earth DTM release

The Lands Dept 5 m DTM was ruled out early: ~290 MB, needs explicit download
permission, and it is **not bare-earth** — it carries elevated roads and depicts
vegetation at vegetation height, so using it naively would bias any volume figure in
exactly the quantity being computed.

### Superseded: the Open-Meteo fallback

`fetch_dem.py` still exists and still works, but nothing shipped reads its output.
It is kept because it is the only elevation path that needs no local tiles.

- **Endpoint:** `https://api.open-meteo.com/v1/elevation`
- **Upstream models:** a downsampled global elevation model, **~90 m** posting
  (measured on this extract — see "Resolution" below)
- **Auth:** none
- **Fetcher:** `backend/scripts/fetch_dem.py`
- **Outputs:** `dem_hsk.npy` (float32 metres), `dem_terrain_rgb.png`, `dem_hsk.json`

Everything from here to the end of §1 is about that fallback, and is kept because
the two lessons in it — the control-point check and the measured resolution — are
the reason the shipped pipeline asserts its own provenance.

### Control points — the part that matters

The fetcher asserts two known values **before it will write anything**:

| Control | Expected | Observed |
|---|---|---|
| Everest summit (27.9881 N, 86.9250 E) | 8,000–9,500 m | 8,724 m |
| South Atlantic (−20.0, −30.0) | ±50 m | 0.0 m |

A response failing either aborts the run. This exists because of what happened
with the source below.

### Rejected: AWS `elevation-tiles-prod` (terrarium)

**This source is a trap and is documented so nobody retries it.**

It returns HTTP 200 with 256×256 PNGs that look like plausible elevation tiles.
The pixels are **not** terrarium-encoded. Decoding
`(R·65536 + G·256 + B)·0.1 − 32768` gives:

| Control | Expected | Decoded |
|---|---|---|
| Everest | 8,848 m | ~1,000,000 m |
| South Atlantic | 0 m | 792,940 m |
| Sierra Nevada | ~3,000 m | 818,976 m |
| HSK/HT | ~0–200 m | 807,633 m |

Both the `terrarium` **and** `normal` path prefixes fail identically. The red
channel sits pinned near 128 regardless of location, which is the tell.

Had this not been checked it would have produced a smooth, plausible,
completely wrong landscape — the worst possible failure, because nothing would
have looked broken.

### Resolution — measured, not assumed

**The effective resolution is ~90 m, not the 1 arc-second (~30 m) that was
originally assumed here.**

The DEM was requested on a 1 arc-second lattice, which is the textbook answer
for SRTM. The API does not serve SRTM at 1 arc-second: it serves a downsampled
global model. The result was a **78 × 110** array containing only **28 × 37
distinct samples** — every source row and column repeated about three times.

Two things followed from measuring instead of assuming:

1. **The oversampling was removed.** Deduplicating repeated rows and columns is
   lossless by construction — a cell is only dropped when it is byte-equal to its
   predecessor — and it preserves all 84 distinct elevations exactly. The shipped
   grid is 28 × 37, i.e. ~85 m E–W × ~90 m N–S.
2. **Every resolution claim in this repository was corrected.** The earlier text
   said "1 arc-second (~30 m)" in four places. There is no 30 m data here, and
   re-sampling on a finer lattice would only duplicate the same values again.

The general lesson: **an API's advertised resolution is not the resolution you
get.** Count the distinct samples.

> Note the contrast with the AWS bucket, which failed loudly under the same kind
> of check. Here the data was *correct* but the metadata around it was wrong —
> a quieter failure, and one that only a measurement would catch.

### Rate limiting

The API accepts **100 coordinate pairs per request** (more returns HTTP 400 with
a helpful message) and throttles on request *frequency*. The fetcher therefore:

- batches 100 coords per request,
- sleeps 2 s between batches (a full grid takes ~3 minutes),
- honours `Retry-After`, else exponential backoff with jitter, up to 10 attempts,
- **caches every batch to `data/dem_cache/`**, keyed on the coordinates.

That last point matters most: an interrupted run resumes for free, and changing
the resolution misses the cache rather than serving the wrong grid.

---

## 2. Land cover — ESA WorldCover 10 m v200

- **Tiles:** `N21E111`, `N21E114` (3°×3°, named by south-west corner)
- **URL pattern:**
  `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif`
- **Licence:** CC BY 4.0 (ESA) — attribution required
- **Fetcher:** `backend/scripts/fetch_landcover.py`
- **Output:** `landclass_hsk.npy`, `landcover_hsk.json`

Read with a **windowed COG read** via `rasterio`, so only the ~1.5 % of each
3°×3° tile that the study area occupies is transferred. Resampled onto the 5 m
analysis grid with **nearest neighbour** — class codes must never be
interpolated.

### Why WorldCover and not OpenStreetMap

OSM land cover is crowd-sourced and patchy at 10 m in Hong Kong. WorldCover is
a consistent, modelled, per-pixel product with a documented class scheme. For
scenario comparison that consistency is the point: **both scenarios must see
exactly the same ground**, or the comparison is meaningless.

### The tile-boundary bug

*Historical: the current 43.5 ha window lies entirely west of 114 °E, so it no
longer crosses this boundary. The bug below was found on the original ≈786 ha
extent (113.982–114.005 °E).*

The tile boundary is at exactly **114 °E**.

The first implementation read only the tile containing the south-west corner, so
the eastern 21 % of the extent came back as **nodata** — 67,064 cells — which
were then filled with the default class. That silently fabricated ~167 ha of
"paved" ground, and made the runoff figure wrong because of it.

`covering_tiles()` now enumerates every intersecting tile and composites them.
The failure mode is recorded in its docstring, because "nodata quietly becomes
the default class" is the kind of bug that survives review.

### Class mapping — 11 ESA classes to 5

| ESA code | Class | → Ours |
|---|---|---|
| 10 | Tree cover | green |
| 20 | Shrubland | green |
| 30 | Grassland | green |
| 40 | Cropland | paved ⚠️ |
| 50 | Built-up | paved |
| 60 | Bare / sparse vegetation | bare |
| 70 | Water bodies | water |
| 80 | Snow and ice | water |
| 90 | Herbaceous wetland | wetland |
| 95 | Mangroves | wetland |
| 100 | Moss and lichen | green |

Collapsing 11 → 5 is deliberate: the wet grid needs to know what **sheds water**
and what **holds it**, not what the crop is.

⚠️ **Cropland and built-up are not the same hydrologically.** Both map to
`paved`. HSK/HT is post-reclamation, so cropland there is mostly formed land
awaiting development — but this is a compromise and it is recorded in the output
metadata rather than hidden. In this window it barely bites: the raw tile carries
only **0.2 % cropland**, well under a hectare.

### The fused land class is not the raw ESA tile

`build_landclass.py` does not ship the ESA classification directly. It starts from
WorldCover and then applies three overrides, which is why the shipped grid reads
differently from the raw tile:

1. **Sentinel-2 NDVI/NDWI** (`pc_stac`, 2024-10-01 → 2026-09-30 window) pushes
   NDVI ≥ 0.35 to green and NDWI ≥ 0.20 (with NDVI < 0.1) to water. The 2021
   WorldCover epoch is already four years old in an area under active
   construction, so this is what keeps the green mask current.
2. **Tall non-building blobs** from the Open3Dhk tiles (`tree_mask.npz`, written
   by `build_buildings.py`) become green cover — the canopy the DSM sees but
   WorldCover's 10 m grid does not resolve.
3. **Real building footprints** (`buildings_hsk.json`) become `paved`.

Observed result on the 43.5 ha window: **85.4 % paved, 14.0 % green, 0.6 % bare,
0 % water, 0 % wetland** — 17,316 cells, of which 5,957 are under a building
footprint.

> **The zero water and zero wetland figure is faithful to the source, not a
> mapping bug.** A windowed read of the raw ESA tile over the same bbox returns
> 87.9 % built-up, 10.6 % tree, 1.3 % bare, 0.2 % cropland and no water class. The
> window is the dense built-up core of HSK/HT. That is an honest crop and it has a
> consequence worth stating: **there is almost no sponge left in this window to
> squeeze**, which limits what a pond-to-pavement comparison can show. A window
> containing the open ponds would tell the brief's story better.

### Caveats

- Single 2021 epoch for the ESA base — **no change since then**, which is why the
  Sentinel-2 overrides matter. The overrides are themselves a median over a
  two-year window, not a dated snapshot.
- 10 m source on a 5 m grid adds no detail below 10 m. The 5 m land class is
  really 10 m.
- ESA codes are modelled classifications, not ground truth.

---

## 3. Buildings — Overture fused with Open3Dhk tile blobs

- **Footprints:** Overture Maps `2026-09-23.0` (ODbL / CDLA), bulk release
- **Heights:** the Open3Dhk city tiles — a 1 m DSM (`dsm_hsk_tiles.npz`) minus the
  terrain tile surface, both from the same tile set
- **Builder:** `backend/scripts/build_buildings.py` (via `tiles_to_buildings.py`)
- **Outputs:** `data/rasters/buildings_hsk.json`, `buildings_meta.json`,
  `data/reference/tree_mask.npz`

The fusion rules are worth stating because they are where the layer's honesty
lives, and because they are the difference between "measured" and "invented":

| Rule | Threshold | Why |
|---|---|---|
| Keep an Overture footprint | p90 surface-minus-ground ≥ 3 m | below that the tiles do not agree a building stands there |
| Add a tile-only blob | ≥ 40 m² and p90 ≥ 7 m | below that it is mostly trees and sheds |
| Height | 90th percentile of surface − terrain inside the footprint | a published percentile rule, reproducible |

Result: **396 features** — 369 kept from Overture, 27 added from the tiles alone.
Heights 3.0–94.2 m, mean 10.9 m.

**The honest caveat.** This is **not surveyed storeys.** It is a 90th-percentile
DSM-minus-DTM height, which includes roof structures and equipment, and the DSM
reflects whatever LandsD draped over the mesh. `buildings_meta.json` records the
rule (`heightSource`) rather than leaving it to be inferred.

**What this replaced.** The procedural massing model — 2,639 axis-aligned square
"pillars" generated from the ESA paved class by `make_citymodel.py`. That
generator is deleted. `buildings3DUrl` renders the real textured 3D Tiles rather
than entity extrusions, and the two contracts are different on purpose:
`buildings_hsk.json` is the *analysis* geometry (heat, shade, sky-view, wind
shelter all read it) and the `.b3dm` meshes are the *render*.

---

## 4. Heat anchor — Landsat 8/9 surface temperature

- **Collection:** Landsat 8/9 Collection 2 Level-2, band `ST_B10`
- **Access:** Microsoft Planetary Computer STAC, public, anonymous SAS-signed hrefs
- **Fetcher:** `backend/scripts/fetch_lst.py`
- **Output:** `data/reference/lst_scenes.npz`, one 2-D array per scene in °C, NaN
  where the QA band flags cloud, shadow, cirrus or snow

Six candidate scenes were fetched over the bbox. **The shipped anchor is the
2022-09-03 scene** — the only capture that is 100 % finite and ~1 % cloud — and its
site mean is **41.26 °C**. It is resampled 30 m → 5 m (nearest neighbour) and
becomes the absolute level of the heat field; the geometry terms (land class, sky
view, shade, wind shelter) only move a cell *away from* that mean, which is why the
overlay is rendered as an anomaly map.

### Two things that must not be conflated

- **`LST_skin` is not `HKO_air`.** Air temperature is an *estimate* at surface
  − 12 °C (29.26 °C against the observed 41.26 °C), never a measurement.
- **The HKO Lau Fau Shan band (26.4–32.7 °C, July 2015–2024) is a sanity anchor,
  not an anchor.** The station is ~20 km away, so it can only catch a grossly wrong
  level; it never contributes to the field.

`HEAT_INDEX`'s land-class weights remain hand-authored apart from `green 0.64`,
which is calibrated from this LST scene. That is stated in
`heatmap_hsk.json`'s `anchoring.heatIndexSource` rather than left implicit.

---

## 5. Study-area extent — OSM anchors, provisional

- **Extent:** 113.9922–113.9994 °E, 22.431–22.4363 °N (≈43.5 ha), cropped to the
  Open3Dhk tile coverage on 13 Oct 2026. The original OSM-anchored extent was
  113.982–114.005 °E, 22.425–22.455 °N (≈786 ha); it is no longer analysed.
- **Derivation:** machine-retrieved OSM place anchors, recorded with queries and
  OSM ids in `backend/data/scene_hsk.json`
- **Status:** `PROVISIONAL_OPERATIONAL_EXTENT`

**This is not the gazetted NDA boundary.** It is a rectangular operational extent
chosen so the sandbox had a stable, citable rectangle on a fixed day.

> **Replace it with the sheet extent of OZP S/HSK/3.** Every raster,
> land-class grid and validation target shifts if it changes — which is exactly
> why it lives in a data file rather than being typed into a script.

### Why HSK/HT and not Deep Bay

The 27-task project plan anchors on Deep Bay (San Tin / Tai Sang Wai / Mai Po)
while the hackathon brief carries HSK/HT. This conflict was unresolved for
weeks. **HSK/HT was chosen on 6 Oct 2026** on the strength of its public
evidence base: approved EIA, OZP S/HSK/3, and a LegCo study.

---

## 6. Terrain rendering — mapbox terrain-RGB

Elevation has to reach Cesium somehow. Cesium's stock `CesiumTerrainProvider`
wants quantized-mesh from a tiling service; Cesium ion wants a token we
deliberately do not use.

So the DEM is encoded as **terrain-RGB** —
`metres = (R·65536 + G·256 + B)·0.1 − 32768` — a published open encoding, served
through Cesium's first-class `CustomHeightmapTerrainProvider`
(`frontend/src/view/demProvider.ts`). The window is 741 × 586 m, so a single image
beats a tile pyramid. The whole study area is **8.6 KB** as a PNG.

---

## Vertical datum — declared, not corrected

Hong Kong has two vertical datums: **mPD** (Principal Datum, ~1985) and **mCD**
(Chart Datum, mean sea level). They differ by **0.146 m**.

That figure is a named constant in `tests/test_constants.py`, with a test that
exists to be revisited when a tide series is added. **A tide state now exists** —
the run takes a Low / Mean / High toggle (HKO Quarry Bay, 0 / 1.4 / 2.2 mCD), and
those values are quoted in mCD against elevation that is in mPD. The 0.146 m
offset is therefore an *unresolved inconsistency* between the two, not a rounding
detail: it is half a 0.3 m LiDAR cell, small, but enough to flip a marginal cell
between wet and dry. It is not corrected, and the toggle is a screening factor
rather than a datum conversion. This is the cleanest remaining known-wrong number
in the project.

Related: elevation here is **orthometric (mPD)**, not ellipsoidal, and there is
**no bare-earth correction**. If vegetation or structures bias it upward, a
*deficit* estimate is biased in the same direction.

---

## Also tried, not adopted

| Source | Why not |
|---|---|
| **Lands Dept 5 m DTM** | ~290 MB, needs permission, **not bare-earth** |
| **ISRIC SoilGrids** (WCS) | Endpoint unreachable from this host (HTTP 000). Would have replaced the *assumed* hydrologic soil group with real soil data — the highest-value remaining upgrade |
| **OpenTopography** | Unreachable from this host (HTTP 000) |
| **HydroSHEDS** | Reachable. Drainage networks would enable real flow routing, but it is a large download and the study area is small enough that routing adds little at 30 m |
| **Overpass (OSM)** | Repeatedly timed out. Nominatim worked, so the extent was derived from place anchors instead |
| **AWS elevation-tiles-prod** | Returns plausible, non-terrarium pixels. See §1 |

---

## Refreshing the data

Order matters: the land class reads the buildings, and the overlays read the land
class.

```bash
cd backend
python scripts/tiles_to_rasters.py         # Open3Dhk tiles -> 5 m DTM + flow rasters
python scripts/tiles_to_buildings.py       # Open3Dhk city tiles -> 1 m DSM
python scripts/build_buildings.py          # + Overture -> buildings_hsk.json, tree_mask.npz
python scripts/build_landclass.py          # WorldCover + Sentinel-2 + trees + buildings
python scripts/fetch_lst.py                # Landsat LST scenes -> lst_scenes.npz
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
```

`fetch_landcover.py` and `fetch_dem.py` still exist for the standalone ESA base and
the Open-Meteo fallback, and are not part of the shipped chain.

Each script prints its own provenance, writes a `.json` manifest, and refuses to
write output that fails its sanity checks. Each registers its result against the
seeded scene, so the viewer's switches pick the new data up on reload.

**Two regeneration traps.** `make_wetgrid.py` writes the Priority-Flood
conditioning and the D8 receiver once; a live run re-solves the water level on the
conditioned surface but does not re-condition it, so **a DEM change needs
`make_wetgrid.py` re-run**, not just a new `dem_hsk.npy`. And the two raster md5
pins in `test_ledger` break whenever a raster is regenerated — by design, so the
change is deliberate rather than silent.

Each fetcher prints its own provenance, writes a `.json` manifest, and refuses
to write output that fails its sanity checks. Each registers its result against
the seeded scene, so the viewer's switches pick the new data up on reload.

To swap in a **better DEM** (a permitted 5 m DTM, or a bare-earth-corrected
surface), drop it in as `dem_hsk.npy` on the study-area bbox, north-up, float32
metres, and set `terrain_source` accordingly. Nothing else needs to change.
